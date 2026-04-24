import argparse
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path


def cmd_fingerprint_path(target: str) -> int:
    import hashlib

    p = Path(target)
    if not p.exists():
        print("")
        return 0

    h = hashlib.sha256()
    if p.is_file():
        st = p.stat()
        h.update(b"F\0")
        h.update(str(st.st_size).encode())
        h.update(b"\0")
        h.update(str(st.st_mtime_ns).encode())
        h.update(b"\0")
    elif p.is_dir():
        h.update(b"D\0")
        for q in sorted(p.rglob("*")):
            rel = q.relative_to(p).as_posix()
            st = q.lstat()
            if q.is_symlink():
                typ = "L"
            elif q.is_dir():
                typ = "D"
            elif q.is_file():
                typ = "F"
            else:
                typ = "O"
            h.update(typ.encode())
            h.update(b"\0")
            h.update(rel.encode())
            h.update(b"\0")
            h.update(str(st.st_size).encode())
            h.update(b"\0")
            h.update(str(st.st_mtime_ns).encode())
            h.update(b"\0")
    else:
        st = p.stat()
        h.update(b"O\0")
        h.update(str(st.st_size).encode())
        h.update(b"\0")
        h.update(str(st.st_mtime_ns).encode())
        h.update(b"\0")

    print(h.hexdigest())
    return 0


def cmd_read_state_value(state_file: str, key: str) -> int:
    state_path = Path(state_file)
    if not state_path.exists():
        print("")
        return 0

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except BaseException:
        print("")
        return 0

    value = data.get(key, "")
    print(value if isinstance(value, str) else "")
    return 0


def cmd_read_artifact_settings(metadata_file: str) -> int:
    meta = Path(metadata_file)
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        print("")
        print("")
        return 0

    def clean(v):
        return v.strip() if isinstance(v, str) else ""

    print(clean(data.get("syk4y_source")))
    print(clean(data.get("syk4y_item_name")))
    return 0


def cmd_write_state_file(state_tmp: str, state_tsv: str) -> int:
    state_tmp_path = Path(state_tmp)
    state_tsv_path = Path(state_tsv)
    state = {}
    for line in state_tsv_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        key, value = line.split("\t", 1)
        state[key] = value
    state_tmp_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return 0


def cmd_pyproject_extra_indexes(pyproject_path: str) -> int:
    pyproject = Path(pyproject_path)
    if not pyproject.exists():
        return 0

    try:
        import tomllib
    except Exception:
        return 0

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    uv_tool = data.get("tool", {}).get("uv", {})
    indexes = uv_tool.get("index", [])

    seen = set()
    for item in indexes:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str):
            continue
        normalized = url.rstrip("/")
        if normalized in ("https://pypi.org/simple", "http://pypi.org/simple"):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        print(url)
    return 0


def cmd_pack_wheelhouse_zip(source_dir: str, output_zip: str, zip_mode: str) -> int:
    source = Path(source_dir)
    output = Path(output_zip)

    mode = (zip_mode or "store").strip().lower()
    compression = zipfile.ZIP_STORED if mode == "store" else zipfile.ZIP_DEFLATED

    with zipfile.ZipFile(output, mode="w", compression=compression) as zf:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(rel)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = compression
            info.create_system = 3
            info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
            zf.writestr(info, path.read_bytes())
    return 0


def cmd_extract_dataset_ref(metadata_file: str) -> int:
    meta = Path(metadata_file)
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except BaseException:
        print("")
        return 0
    ref = data.get("id", "")
    print(ref.strip() if isinstance(ref, str) else "")
    return 0


def cmd_kaggle_resume_dir() -> int:
    print(os.path.join(tempfile.gettempdir(), ".kaggle", "uploads"))
    return 0


def cmd_kaggle_resume_marker(path: str, resume_dir: str) -> int:
    abspath = os.path.abspath(path)
    key = abspath.replace(os.path.sep, "_").replace(":", "_")
    print(os.path.join(resume_dir, key + ".json"))
    return 0


def _normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _strip_inline_comment(line: str) -> str:
    # Preserve URL fragments (e.g. #sha256=...) but drop trailing comments.
    if " #" in line:
        return line.split(" #", 1)[0].rstrip()
    return line


def cmd_sanitize_wheelhouse_requirements(input_path: str, output_path: str) -> int:
    src = Path(input_path)
    dst = Path(output_path)

    try:
        from importlib import metadata as importlib_metadata
    except Exception:
        importlib_metadata = None

    installed_versions = {}
    if importlib_metadata is not None:
        try:
            for dist in importlib_metadata.distributions():
                name = dist.metadata.get("Name")
                version = dist.version
                if isinstance(name, str) and isinstance(version, str):
                    installed_versions[_normalize_dist_name(name)] = version
        except Exception:
            installed_versions = {}

    out_lines = []
    seen = set()
    for raw_line in src.read_text(encoding="utf-8").splitlines():
        line = _strip_inline_comment(raw_line.strip())
        if not line:
            continue
        if line.startswith("#") or line.startswith("-"):
            continue

        # Convert direct local file references (common with uv-managed pip/setuptools)
        # to portable name==version pins when the package is installed.
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*@\s*file://", line)
        if m is not None:
            pkg_name = m.group(1)
            normalized = _normalize_dist_name(pkg_name)
            version = installed_versions.get(normalized, "")
            if version:
                line = f"{pkg_name}=={version}"
                print(
                    f"Sanitized local file requirement: {pkg_name} @ file://... -> {line}",
                    file=os.sys.stderr,
                )
            else:
                print(
                    f"Skipping non-portable local file requirement: {raw_line}",
                    file=os.sys.stderr,
                )
                continue

        if line in seen:
            continue
        seen.add(line)
        out_lines.append(line)

    dst.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="kaggle_upload_py_cli.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fp = sub.add_parser("fingerprint-path")
    p_fp.add_argument("target")

    p_rsv = sub.add_parser("read-state-value")
    p_rsv.add_argument("state_file")
    p_rsv.add_argument("key")

    p_ras = sub.add_parser("read-artifact-settings")
    p_ras.add_argument("metadata_file")

    p_wsf = sub.add_parser("write-state-file")
    p_wsf.add_argument("state_tmp")
    p_wsf.add_argument("state_tsv")

    p_pei = sub.add_parser("pyproject-extra-indexes")
    p_pei.add_argument("pyproject_path")

    p_pwz = sub.add_parser("pack-wheelhouse-zip")
    p_pwz.add_argument("source_dir")
    p_pwz.add_argument("output_zip")
    p_pwz.add_argument("zip_mode")

    p_edr = sub.add_parser("extract-dataset-ref")
    p_edr.add_argument("metadata_file")

    sub.add_parser("kaggle-resume-dir")

    p_krm = sub.add_parser("kaggle-resume-marker")
    p_krm.add_argument("path")
    p_krm.add_argument("resume_dir")

    p_swr = sub.add_parser("sanitize-wheelhouse-requirements")
    p_swr.add_argument("input_path")
    p_swr.add_argument("output_path")

    args = parser.parse_args()

    if args.command == "fingerprint-path":
        return cmd_fingerprint_path(args.target)
    if args.command == "read-state-value":
        return cmd_read_state_value(args.state_file, args.key)
    if args.command == "read-artifact-settings":
        return cmd_read_artifact_settings(args.metadata_file)
    if args.command == "write-state-file":
        return cmd_write_state_file(args.state_tmp, args.state_tsv)
    if args.command == "pyproject-extra-indexes":
        return cmd_pyproject_extra_indexes(args.pyproject_path)
    if args.command == "pack-wheelhouse-zip":
        return cmd_pack_wheelhouse_zip(args.source_dir, args.output_zip, args.zip_mode)
    if args.command == "extract-dataset-ref":
        return cmd_extract_dataset_ref(args.metadata_file)
    if args.command == "kaggle-resume-dir":
        return cmd_kaggle_resume_dir()
    if args.command == "kaggle-resume-marker":
        return cmd_kaggle_resume_marker(args.path, args.resume_dir)
    if args.command == "sanitize-wheelhouse-requirements":
        return cmd_sanitize_wheelhouse_requirements(args.input_path, args.output_path)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
