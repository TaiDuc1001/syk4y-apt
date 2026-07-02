import argparse
import binascii
import concurrent.futures
import csv
import io
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
import zlib
from importlib import metadata as importlib_metadata
from pathlib import Path
from urllib.parse import unquote, urlparse


def _parallel_pack_zip(output_zip: Path, files_to_compress, compression):
    class ParallelZipFile(zipfile.ZipFile):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pre_compressed_map = {}

        def set_pre_compressed(self, filename, compressed_data, uncompressed_size, crc):
            self._pre_compressed_map[filename] = (compressed_data, uncompressed_size, crc)

        def _open_to_write(self, zinfo, force_zip64=False):
            write_file = super()._open_to_write(zinfo, force_zip64=force_zip64)
            filename = zinfo.filename
            if filename in self._pre_compressed_map:
                comp_data, unc_size, crc = self._pre_compressed_map[filename]
                
                def custom_write(data):
                    if write_file.closed:
                        raise ValueError("I/O operation on closed file.")
                    if write_file._file_size == 0:
                        write_file._file_size = unc_size
                        write_file._crc = crc
                        write_file._compress_size = len(comp_data)
                        write_file._fileobj.write(comp_data)
                    return len(data)
                    
                def custom_close():
                    if write_file.closed:
                        return
                    try:
                        super(zipfile._ZipWriteFile, write_file).close()
                        write_file._zinfo.compress_size = write_file._compress_size
                        write_file._zinfo.CRC = write_file._crc
                        write_file._zinfo.file_size = write_file._file_size
                        
                        if write_file._zinfo.flag_bits & 0x08:
                            import struct
                            fmt = "<LLQQ" if write_file._zip64 else "<LLLL"
                            write_file._fileobj.write(struct.pack(fmt, zipfile._DD_SIGNATURE, write_file._zinfo.CRC,
                                write_file._zinfo.compress_size, write_file._zinfo.file_size))
                            write_file._zipfile.start_dir = write_file._fileobj.tell()
                        else:
                            write_file._zipfile.start_dir = write_file._fileobj.tell()
                            write_file._fileobj.seek(write_file._zinfo.header_offset)
                            write_file._fileobj.write(write_file._zinfo.FileHeader(write_file._zip64))
                            write_file._fileobj.seek(write_file._zipfile.start_dir)

                        write_file._zipfile.filelist.append(write_file._zinfo)
                        write_file._zipfile.NameToInfo[write_file._zinfo.filename] = write_file._zinfo
                    finally:
                        write_file._zipfile._writing = False
                        
                write_file.write = custom_write
                write_file.close = custom_close
                write_file._compressor = None
                
            return write_file

    def print_progress(current, total, prefix="Zipping", suffix="", bar_length=30):
        if total == 0:
            return
        percent = float(current) * 100 / total
        filled_length = int(round(bar_length * current / total))
        bar = "█" * filled_length + "-" * (bar_length - filled_length)
        sys.stderr.write(f"\r{prefix} |{bar}| {percent:.1f}% ({current}/{total}) {suffix}\033[K")
        sys.stderr.flush()

    MAX_PARALLEL_FILE_SIZE = 16 * 1024 * 1024  # 16MB
    
    parallel_jobs = []
    sequential_files = []
    
    for path, rel, ext_attr in files_to_compress:
        if path is None:
            sequential_files.append((None, rel, ext_attr))
            continue
        try:
            st = path.stat()
            size = st.st_size
        except OSError:
            continue
            
        if size < MAX_PARALLEL_FILE_SIZE and compression == zipfile.ZIP_DEFLATED:
            parallel_jobs.append((path, rel, ext_attr))
        else:
            sequential_files.append((path, rel, ext_attr))

    pre_compressed_map = {}
    
    if parallel_jobs:
        def compress_worker(job):
            p, r, _ = job
            try:
                data = p.read_bytes()
                crc = binascii.crc32(data) & 0xffffffff
                compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
                c_data = compressor.compress(data) + compressor.flush()
                return r, c_data, len(data), crc
            except Exception as e:
                print(f"Warning: failed to read/compress {p}: {e}", file=sys.stderr)
                return r, None, 0, 0

        max_workers = min(32, (os.cpu_count() or 4) * 2)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(compress_worker, job): job for job in parallel_jobs}
            total_jobs = len(parallel_jobs)
            completed_jobs = 0
            
            print_progress(0, total_jobs, prefix="Compressing", suffix="")
            
            for future in concurrent.futures.as_completed(futures):
                r, c_data, unc_size, crc = future.result()
                if c_data is not None:
                    pre_compressed_map[r] = (c_data, unc_size, crc)
                completed_jobs += 1
                filename = futures[future][1]
                if len(filename) > 30:
                    filename = "..." + filename[-27:]
                print_progress(completed_jobs, total_jobs, prefix="Compressing", suffix=filename)
            
            print_progress(total_jobs, total_jobs, prefix="Compressing", suffix="Done!")
            sys.stderr.write("\n")
            sys.stderr.flush()

    total_files = len(files_to_compress)
    written_files = 0
    
    print_progress(0, total_files, prefix="Writing zip", suffix="")

    with ParallelZipFile(output_zip, mode="w", compression=compression) as zf:
        for r, val in pre_compressed_map.items():
            zf.set_pre_compressed(r, val[0], val[1], val[2])
            
        for path, rel, ext_attr in files_to_compress:
            info = zipfile.ZipInfo(rel)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = compression
            info.create_system = 3
            info.external_attr = ext_attr
            
            filename = rel
            if len(filename) > 30:
                filename = "..." + filename[-27:]
            print_progress(written_files, total_files, prefix="Writing zip", suffix=filename)

            if rel in pre_compressed_map:
                zf.writestr(info, b"")
            else:
                if path is None:
                    zf.writestr(info, b"")
                else:
                    with path.open("rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
            
            written_files += 1
            
        print_progress(total_files, total_files, prefix="Writing zip", suffix="Done!")
        sys.stderr.write("\n")
        sys.stderr.flush()


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
        for q, rel, typ in _walk_path_following_symlink_dirs(p):
            st = q.stat() if typ in {"D", "F"} else q.lstat()
            h.update(typ.encode())
            h.update(b"\0")
            h.update(rel.encode())
            h.update(b"\0")
            h.update(str(st.st_size).encode())
            h.update(b"\0")
            h.update(str(st.st_mtime_ns).encode())
            h.update(b"\0")
            if q.is_symlink():
                h.update(os.readlink(q).encode())
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


def _walk_path_following_symlink_dirs(root: Path):
    seen_dirs = set()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        current = Path(dirpath)
        try:
            current_stat = current.stat()
        except OSError:
            dirnames[:] = []
            continue

        current_key = (current_stat.st_dev, current_stat.st_ino)
        if current_key in seen_dirs:
            dirnames[:] = []
            continue
        seen_dirs.add(current_key)

        entries = []
        for name in dirnames:
            path = current / name
            rel = path.relative_to(root).as_posix()
            entries.append((path, rel, "D"))
        for name in filenames:
            path = current / name
            rel = path.relative_to(root).as_posix()
            entries.append((path, rel, "F" if path.is_file() else "O"))

        for path, rel, typ in sorted(entries, key=lambda item: item[1]):
            yield path, rel, typ


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


def _clean_requirement_line(line: str) -> str:
    if not line.lower().endswith(".whl") and ".whl" not in line.lower():
        return line

    match = re.search(r"([^/\\@\s]+-([^-/\\]+)-[^-/\\]+-[^-/\\]+-[^-/\\]+\.whl)$", line, re.IGNORECASE)
    if not match:
        match = re.search(r"([^/\\@\s]+\.whl)$", line, re.IGNORECASE)
        if not match:
            return line
        filename = match.group(1)
        parts = filename.split("-")
        if len(parts) >= 2:
            pkg_name = parts[0].replace("_", "-")
            pkg_version = parts[1]
            return f"{pkg_name}=={pkg_version}"
        return line

    filename = match.group(1)
    pkg_version = match.group(2)
    pkg_name = filename.split("-", 1)[0].replace("_", "-")
    return f"{pkg_name}=={pkg_version}"


def cmd_pack_wheelhouse_zip(source_dir: str, output_zip: str, zip_mode: str) -> int:
    source = Path(source_dir)
    output = Path(output_zip)

    for req_filename in ["_requirements.txt", "_requirements_sanitized.txt"]:
        req_path = source / req_filename
        if req_path.is_file():
            try:
                content = req_path.read_text(encoding="utf-8")
                lines = content.splitlines()
                new_lines = []
                changed = False
                for line in lines:
                    cleaned = _clean_requirement_line(line)
                    if cleaned != line:
                        changed = True
                    new_lines.append(cleaned)
                if changed:
                    req_path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")
            except Exception as e:
                print(f"Warning: failed to sanitize {req_filename} in wheelhouse: {e}", file=sys.stderr)

    mode = (zip_mode or "store").strip().lower()
    compression = zipfile.ZIP_STORED if mode == "store" else zipfile.ZIP_DEFLATED

    files_to_compress = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source).as_posix()
        try:
            st = path.stat()
            ext_attr = (st.st_mode & 0xFFFF) << 16
        except OSError:
            continue
        files_to_compress.append((path, rel, ext_attr))

    _parallel_pack_zip(output, files_to_compress, compression)
    return 0


def cmd_pack_artifact_dir_zip(source_dir: str, output_zip: str, zip_mode: str) -> int:
    source = Path(source_dir)
    output = Path(output_zip)

    mode = (zip_mode or "store").strip().lower()
    compression = zipfile.ZIP_STORED if mode == "store" else zipfile.ZIP_DEFLATED

    files_to_compress = []
    for path, rel, typ in _walk_path_following_symlink_dirs(source):
        try:
            st = path.stat()
            ext_attr = (st.st_mode & 0xFFFF) << 16
        except OSError:
            continue
        if typ == "D":
            files_to_compress.append((None, rel.rstrip("/") + "/", ext_attr))
        elif typ == "F":
            files_to_compress.append((path, rel, ext_attr))

    _parallel_pack_zip(output, files_to_compress, compression)
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


def cmd_rewrite_dataset_owner(metadata_file: str, kaggle_username: str) -> int:
    username = (kaggle_username or "").strip()
    if not username:
        print("Error: Kaggle username is empty.", file=sys.stderr)
        return 2

    meta = Path(metadata_file)
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Error: could not read dataset metadata '{metadata_file}': {exc}", file=sys.stderr)
        return 1

    ref = data.get("id", "")
    if not isinstance(ref, str) or "/" not in ref:
        print(f"Error: '{metadata_file}' has invalid dataset id: {ref!r}", file=sys.stderr)
        return 1

    owner, slug = ref.split("/", 1)
    owner = owner.strip()
    slug = slug.strip()
    if not owner or not slug:
        print(f"Error: '{metadata_file}' has invalid dataset id: {ref!r}", file=sys.stderr)
        return 1

    new_ref = f"{username}/{slug}"
    if ref != new_ref:
        data["id"] = new_ref
        meta.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    print(new_ref)
    return 0


def cmd_kaggle_resume_dir() -> int:
    print(os.path.join(tempfile.gettempdir(), ".kaggle", "uploads"))
    return 0


def cmd_kaggle_resume_marker(path: str, resume_dir: str) -> int:
    abspath = os.path.abspath(path)
    key = abspath.replace(os.path.sep, "_").replace(":", "_")
    print(os.path.join(resume_dir, key + ".json"))
    return 0


def cmd_csv_first_column_contains(needle: str) -> int:
    data = sys.stdin.read()
    if not data.strip():
        print("0")
        return 0

    reader = csv.reader(io.StringIO(data))
    for row in reader:
        if not row:
            continue
        first_col = row[0].strip()
        if first_col.lower() == "name":
            continue
        if first_col == needle:
            print("1")
            return 0

    print("0")
    return 0


def _normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _strip_inline_comment(line: str) -> str:
    # Preserve URL fragments (e.g. #sha256=...) but drop trailing comments.
    if " #" in line:
        return line.split(" #", 1)[0].rstrip()
    return line


def _repo_local_wheel_requirement(line: str, repo_root: Path | None) -> str:
    if repo_root is None:
        return ""

    match = re.match(
        r"^(?:[A-Za-z0-9][A-Za-z0-9._-]*\s*@\s*)?(file://\S+)$",
        line,
    )
    if match is None:
        return ""

    parsed = urlparse(match.group(1))
    source_path = Path(unquote(parsed.path))
    filename = source_path.name
    if not filename.lower().endswith(".whl"):
        return ""

    candidates = []
    if source_path.is_file():
        candidates.append(source_path)
    candidates.extend(
        [
            repo_root / "wheels" / filename,
            repo_root / filename,
        ]
    )

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return candidate.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return str(candidate.resolve())
    return ""


def cmd_sanitize_wheelhouse_requirements(
    input_path: str,
    output_path: str,
    repo_root: str = "",
) -> int:
    src = Path(input_path)
    dst = Path(output_path)
    repo = Path(repo_root) if repo_root else None

    installed_versions = {}
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

        local_wheel = _repo_local_wheel_requirement(line, repo)
        if local_wheel:
            print(
                f"Mapped local wheel requirement: {raw_line} -> {local_wheel}",
                file=os.sys.stderr,
            )
            line = local_wheel

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

    p_paz = sub.add_parser("pack-artifact-dir-zip")
    p_paz.add_argument("source_dir")
    p_paz.add_argument("output_zip")
    p_paz.add_argument("zip_mode")

    p_edr = sub.add_parser("extract-dataset-ref")
    p_edr.add_argument("metadata_file")

    p_rdo = sub.add_parser("rewrite-dataset-owner")
    p_rdo.add_argument("metadata_file")
    p_rdo.add_argument("kaggle_username")

    sub.add_parser("kaggle-resume-dir")

    p_krm = sub.add_parser("kaggle-resume-marker")
    p_krm.add_argument("path")
    p_krm.add_argument("resume_dir")

    p_cfc = sub.add_parser("csv-first-column-contains")
    p_cfc.add_argument("needle")

    p_swr = sub.add_parser("sanitize-wheelhouse-requirements")
    p_swr.add_argument("input_path")
    p_swr.add_argument("output_path")
    p_swr.add_argument("repo_root", nargs="?", default="")

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
    if args.command == "pack-artifact-dir-zip":
        return cmd_pack_artifact_dir_zip(args.source_dir, args.output_zip, args.zip_mode)
    if args.command == "extract-dataset-ref":
        return cmd_extract_dataset_ref(args.metadata_file)
    if args.command == "rewrite-dataset-owner":
        return cmd_rewrite_dataset_owner(args.metadata_file, args.kaggle_username)
    if args.command == "kaggle-resume-dir":
        return cmd_kaggle_resume_dir()
    if args.command == "kaggle-resume-marker":
        return cmd_kaggle_resume_marker(args.path, args.resume_dir)
    if args.command == "csv-first-column-contains":
        return cmd_csv_first_column_contains(args.needle)
    if args.command == "sanitize-wheelhouse-requirements":
        return cmd_sanitize_wheelhouse_requirements(
            args.input_path,
            args.output_path,
            args.repo_root,
        )

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
