import base64
import hashlib
import io
import json
import lzma
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

FORMAT_VERSION = 2
MANIFEST_VERSION = 1
PAYLOAD_CODEC = "lzma/xz"
CODEC = {"name": PAYLOAD_CODEC, "dictionary": None}

CHUNK_FILE_MIN_BYTES = 4096
CHUNK_MIN_BYTES = 2048
CHUNK_TARGET_BITS = 13  # ~8KiB average
CHUNK_MAX_BYTES = 16384
CHUNK_BOUNDARY_MASK = (1 << CHUNK_TARGET_BITS) - 1


def _encode_text_b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8", "surrogateescape")).decode("ascii")


def _stable_tarinfo(
    name: str,
    mode: int,
    size: int = 0,
    *,
    typeflag: bytes = tarfile.REGTYPE,
    linkname: str = "",
) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.mode = mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.type = typeflag
    info.size = size
    if typeflag == tarfile.SYMTYPE:
        info.linkname = linkname
    return info


def _build_tar_bundle(entries):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tf:
        for entry in entries:
            rel_path = entry["path"]
            kind = entry["kind"]
            if kind == "symlink":
                info = _stable_tarinfo(
                    rel_path,
                    0o777,
                    0,
                    typeflag=tarfile.SYMTYPE,
                    linkname=entry["target"],
                )
                tf.addfile(info)
                continue

            data = entry["data"]
            info = _stable_tarinfo(rel_path, entry["mode"], len(data))
            tf.addfile(info, io.BytesIO(data))
    return out.getvalue()


def _chunk_content_defined(data: bytes):
    if len(data) <= CHUNK_MIN_BYTES:
        return [data]

    chunks = []
    start = 0
    rolling = 0

    for idx, byte in enumerate(data):
        rolling = ((rolling << 5) ^ (rolling >> 2) ^ byte) & 0xFFFFFFFF
        cur_size = idx - start + 1
        if cur_size < CHUNK_MIN_BYTES:
            continue
        if (rolling & CHUNK_BOUNDARY_MASK) == 0 or cur_size >= CHUNK_MAX_BYTES:
            chunks.append(data[start : idx + 1])
            start = idx + 1
            rolling = 0

    if start < len(data):
        chunks.append(data[start:])

    return chunks if chunks else [data]


def _build_chunkstore_bundle(entries):
    blobs = {}
    manifest_entries = []

    for entry in entries:
        rel_path = entry["path"]
        path_b64 = _encode_text_b64(rel_path)
        kind = entry["kind"]

        if kind == "symlink":
            manifest_entries.append(
                {
                    "kind": "symlink",
                    "path_b64": path_b64,
                    "target_b64": _encode_text_b64(entry["target"]),
                }
            )
            continue

        mode = entry["mode"]
        data = entry["data"]
        if len(data) < CHUNK_FILE_MIN_BYTES:
            digest = hashlib.sha256(data).hexdigest()
            blobs.setdefault(digest, data)
            manifest_entries.append(
                {"kind": "file", "path_b64": path_b64, "mode": mode, "blob": digest}
            )
            continue

        chunks = _chunk_content_defined(data)
        chunk_ids = []
        for chunk in chunks:
            digest = hashlib.sha256(chunk).hexdigest()
            blobs.setdefault(digest, chunk)
            chunk_ids.append(digest)

        if len(chunk_ids) <= 1:
            manifest_entries.append(
                {"kind": "file", "path_b64": path_b64, "mode": mode, "blob": chunk_ids[0]}
            )
        else:
            manifest_entries.append(
                {
                    "kind": "chunked-file",
                    "path_b64": path_b64,
                    "mode": mode,
                    "chunks": chunk_ids,
                }
            )

    manifest = {
        "format_version": MANIFEST_VERSION,
        "codec": CODEC,
        "entries": manifest_entries,
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tf:
        tf.addfile(
            _stable_tarinfo("manifest.json", 0o644, len(manifest_bytes)),
            io.BytesIO(manifest_bytes),
        )
        for digest in sorted(blobs):
            blob = blobs[digest]
            tf.addfile(
                _stable_tarinfo(f"blobs/{digest}", 0o644, len(blob)),
                io.BytesIO(blob),
            )
    return out.getvalue()


def _xz_compress(data: bytes) -> bytes:
    return lzma.compress(data, preset=(9 | lzma.PRESET_EXTREME))


def _write_wrapped_b64(f, value: str) -> None:
    f.write(f"PAYLOAD_B64 = {value!r}\n\n")


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "Usage: gen_snapshot_cli.py <repo_root> <out_abs> <template_dir>",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(sys.argv[1]).resolve(strict=False)
    out_abs = Path(sys.argv[2]).resolve(strict=False)
    template_dir = Path(sys.argv[3]).resolve(strict=False)
    header_template = template_dir / "gen-full-header.py.tmpl"

    out_abs.parent.mkdir(parents=True, exist_ok=True)
    if not header_template.exists():
        raise FileNotFoundError(f"Missing template: {header_template}")

    out_skip = None
    try:
        out_skip = out_abs.relative_to(repo_root).as_posix()
    except ValueError:
        out_skip = None

    raw_paths = subprocess.check_output(
        [
            "git",
            "-c",
            "core.quotepath=off",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=repo_root,
    )
    paths = [p for p in raw_paths.decode("utf-8", "surrogateescape").split("\0") if p]
    paths.sort(key=lambda p: p.encode("utf-8", "surrogateescape"))

    entries = []
    file_count = 0
    link_count = 0

    for rel_path in paths:
        if out_skip and rel_path == out_skip:
            continue

        full_path = repo_root / rel_path

        if full_path.is_symlink():
            entries.append(
                {"kind": "symlink", "path": rel_path, "target": os.readlink(full_path)}
            )
            link_count += 1
            continue

        if not full_path.is_file():
            continue

        mode = stat.S_IMODE(full_path.stat().st_mode)
        entries.append(
            {
                "kind": "file",
                "path": rel_path,
                "mode": mode,
                "data": full_path.read_bytes(),
            }
        )
        file_count += 1

    bundle_tar = _build_tar_bundle(entries)
    chunkstore_tar = _build_chunkstore_bundle(entries)

    bundle_payload = _xz_compress(bundle_tar)
    chunkstore_payload = _xz_compress(chunkstore_tar)

    if len(chunkstore_payload) < len(bundle_payload):
        payload_kind = "chunkstore-tar-v1"
        payload = chunkstore_payload
    else:
        payload_kind = "bundle-tar-v1"
        payload = bundle_payload

    payload_b64 = base64.b64encode(payload).decode("ascii")

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f"{out_abs.name}.", suffix=".tmp", dir=str(out_abs.parent)
    )
    os.close(tmp_fd)

    header = header_template.read_text(encoding="utf-8")
    if not header.endswith("\n"):
        header += "\n"

    with open(tmp_name, "w", encoding="utf-8", newline="\n") as f:
        f.write(header)
        f.write(f"FORMAT_VERSION = {FORMAT_VERSION}\n")
        f.write(f"DEFAULT_RESTORE_DIR = {repo_root.name!r}\n")
        f.write(f"PAYLOAD_KIND = {payload_kind!r}\n")
        f.write(f"CODEC = {CODEC!r}\n")
        f.write(f"ENTRY_COUNT = {file_count + link_count}\n\n")
        _write_wrapped_b64(f, payload_b64)
        f.write("gen_full()\n")

    os.chmod(tmp_name, 0o755)
    tmp_path = Path(tmp_name)
    should_overwrite = True

    if out_abs.exists() and out_abs.is_file():
        should_overwrite = out_abs.read_bytes() != tmp_path.read_bytes()

    if should_overwrite:
        os.replace(tmp_name, out_abs)
        print(f"Snapshot script updated: {out_abs}")
    else:
        tmp_path.unlink()
        print(f"Snapshot script unchanged: {out_abs}")

    print(f"Snapshot entries: files={file_count}, symlinks={link_count}")
    print(
        "Snapshot payload: "
        f"kind={payload_kind}, "
        f"bundle_xz_bytes={len(bundle_payload)}, "
        f"chunkstore_xz_bytes={len(chunkstore_payload)}, "
        f"selected_base64_bytes={len(payload_b64)}"
    )
    if out_skip:
        print(f"Snapshot excluded output path: {out_skip}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
