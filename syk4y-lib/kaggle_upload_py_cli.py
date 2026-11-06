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


import hashlib
import uuid


def print_progress(current, total, prefix="Zipping", suffix="", bar_length=30):
    if total == 0:
        return
    percent = float(current) * 100 / total
    filled_length = int(round(bar_length * current / total))
    bar = "█" * filled_length + "-" * (bar_length - filled_length)
    sys.stderr.write(f"\r{prefix} |{bar}| {percent:.1f}% ({current}/{total}) {suffix}\033[K")
    sys.stderr.flush()


def _normalize_archive_relpath(rel_path: str) -> str:
    clean = rel_path.replace("\\", "/").strip()
    clean = clean.lstrip("/")
    parts = [p for p in clean.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError(f"Directory traversal ('..') detected in archive path: {rel_path!r}")
    if ":" in parts[0] and len(parts[0]) == 2:
        raise ValueError(f"Drive letter detected in archive path: {rel_path!r}")
    return "/".join(parts)


def compute_manifest_fingerprint(all_files: list, empty_dirs: set) -> str:
    all_files_sorted = sorted(all_files, key=lambda x: x[1])
    empty_dirs_sorted = sorted(d.rstrip("/") for d in empty_dirs)

    h = hashlib.sha256()
    h.update(b"V3_SAFE_ZIP_FP\0")
    for d in empty_dirs_sorted:
        h.update(f"D:{d}\0".encode())
    for src_path, rel, _, size in all_files_sorted:
        try:
            mtime_ns = Path(src_path).stat().st_mtime_ns
            h.update(f"F:{rel}:{size}:{mtime_ns}\0".encode())
        except OSError:
            h.update(f"F:{rel}:{size}\0".encode())
    return h.hexdigest()


def _safe_walk_artifact_dir(source_dir: Path, exclude_paths=None):
    source_dir = Path(source_dir).resolve()
    if not source_dir.is_dir():
        raise ValueError(f"Source directory does not exist or is not a directory: {source_dir}")

    exclude_realpaths = set()
    if exclude_paths:
        for p in exclude_paths:
            try:
                exclude_realpaths.add(Path(p).resolve())
            except OSError:
                pass

    visited_real_dirs = set()
    all_files = []  # list of (abs_path, norm_rel, ext_attr, size)
    populated_dirs = set()  # set of norm_rel dir paths that contain files
    empty_dirs = set()  # set of norm_rel dir paths with no files
    seen_rel_paths = set()

    def walk_dir(current_dir: Path, rel_prefix: str):
        try:
            st = current_dir.stat()
        except OSError as exc:
            raise OSError(f"Cannot stat directory '{current_dir}': {exc}")

        dir_key = (st.st_dev, st.st_ino)
        if dir_key in visited_real_dirs:
            # Prevent circular symlinks or duplicated traversal of same directory
            return None
        visited_real_dirs.add(dir_key)

        try:
            # Prefer real directories before symlinks to avoid recording alias paths
            entries = sorted(os.scandir(current_dir), key=lambda e: (e.is_symlink(), e.name))
        except OSError as exc:
            raise OSError(f"Cannot read directory '{current_dir}': {exc}")

        has_child_file = False

        for entry in entries:
            name = entry.name
            if name in {".syk4y-temp", ".upload.lock"} or name.startswith(".syk4y-"):
                continue

            entry_path = Path(entry.path)
            try:
                if entry_path.resolve() in exclude_realpaths:
                    continue
            except OSError:
                pass

            rel_path = f"{rel_prefix}/{name}" if rel_prefix else name
            norm_rel = _normalize_archive_relpath(rel_path)

            # Strict symlink policy (P0-D):
            # 1. Reject links pointing outside source directory
            # 2. Reject dangling symlinks
            # 3. Detect loops and aliasing
            if entry.is_symlink():
                try:
                    resolved_target = entry_path.resolve(strict=True)
                except FileNotFoundError:
                    target_dest = os.readlink(entry_path)
                    raise ValueError(f"Dangling symlink detected: '{entry_path}' points to non-existent target '{target_dest}'")
                except OSError as exc:
                    raise ValueError(f"Cannot resolve symlink '{entry_path}': {exc}")

                try:
                    resolved_target.relative_to(source_dir)
                except ValueError:
                    raise ValueError(
                        f"Symlink escape detected: '{entry_path}' points to '{resolved_target}', "
                        f"which is outside source directory '{source_dir}'"
                    )

            try:
                is_dir = entry.is_dir(follow_symlinks=True)
            except OSError as exc:
                raise OSError(f"Cannot determine directory status for '{entry_path}': {exc}")

            if is_dir:
                child_status = walk_dir(entry_path, norm_rel)
                if child_status is True:
                    has_child_file = True
                elif child_status is False:
                    empty_dirs.add(norm_rel)
            else:
                try:
                    fst = entry.stat(follow_symlinks=True)
                    ext_attr = (fst.st_mode & 0xFFFF) << 16
                    size = fst.st_size
                except OSError as exc:
                    raise OSError(f"Cannot stat file '{entry_path}': {exc}")

                if norm_rel in seen_rel_paths:
                    raise ValueError(f"Duplicate archive path detected: {norm_rel!r}")
                seen_rel_paths.add(norm_rel)

                all_files.append((entry_path, norm_rel, ext_attr, size))
                has_child_file = True

                # Mark all parent directories of this file as populated
                parent = norm_rel
                while "/" in parent:
                    parent = parent.rsplit("/", 1)[0]
                    populated_dirs.add(parent)

        return has_child_file

    walk_dir(source_dir, "")

    # Prune any empty dirs that turned out to be ancestors of populated dirs
    pure_empty_dirs = {d for d in empty_dirs if d not in populated_dirs}

    # Check for file/directory name collisions
    for _, norm_rel, _, _ in all_files:
        if norm_rel in populated_dirs or norm_rel in pure_empty_dirs:
            raise ValueError(f"File/directory collision: {norm_rel!r} is both a file and a directory prefix")

    return all_files, pure_empty_dirs


def validate_zip_archive(zip_path: Path, level: str = "full") -> dict:
    level = (level or "quick").strip().lower()
    if level not in ("quick", "full"):
        raise ValueError(f"Invalid validation level '{level}'. Must be 'quick' or 'full'.")

    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise ValueError(f"ZIP archive does not exist: {zip_path}")
    if not zipfile.is_zipfile(zip_path):
        raise ValueError(f"Corrupt or invalid ZIP archive: {zip_path}")

    seen_names = set()
    dir_entries = set()
    file_entries = set()
    populated_prefixes = set()

    with zipfile.ZipFile(zip_path, "r") as zf:
        if level == "full":
            bad_file = zf.testzip()
            if bad_file is not None:
                raise ValueError(f"ZIP CRC checksum failed on entry: {bad_file!r}")

        for info in zf.infolist():
            raw_name = info.filename

            if raw_name in seen_names:
                raise ValueError(f"Duplicate entry found in ZIP: {raw_name!r}")
            seen_names.add(raw_name)

            if raw_name.startswith("/") or raw_name.startswith("\\"):
                raise ValueError(f"Absolute path in ZIP entry: {raw_name!r}")
            if any(seg == ".." for seg in raw_name.replace("\\", "/").split("/")):
                raise ValueError(f"Directory traversal ('..') in ZIP entry: {raw_name!r}")

            norm_name = _normalize_archive_relpath(raw_name)

            if info.is_dir():
                clean_dir = norm_name.rstrip("/")
                dir_entries.add(clean_dir)
            else:
                file_entries.add(norm_name)
                parent = norm_name
                while "/" in parent:
                    parent = parent.rsplit("/", 1)[0]
                    populated_prefixes.add(parent)

        conflicts = dir_entries & file_entries
        if conflicts:
            raise ValueError(f"File/directory collision detected in archive: {sorted(conflicts)}")

        redundant_dirs = dir_entries & populated_prefixes
        if redundant_dirs:
            raise ValueError(
                f"Conflicting redundant directory entry declared for populated path: {sorted(redundant_dirs)}. "
                f"Populated directories must not have separate directory entries to prevent Kaggle ingestion collision."
            )

    return {
        "level": level,
        "total_entries": len(seen_names),
        "file_entries": len(file_entries),
        "dir_entries": len(dir_entries),
    }


def _compute_source_fingerprint(source_dir: Path, exclude_paths=None) -> str:
    all_files, empty_dirs = _safe_walk_artifact_dir(source_dir, exclude_paths=exclude_paths)
    return compute_manifest_fingerprint(all_files, empty_dirs)


def _safe_pack_zip(output_zip: Path, all_files, empty_dirs, zip_mode: str, source_dir: Path):
    output_zip = Path(output_zip).resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    mode = (zip_mode or "store").strip().lower()
    default_compression = zipfile.ZIP_STORED if mode == "store" else zipfile.ZIP_DEFLATED

    ALREADY_COMPRESSED_EXTS = {
        ".jpg", ".jpeg", ".png", ".webp", ".gif",
        ".mp4", ".avi", ".mkv", ".mov",
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".tgz",
        ".whl", ".pt", ".pth", ".bin", ".onnx", ".h5", ".safetensors",
    }

    all_files.sort(key=lambda x: x[1])
    empty_dirs_sorted = sorted(empty_dirs)

    pid_uuid = f"{os.getpid()}.{uuid.uuid4().hex[:8]}"
    temp_zip = output_zip.parent / f"{output_zip.name}.tmp.{pid_uuid}"
    temp_meta = output_zip.parent / f"{output_zip.name}.metadata.json.tmp.{pid_uuid}"

    total_files = len(all_files)
    written_files = 0
    total_bytes = sum(size for _, _, _, size in all_files)
    max_mtime_ns = 0

    print_progress(0, total_files, prefix="Writing zip", suffix="")

    try:
        with zipfile.ZipFile(temp_zip, "w", default_compression) as zf:
            # 1. Write pure empty directories (if any)
            for d in empty_dirs_sorted:
                d_name = d.rstrip("/") + "/"
                info = zipfile.ZipInfo(d_name)
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = 0o755 << 16
                zf.writestr(info, b"")

            # 2. Stream files
            for src_path, rel_name, ext_attr, size in all_files:
                ext = Path(rel_name).suffix.lower()
                if mode == "store" or ext in ALREADY_COMPRESSED_EXTS:
                    comp = zipfile.ZIP_STORED
                else:
                    comp = default_compression

                info = zipfile.ZipInfo(rel_name)
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = comp
                info.create_system = 3
                info.external_attr = ext_attr or (0o644 << 16)

                try:
                    with open(src_path, "rb") as fsrc:
                        with zf.open(info, "w", force_zip64=True) as fdst:
                            shutil.copyfileobj(fsrc, fdst, length=1024 * 1024)
                except OSError as exc:
                    raise OSError(f"Failed to read file '{src_path}' during packaging: {exc}")

                try:
                    st = src_path.stat()
                    if st.st_size != size:
                        raise OSError(f"File '{src_path}' was modified during packaging (size changed from {size} to {st.st_size})")
                    if st.st_mtime_ns > max_mtime_ns:
                        max_mtime_ns = st.st_mtime_ns
                except OSError as exc:
                    raise OSError(f"Cannot stat file '{src_path}' during packaging: {exc}")

                written_files += 1
                display_name = rel_name if len(rel_name) <= 30 else "..." + rel_name[-27:]
                print_progress(written_files, total_files, prefix="Writing zip", suffix=display_name)

        print_progress(total_files, total_files, prefix="Writing zip", suffix="Done!")
        sys.stderr.write("\n")
        sys.stderr.flush()

        # Self-validate temporary archive before atomic replacement (full validation)
        if temp_zip.is_file():
            validate_zip_archive(temp_zip, level="full")

        # Compute source fingerprint using unified function
        source_fingerprint = compute_manifest_fingerprint(all_files, empty_dirs)

        # Write metadata to temp_meta first
        meta_data = {
            "packer_version": 3,
            "zip_mode": mode,
            "file_count": total_files,
            "total_size": total_bytes,
            "max_mtime_ns": max_mtime_ns,
            "source_dir": str(source_dir),
            "fingerprint": source_fingerprint,
        }
        temp_meta.write_text(json.dumps(meta_data, indent=2) + "\n", encoding="utf-8")

        # Atomic commit: both archive and metadata
        os.replace(temp_zip, output_zip)
        os.replace(temp_meta, output_zip.with_name(f"{output_zip.name}.metadata.json"))

    finally:
        if temp_zip.exists():
            try:
                temp_zip.unlink()
            except OSError:
                pass
        if temp_meta.exists():
            try:
                temp_meta.unlink()
            except OSError:
                pass


def _walk_path_following_symlink_dirs(root: Path):
    """Compatibility generator for external callers."""
    all_files, empty_dirs = _safe_walk_artifact_dir(root)
    entries = []
    for d in empty_dirs:
        entries.append((root / d, d, "D"))
    for p, rel, _, _ in all_files:
        entries.append((p, rel, "F"))
    for item in sorted(entries, key=lambda x: x[1]):
        yield item


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

    # If querying an artifact hash, verify it is in confirmed READY state
    if key.endswith("_hash"):
        art_id = key[:-5]
        artifacts = data.get("artifacts", {})
        if isinstance(artifacts, dict) and art_id in artifacts:
            art = artifacts[art_id]
            if art.get("status") == "READY":
                val = art.get("source_fingerprint") or data.get(key, "")
                print(val if isinstance(val, str) else "")
                return 0
            else:
                # Not confirmed READY! Must not be treated as up to date!
                print("")
                return 0

    value = data.get(key, "")
    print(value if isinstance(value, str) else "")
    return 0


def cmd_get_artifact_state(state_file: str, artifact_id: str) -> int:
    state_path = Path(state_file)
    if not state_path.is_file():
        print(json.dumps({}))
        return 0

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        print(json.dumps({}))
        return 0

    artifacts = state.get("artifacts", {})
    if isinstance(artifacts, dict) and artifact_id in artifacts:
        print(json.dumps(artifacts[artifact_id]))
        return 0

    legacy_hash = state.get(f"{artifact_id}_hash")
    if legacy_hash:
        print(json.dumps({"status": "READY", "source_fingerprint": legacy_hash}))
        return 0

    print(json.dumps({}))
    return 0


def cmd_set_artifact_state(
    state_file: str,
    artifact_id: str,
    status: str,
    source_fp: str = "",
    archive_fp: str = "",
    dataset_ref: str = "",
    operation: str = "",
    remote_version: str = "",
) -> int:
    state_path = Path(state_file).resolve()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except Exception:
            state = {}

    state["version"] = 2
    artifacts = state.setdefault("artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
        state["artifacts"] = artifacts

    art_record = artifacts.get(artifact_id, {})
    if not isinstance(art_record, dict):
        art_record = {}

    import datetime
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    art_record["status"] = status
    art_record["updated_at"] = now_iso
    if source_fp:
        art_record["source_fingerprint"] = source_fp
    if archive_fp:
        art_record["archive_fingerprint"] = archive_fp
    if dataset_ref:
        art_record["dataset_ref"] = dataset_ref
    if operation:
        art_record["operation"] = operation
    if remote_version:
        art_record["remote_version"] = remote_version

    artifacts[artifact_id] = art_record

    # If confirmed READY, update legacy hash key for backward compatibility
    if status == "READY" and source_fp:
        state[f"{artifact_id}_hash"] = source_fp

    # Atomic write
    temp_state = state_path.parent / f"{state_path.name}.tmp.{os.getpid()}"
    temp_state.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(temp_state, state_path)
    return 0


def cmd_compute_source_fingerprint(source_path: str) -> int:
    p = Path(source_path).resolve()
    if not p.exists():
        print(f"Error: path '{source_path}' does not exist.", file=sys.stderr)
        return 1
    if p.is_dir():
        fp = _compute_source_fingerprint(p)
    else:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        fp = h.hexdigest()
    print(fp)
    return 0


def cmd_parse_kaggle_status(status_output: str, exit_code_str: str) -> int:
    try:
        exit_code = int(exit_code_str)
    except (ValueError, TypeError):
        exit_code = 1

    raw = status_output or ""
    # Redact any sensitive tokens/credentials
    redacted = re.sub(r'(?i)(key|token|secret|password|bearer)[=:\s]+[A-Za-z0-9_-]{8,}', r'\1=***REDACTED***', raw)
    text_lower = redacted.lower()

    # 1. Authentication / Authorization error (401 / 403 / credentials)
    if re.search(r'\b(401|403)\b', text_lower) or "unauthorized" in text_lower or "forbidden" in text_lower or "invalid credentials" in text_lower or "api key not found" in text_lower:
        print(f"AUTH_ERROR: {redacted.strip()}")
        return 30

    # 2. Permanent Failure check (before checking ready!)
    # Note: "already exists" contains substring "ready", so check error patterns FIRST!
    if "already exists" in text_lower or "directory already exists" in text_lower or \
       re.search(r'\b(failed|error|datasetfailed)\b', text_lower) or \
       re.search(r'dataset (creation|version creation) error', text_lower):
        print(f"FAILED: {redacted.strip()}")
        return 20

    # If exit code was nonzero:
    if exit_code != 0:
        if re.search(r'\b(timeout|timed out|connection reset|temporarily unavailable|500|502|503|504)\b', text_lower):
            print(f"TRANSIENT_ERROR: {redacted.strip()}")
            return 50
        print(f"FAILED: {redacted.strip()}")
        return 20

    # 3. Confirmed READY (exact word boundary \bready\b)
    if re.search(r'\bready\b', text_lower) and "already" not in text_lower:
        print("READY")
        return 0

    # 4. In-progress / Pending / Processing
    if re.search(r'\b(pending|processing|processed|queued|creating|uploading)\b', text_lower) or "being processed" in text_lower:
        print("PROCESSING")
        return 10

    # 5. Unknown response
    print(f"UNKNOWN: {redacted.strip()}")
    return 40


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
    print(clean(data.get("syk4y_artifact_id")))
    return 0


def cmd_write_state_file(state_tmp: str, state_tsv: str, existing_state_file: str = "") -> int:
    state_tmp_path = Path(state_tmp)
    state_tsv_path = Path(state_tsv)
    state = {}
    if existing_state_file and Path(existing_state_file).is_file():
        try:
            state = json.loads(Path(existing_state_file).read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except Exception:
            state = {}

    for line in state_tsv_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        key, value = line.split("\t", 1)
        state[key] = value
    state_tmp_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return 0


def cmd_validate_metadata(metadata_file: str) -> int:
    meta = Path(metadata_file)
    if not meta.exists():
        print(f"Error: metadata file '{metadata_file}' does not exist.", file=sys.stderr)
        return 1
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Error: malformed JSON in metadata file '{metadata_file}': {exc}", file=sys.stderr)
        return 2

    ref = data.get("id")
    if not isinstance(ref, str) or "/" not in ref:
        print(f"Error: metadata file '{metadata_file}' missing valid 'id' (format: owner/dataset-slug).", file=sys.stderr)
        return 3
    owner, slug = ref.split("/", 1)
    if not owner.strip() or not slug.strip():
        print(f"Error: metadata file '{metadata_file}' has empty owner or slug in 'id': {ref!r}.", file=sys.stderr)
        return 3
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

    try:
        all_files, empty_dirs = _safe_walk_artifact_dir(source, exclude_paths=[output])
    except Exception as exc:
        print(f"Error during wheelhouse scanning: {exc}", file=sys.stderr)
        return 1

    try:
        _safe_pack_zip(output, all_files, empty_dirs, zip_mode, source)
    except Exception as exc:
        print(f"Error: failed to create wheelhouse ZIP '{output_zip}': {exc}", file=sys.stderr)
        return 1

    return 0


def cmd_pack_artifact_dir_zip(source_dir: str, output_zip: str, zip_mode: str, force: bool = False) -> int:
    source = Path(source_dir).resolve()
    output = Path(output_zip).resolve()

    if not source.exists():
        print(f"Error: source directory '{source_dir}' does not exist.", file=sys.stderr)
        return 1

    try:
        all_files, empty_dirs = _safe_walk_artifact_dir(source, exclude_paths=[output])
    except Exception as exc:
        print(f"Error during directory scanning: {exc}", file=sys.stderr)
        return 1

    try:
        _safe_pack_zip(output, all_files, empty_dirs, zip_mode, source)
    except Exception as exc:
        print(f"Error: failed to create ZIP archive '{output_zip}': {exc}", file=sys.stderr)
        return 1

    return 0


def cmd_check_zip_cache(source_dir: str, output_zip: str) -> int:
    source = Path(source_dir).resolve()
    output = Path(output_zip).resolve()
    metadata_file = output.with_name(f"{output.name}.metadata.json")

    if not output.is_file():
        print("invalid")
        return 0

    try:
        validate_zip_archive(output, level="quick")
    except Exception:
        print("invalid")
        return 0

    if not metadata_file.is_file():
        print("changed")
        return 0

    try:
        meta = json.loads(metadata_file.read_text(encoding="utf-8"))
    except Exception:
        print("invalid")
        return 0

    if meta.get("packer_version") not in (2, 3):
        print("changed")
        return 0

    try:
        current_fp = _compute_source_fingerprint(source, exclude_paths=[output])
        if current_fp == meta.get("fingerprint"):
            print("valid")
            return 0
        else:
            print("changed")
            return 0
    except Exception:
        print("invalid")
        return 0


def cmd_validate_artifact_archive(zip_path: str, metadata_file: str = "", source_dir: str = "", level: str = "quick") -> int:
    p_zip = Path(zip_path)
    if not p_zip.is_file():
        print(f"ERROR: Dataset archive validation failed.\nReason: Archive file does not exist\nPath: {zip_path}", file=sys.stderr)
        return 1

    try:
        stats = validate_zip_archive(p_zip, level=level)
    except ValueError as exc:
        print(f"ERROR: Dataset archive validation failed.\nReason: {exc}\nPath: {zip_path}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: Dataset archive validation failed.\nReason: Corrupt or unreadable archive ({exc})\nPath: {zip_path}", file=sys.stderr)
        return 1

    # Validate metadata if provided
    if metadata_file:
        p_meta = Path(metadata_file)
        if not p_meta.is_file():
            print(f"ERROR: Dataset archive validation failed.\nReason: Metadata file does not exist\nPath: {metadata_file}", file=sys.stderr)
            return 1
        try:
            meta = json.loads(p_meta.read_text(encoding="utf-8"))
            dataset_id = meta.get("id", "")
            if not isinstance(dataset_id, str) or "/" not in dataset_id:
                print(f"ERROR: Dataset archive validation failed.\nReason: Invalid dataset id in metadata: {dataset_id!r}\nPath: {metadata_file}", file=sys.stderr)
                return 1
            owner, slug = dataset_id.split("/", 1)
            if not owner.strip() or not slug.strip():
                print(f"ERROR: Dataset archive validation failed.\nReason: Empty owner or slug in metadata 'id': {dataset_id!r}\nPath: {metadata_file}", file=sys.stderr)
                return 1
        except Exception as exc:
            print(f"ERROR: Dataset archive validation failed.\nReason: Malformed JSON in metadata: {exc}\nPath: {metadata_file}", file=sys.stderr)
            return 1

    # Validate cache freshness if source_dir provided
    if source_dir:
        p_source = Path(source_dir)
        if not p_source.exists():
            print(f"ERROR: Dataset archive validation failed.\nReason: Source directory does not exist\nPath: {source_dir}", file=sys.stderr)
            return 1
        meta_cache = p_zip.with_name(f"{p_zip.name}.metadata.json")
        if meta_cache.is_file():
            try:
                cached_data = json.loads(meta_cache.read_text(encoding="utf-8"))
                current_fp = _compute_source_fingerprint(p_source, exclude_paths=[p_zip])
                if current_fp != cached_data.get("fingerprint"):
                    print(f"ERROR: Dataset archive validation failed.\nReason: Archive cache is stale (source directory has changed since packaging)\nPath: {zip_path}", file=sys.stderr)
                    return 1
            except Exception:
                pass

    size_mb = p_zip.stat().st_size / (1024 * 1024)
    print(f"Archive structure valid: {stats['file_entries']} files, {stats['dir_entries']} empty dirs ({size_mb:.2f} MB)")
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


def cmd_prune_wheelhouse_build_dir(source_dir: str, requirements_file: str) -> int:
    source = Path(source_dir)
    req_path = Path(requirements_file)
    if not req_path.is_file():
        print(f"Error: requirements file {requirements_file} not found.", file=sys.stderr)
        return 1

    allowed_packages = {}
    allowed_wheel_filenames = set()

    for line in req_path.read_text(encoding="utf-8").splitlines():
        line = _strip_inline_comment(line.strip())
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if line.endswith(".whl"):
            allowed_wheel_filenames.add(Path(line).name)
            continue

        parts = line.split(";", 1)
        req_part = parts[0].strip()
        if "==" in req_part:
            pkg_name, pkg_ver = req_part.split("==", 1)
            pkg_name = _normalize_dist_name(pkg_name.strip())
            pkg_ver = pkg_ver.strip()
            allowed_packages.setdefault(pkg_name, set()).add(pkg_ver)
        else:
            pkg_name = _normalize_dist_name(req_part)
            allowed_packages.setdefault(pkg_name, set()).add("*")

    if not source.is_dir():
        return 0

    deleted_count = 0
    for path in source.glob("*.whl"):
        filename = path.name
        if filename in allowed_wheel_filenames:
            continue

        # Wheel filename format: {distribution}-{version}-...
        parts = filename.split("-")
        if len(parts) < 2:
            continue

        dist_name = _normalize_dist_name(parts[0])
        dist_ver = parts[1]

        if dist_name not in allowed_packages:
            print(f"Pruning unused wheel: {filename} (package not in requirements)", file=sys.stderr)
            path.unlink()
            deleted_count += 1
            continue

        versions = allowed_packages[dist_name]
        if "*" not in versions and dist_ver not in versions:
            print(f"Pruning old version wheel: {filename} (expected version in {versions})", file=sys.stderr)
            path.unlink()
            deleted_count += 1
            continue

    if deleted_count > 0:
        print(f"Pruned {deleted_count} old/unused wheel(s) from {source_dir}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="kaggle_upload_py_cli.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rsv = sub.add_parser("read-state-value")
    p_rsv.add_argument("state_file")
    p_rsv.add_argument("key")

    p_ras = sub.add_parser("read-artifact-settings")
    p_ras.add_argument("metadata_file")

    p_wsf = sub.add_parser("write-state-file")
    p_wsf.add_argument("state_tmp")
    p_wsf.add_argument("state_tsv")
    p_wsf.add_argument("existing_state_file", nargs="?", default="")

    p_val_meta = sub.add_parser("validate-metadata")
    p_val_meta.add_argument("metadata_file")

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
    p_paz.add_argument("--force", action="store_true")

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

    p_prune = sub.add_parser("prune-wheelhouse-build-dir")
    p_prune.add_argument("source_dir")
    p_prune.add_argument("requirements_file")

    p_czc = sub.add_parser("check-zip-cache")
    p_czc.add_argument("source_dir")
    p_czc.add_argument("output_zip")

    p_vaa = sub.add_parser("validate-artifact-archive")
    p_vaa.add_argument("zip_path")
    p_vaa.add_argument("metadata_file", nargs="?", default="")
    p_vaa.add_argument("source_dir", nargs="?", default="")
    p_vaa.add_argument("--level", choices=["quick", "full"], default="quick")

    p_csf = sub.add_parser("compute-source-fingerprint")
    p_csf.add_argument("source_path")

    p_pks = sub.add_parser("parse-kaggle-status")
    p_pks.add_argument("status_output")
    p_pks.add_argument("exit_code")

    p_ssa = sub.add_parser("set-artifact-state")
    p_ssa.add_argument("state_file")
    p_ssa.add_argument("artifact_id")
    p_ssa.add_argument("status")
    p_ssa.add_argument("--source-fp", default="")
    p_ssa.add_argument("--archive-fp", default="")
    p_ssa.add_argument("--dataset-ref", default="")
    p_ssa.add_argument("--operation", default="")
    p_ssa.add_argument("--remote-version", default="")

    p_gsa = sub.add_parser("get-artifact-state")
    p_gsa.add_argument("state_file")
    p_gsa.add_argument("artifact_id")

    args = parser.parse_args()

    if args.command == "check-zip-cache":
        return cmd_check_zip_cache(args.source_dir, args.output_zip)
    if args.command == "validate-artifact-archive":
        return cmd_validate_artifact_archive(args.zip_path, args.metadata_file, args.source_dir, level=args.level)
    if args.command == "compute-source-fingerprint":
        return cmd_compute_source_fingerprint(args.source_path)
    if args.command == "parse-kaggle-status":
        return cmd_parse_kaggle_status(args.status_output, args.exit_code)
    if args.command == "set-artifact-state":
        return cmd_set_artifact_state(
            args.state_file,
            args.artifact_id,
            args.status,
            source_fp=args.source_fp,
            archive_fp=args.archive_fp,
            dataset_ref=args.dataset_ref,
            operation=args.operation,
            remote_version=args.remote_version,
        )
    if args.command == "get-artifact-state":
        return cmd_get_artifact_state(args.state_file, args.artifact_id)

    if args.command == "read-state-value":
        return cmd_read_state_value(args.state_file, args.key)
    if args.command == "read-artifact-settings":
        return cmd_read_artifact_settings(args.metadata_file)
    if args.command == "write-state-file":
        return cmd_write_state_file(args.state_tmp, args.state_tsv, args.existing_state_file)
    if args.command == "validate-metadata":
        return cmd_validate_metadata(args.metadata_file)
    if args.command == "pyproject-extra-indexes":
        return cmd_pyproject_extra_indexes(args.pyproject_path)
    if args.command == "pack-wheelhouse-zip":
        return cmd_pack_wheelhouse_zip(args.source_dir, args.output_zip, args.zip_mode)
    if args.command == "pack-artifact-dir-zip":
        return cmd_pack_artifact_dir_zip(args.source_dir, args.output_zip, args.zip_mode, force=args.force)
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
    if args.command == "prune-wheelhouse-build-dir":
        return cmd_prune_wheelhouse_build_dir(args.source_dir, args.requirements_file)
    if args.command == "sanitize-wheelhouse-requirements":
        return cmd_sanitize_wheelhouse_requirements(
            args.input_path,
            args.output_path,
            args.repo_root,
        )

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
