#!/usr/bin/env bash
""":"
python_bin="python3"
if command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
elif command -v python >/dev/null 2>&1; then
  python_bin="python"
fi
exec "$python_bin" "$0" "$@"
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path


def sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_zip_integrity(zip_path: Path) -> tuple[bool, str, int]:
    if not zip_path.is_file():
        return False, "File does not exist", 0
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad_file = zf.testzip()
            if bad_file:
                return False, f"Corrupted file inside archive: {bad_file}", 0
            count = len(zf.namelist())
            return True, "Archive integrity verified", count
    except Exception as exc:
        return False, f"Invalid zip archive: {exc}", 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely inspect and migrate legacy nested kaggle_upload layouts."
    )
    parser.add_argument(
        "--repo-root",
        default="",
        help="Project repository root directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned migration steps without modifying files on disk.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite destination files if they already exist.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root or os.getcwd()).resolve()
    upload_root = repo_root / "kaggle_upload"
    legacy_dir = upload_root / "kaggle_upload"

    print("=" * 70)
    print(" syk4y Layout Migration Inspector")
    print("=" * 70)
    print(f"Project root : {repo_root}")
    print(f"Upload root  : {upload_root}")
    print(f"Legacy dir   : {legacy_dir}")
    print(f"Mode         : {'DRY RUN (no changes)' if args.dry_run else 'APPLY MIGRATION'}")
    print("=" * 70)

    if not legacy_dir.is_dir():
        print(f"\nNo legacy nested layout found at '{legacy_dir}'.")
        print("Your project layout is already clean.")
        return 0

    print("\n[!] Issue detected:")
    print(f"    Nested directory '{legacy_dir}' was found.")
    print("    Cause: Earlier versions of syk4y set project-root to kaggle_upload when")
    print("    executed from inside that folder, creating accidental nested directories.\n")

    # Discover items inside legacy_dir
    entries = sorted(legacy_dir.iterdir())
    if not entries:
        print("Legacy directory is empty. Nothing to migrate.")
        return 0

    actions = []
    artifacts_found = 0
    wheelhouses_found = 0

    for item in entries:
        if item.name.startswith(".migrated"):
            continue

        dest = upload_root / item.name
        metadata_file = item / "dataset-metadata.json" if item.is_dir() else None

        if metadata_file and metadata_file.is_file():
            artifacts_found += 1
            meta_valid = False
            dataset_id = ""
            try:
                meta_json = json.loads(metadata_file.read_text(encoding="utf-8"))
                dataset_id = meta_json.get("id", "")
                meta_valid = bool(dataset_id and "/" in dataset_id)
            except Exception:
                pass

            wh_file = item / "wheelhouse.zip"
            wh_info = ""
            if wh_file.is_file():
                wheelhouses_found += 1
                valid, msg, count = verify_zip_integrity(wh_file)
                wh_hash = sha256_file(wh_file)[:16]
                wh_info = f"wheelhouse.zip ({count} files, sha256:{wh_hash}..., integrity: {'OK' if valid else 'FAIL'})"

            actions.append({
                "type": "artifact",
                "src": item,
                "dest": dest,
                "dataset_id": dataset_id,
                "meta_valid": meta_valid,
                "wh_info": wh_info,
            })
        elif item.name == ".upload-state.json":
            actions.append({
                "type": "state",
                "src": item,
                "dest": dest,
            })
        else:
            actions.append({
                "type": "file" if item.is_file() else "dir",
                "src": item,
                "dest": dest,
            })

    print(f"Discovered {len(actions)} item(s) to migrate ({artifacts_found} artifact(s), {wheelhouses_found} wheelhouse(s)):")
    for act in actions:
        src_name = act["src"].name
        dest_exists = act["dest"].exists()
        extra = []
        if act.get("dataset_id"):
            extra.append(f"remote dataset: {act['dataset_id']}")
        if act.get("wh_info"):
            extra.append(act["wh_info"])
        if dest_exists:
            extra.append("WARNING: destination already exists")

        extra_str = f" [{', '.join(extra)}]" if extra else ""
        print(f"  - {src_name} -> {act['dest']}{extra_str}")

    if args.dry_run:
        print("\n" + "=" * 70)
        print("DRY RUN COMPLETE: No filesystem modifications were made.")
        print("To apply this migration safely, run:")
        print("  syk4y migrate-layout")
        print("=" * 70)
        return 0

    print("\nApplying safe migration...")
    upload_root.mkdir(parents=True, exist_ok=True)

    for act in actions:
        src = act["src"]
        dest = act["dest"]

        if act["type"] == "state":
            # Merge upload state
            try:
                src_state = json.loads(src.read_text(encoding="utf-8"))
            except Exception:
                src_state = {}

            dest_state = {}
            if dest.is_file():
                try:
                    dest_state = json.loads(dest.read_text(encoding="utf-8"))
                except Exception:
                    dest_state = {}

            merged = {**src_state, **dest_state}
            dest.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
            print(f"  [OK] Merged state file: {dest}")
            continue

        if dest.exists():
            if not args.force:
                print(f"  [SKIP] Destination exists and --force not specified: {dest}")
                continue
            print(f"  [OVERWRITE] Replacing destination with legacy item: {dest}")
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()

        # Move item to destination
        shutil.move(str(src), str(dest))
        print(f"  [OK] Migrated: {src.name} -> {dest}")

        # If it was an artifact with wheelhouse, re-verify
        wh_dest = dest / "wheelhouse.zip" if dest.is_dir() else None
        if wh_dest and wh_dest.is_file():
            valid, msg, count = verify_zip_integrity(wh_dest)
            if valid:
                print(f"       Preserved wheelhouse: {wh_dest} ({count} packages intact)")
            else:
                print(f"       Warning: {msg}")

    # Retain the legacy folder safely - NEVER silently delete
    timestamp = int(time.time())
    backup_name = f"kaggle_upload_legacy_{timestamp}"
    backup_path = upload_root / backup_name
    try:
        shutil.move(str(legacy_dir), str(backup_path))
        print(f"\n[OK] Retained legacy directory as backup: '{backup_path}'.")
        print("     No files were deleted.")
    except Exception as exc:
        print(f"\n[Notice] Legacy directory retained at '{legacy_dir}' ({exc}).")

    print("\n" + "=" * 70)
    print("Migration finished successfully!")
    print("Your artifacts and wheelhouse archives are now in the standard layout:")
    print(f"  {upload_root}")
    print("Verify with:")
    print("  syk4y doctor")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
