import argparse
import base64
import fnmatch
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
from pathlib import Path, PurePosixPath

FORMAT_VERSION = 2
MANIFEST_VERSION = 1
PAYLOAD_CODEC = "lzma/xz"
CODEC = {"name": PAYLOAD_CODEC, "dictionary": None}

CHUNK_FILE_MIN_BYTES = 4096
CHUNK_MIN_BYTES = 2048
CHUNK_TARGET_BITS = 13  # ~8KiB average
CHUNK_MAX_BYTES = 16384
CHUNK_BOUNDARY_MASK = (1 << CHUNK_TARGET_BITS) - 1

MAX_EMBED_FILE_BYTES = 1024 * 1024
MAX_SNAPSHOT_SCRIPT_BYTES = 1024 * 1024

ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_RESET = "\033[0m"

DEFAULT_SNAPSHOT_IGNORE_PATTERNS = [
    # VCS & tool internal directories
    ".git/",
    ".syk4y-temp/",
    "kaggle_upload/",
    ".hg/",
    ".svn/",
    # OS & editor artifacts
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "__MACOSX/",
    ".idea/",
    ".vscode/",
    "*.swp",
    "*.swo",
    "*~",
    # Python & runtime caches / virtualenvs
    "__pycache__/",
    "*.py[cod]",
    "*$py.class",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".ipynb_checkpoints/",
    ".coverage",
    ".coverage.*",
    "htmlcov/",
    ".tox/",
    ".nox/",
    ".venv/",
    "venv/",
    "env/",
    ".conda/",
    "build/",
    "dist/",
    "*.egg-info/",
    ".eggs/",
    # ML/DL training checkpoints, weights & models
    "*.pt",
    "*.pth",
    "*.bin",
    "*.onnx",
    "*.ckpt",
    "*.safetensors",
    "*.weights",
    "*.pb",
    "*.tflite",
    "*.h5",
    "*.hdf5",
    "*.pkl",
    "*.pickle",
    "*.joblib",
    "*.npy",
    "*.npz",
    # Tabular data & datasets (to be uploaded via syk4y kaggle upload instead)
    "*.csv",
    "*.tsv",
    "*.parquet",
    "*.feather",
    "*.arrow",
    "*.xlsx",
    "*.xls",
    # Database dumps & databases
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "*.rdb",
    # Media & binary assets (images, audio, video, documents)
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.bmp",
    "*.tiff",
    "*.webp",
    "*.ico",
    "*.mp4",
    "*.avi",
    "*.mov",
    "*.mkv",
    "*.wav",
    "*.mp3",
    "*.flac",
    "*.pdf",
    # Compressed archives
    "*.zip",
    "*.tar",
    "*.gz",
    "*.tgz",
    "*.bz2",
    "*.xz",
    "*.7z",
    "*.rar",
    # Logging, experiment tracking & run outputs
    "*.log",
    "*.tfevents*",
    "events.out.tfevents.*",
    "wandb/",
    ".wandb/",
    "runs/",
    "mlruns/",
    "lightning_logs/",
    "checkpoints/",
    "saved_models/",
    # Research experiment evidence / audit folders (e.g. research/.../evidence/)
    "evidence/",
    "**/evidence/",
    "**/evidence/**",
    # Documentation, planning, and design notes (not code needed for remote execution)
    "docs/",
    "doc/",
    "documentation/",
    ".planning/",
    "planning/",
    ".agent/",
    "*.md",
    "*.rst",
    "!README.md",
    "!README.rst",
    "!README.txt",
    # Package lockfiles (dependencies resolved by wheelhouse/pip)
    "*.lock",
    "package-lock.json",
    # Tabular line files / streaming dataset & run records
    "*.jsonl",
    "*.ndjson",
    # Experiment runs, trial catalogs & reports
    "experiments/",
    "reports/",
    "report/",
]


class IgnoreMatcher:
    def __init__(
        self,
        use_default_patterns: bool = True,
        syk4yignore_files: list[Path] | None = None,
        cli_excludes: list[str] | None = None,
    ):
        self.rules: list[tuple[bool, str]] = []
        if use_default_patterns:
            for pat in DEFAULT_SNAPSHOT_IGNORE_PATTERNS:
                self._add_rule(pat)

        if syk4yignore_files:
            for f in syk4yignore_files:
                if f.is_file():
                    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                        self._add_rule(line)

        if cli_excludes:
            for pat in cli_excludes:
                self._add_rule(pat)

    def _add_rule(self, line: str) -> None:
        line = line.strip()
        if not line or line.startswith("#"):
            return
        is_neg = line.startswith("!")
        if is_neg:
            line = line[1:].strip()
        self.rules.append((is_neg, line))

    def matches(self, rel_str: str) -> bool:
        p = PurePosixPath(rel_str)
        ignored = False
        parts = p.parts
        for is_neg, pat in self.rules:
            is_dir_pat = pat.endswith("/")
            pat_clean = pat.rstrip("/")
            hit = False
            if is_dir_pat:
                if any(part == pat_clean or fnmatch.fnmatch(part, pat_clean) for part in parts[:-1]):
                    hit = True
                elif p.match(f"**/{pat_clean}/**") or p.match(f"{pat_clean}/**"):
                    hit = True
            else:
                if "/" in pat:
                    hit = p.match(pat) or p.match(f"**/{pat}")
                else:
                    hit = p.match(pat) or fnmatch.fnmatch(p.name, pat)
            if hit:
                ignored = not is_neg
        return ignored


def _color(text: str, ansi_color: str) -> str:
    return f"{ansi_color}{text}{ANSI_RESET}"


def _format_bytes(size: int) -> str:
    return f"{size} bytes"


def _warn(message: str) -> None:
    print(_color(f"Warning: {message}", ANSI_YELLOW), file=sys.stderr)


def _error(message: str) -> None:
    print(_color(f"Error: {message}", ANSI_RED), file=sys.stderr)


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


def parse_bytes_limit(value: str | None, allow_large: bool) -> int:
    if allow_large and not value:
        return 10 * 1024 * 1024
    if not value:
        return MAX_SNAPSHOT_SCRIPT_BYTES
    v = value.strip().upper()
    multipliers = {
        "K": 1024,
        "KB": 1024,
        "M": 1024 * 1024,
        "MB": 1024 * 1024,
        "G": 1024 * 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
    }
    for suffix, mult in multipliers.items():
        if v.endswith(suffix):
            num = v[: -len(suffix)].strip()
            return int(float(num) * mult)
    return int(v)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate repository snapshot script.",
        add_help=True,
    )
    parser.add_argument("repo_root", type=Path, help="Target repository root")
    parser.add_argument("out_abs", type=Path, help="Output snapshot script path")
    parser.add_argument("template_dir", type=Path, help="Template directory")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Exclude file/dir pattern from snapshot (repeatable)",
    )
    parser.add_argument(
        "--no-default-ignore",
        action="store_true",
        help="Do not apply default snapshot ignore patterns",
    )
    parser.add_argument(
        "--max-size",
        type=str,
        default=None,
        help="Maximum allowed snapshot script size (e.g. 2M, 5MB; default: 1MB)",
    )
    parser.add_argument(
        "--allow-large-snapshot",
        action="store_true",
        help="Allow snapshot scripts larger than 1MB (sets limit to 10MB)",
    )
    return parser.parse_args(argv)


def main() -> int:
    try:
        args = parse_args(sys.argv[1:])
    except SystemExit as exc:
        return exc.code

    repo_root = args.repo_root.resolve(strict=False)
    out_abs = args.out_abs.resolve(strict=False)
    template_dir = args.template_dir.resolve(strict=False)
    header_template = template_dir / "gen-full-header.py.tmpl"

    out_abs.parent.mkdir(parents=True, exist_ok=True)
    if not header_template.exists():
        raise FileNotFoundError(f"Missing template: {header_template}")

    out_skip = None
    try:
        out_skip = out_abs.relative_to(repo_root).as_posix()
    except ValueError:
        out_skip = None

    ignore_files = [
        repo_root / ".syk4yignore",
        repo_root / ".syk4y" / "ignore",
    ]
    matcher = IgnoreMatcher(
        use_default_patterns=not args.no_default_ignore,
        syk4yignore_files=ignore_files,
        cli_excludes=args.exclude,
    )

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
    ignored_count = 0
    skipped_large_files = []

    for rel_path in paths:
        if out_skip and rel_path == out_skip:
            continue

        if matcher.matches(rel_path):
            ignored_count += 1
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

        st = full_path.stat()
        if st.st_size > MAX_EMBED_FILE_BYTES:
            skipped_large_files.append((rel_path, st.st_size))
            _warn(
                "skipping large snapshot file "
                f"{rel_path!r} ({_format_bytes(st.st_size)} > "
                f"{_format_bytes(MAX_EMBED_FILE_BYTES)})"
            )
            continue

        mode = stat.S_IMODE(st.st_mode)
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
    snapshot_script_size = tmp_path.stat().st_size
    max_script_size = parse_bytes_limit(args.max_size, args.allow_large_snapshot)
    if snapshot_script_size > max_script_size:
        tmp_path.unlink()
        _error(
            "generated snapshot script is too large: "
            f"{out_abs} is {_format_bytes(snapshot_script_size)} "
            f"> {_format_bytes(max_script_size)}. "
            "Use .syk4yignore or --exclude to omit extra directories/files, "
            "move large assets to Kaggle artifacts, or pass --allow-large-snapshot."
        )
        return 1

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
    if ignored_count:
        print(f"Snapshot auto-ignored files: {ignored_count}")
    if skipped_large_files:
        print(f"Snapshot skipped large files: {len(skipped_large_files)}")
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
