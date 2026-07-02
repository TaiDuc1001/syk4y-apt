import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TRANSFER_SH = REPO_ROOT / "syk4y-cli-lib" / "kaggle_upload_transfer.sh"


class KaggleUploadTransferTests(unittest.TestCase):
    def _run_upload(self, dataset_exists: int, kaggle_exit: int, kaggle_output: str = ""):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_file = tmp_path / "artifact.bin"
            metadata_file = tmp_path / "dataset-metadata.json"
            fake_kaggle = tmp_path / "fake-kaggle.sh"
            cmd_log = tmp_path / "kaggle-cmd.log"

            source_file.write_text("payload\n", encoding="utf-8")
            metadata_file.write_text('{"id":"owner/dataset"}\n', encoding="utf-8")
            fake_kaggle.write_text(
                "#!/usr/bin/env bash\n"
                "echo \"$*\" >> \"$CMD_LOG\"\n"
                "printf '%s\\n' \"$KAGGLE_OUTPUT\"\n"
                "exit \"$KAGGLE_EXIT\"\n",
                encoding="utf-8",
            )
            fake_kaggle.chmod(0o755)

            script = f"""
set -u -o pipefail
source "{TRANSFER_SH}"
clear_resume_markers() {{ :; }}
artifact_item_name() {{ printf '%s\\n' "artifact.bin"; }}
artifact_source_path() {{ printf '%s\\n' "$SOURCE_FILE"; }}
artifact_metadata_file() {{ printf '%s\\n' "$METADATA_FILE"; }}
KAGGLE_CMD=("$FAKE_KAGGLE")
VERSION_MESSAGE="test upload"
DIR_MODE="zip"
upload_single_artifact "datasets" "owner/dataset" "{dataset_exists}" "1" "0" ""
status="$?"
trap_output="$(trap -p RETURN || true)"
echo "STATUS=$status"
echo "TRAP=$trap_output"
"""

            proc = subprocess.run(
                ["bash", "-lc", script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "SOURCE_FILE": str(source_file),
                    "METADATA_FILE": str(metadata_file),
                    "FAKE_KAGGLE": str(fake_kaggle),
                    "CMD_LOG": str(cmd_log),
                    "KAGGLE_EXIT": str(kaggle_exit),
                    "KAGGLE_OUTPUT": kaggle_output,
                },
                text=True,
                capture_output=True,
                check=False,
            )
            cmd_text = cmd_log.read_text(encoding="utf-8") if cmd_log.exists() else ""
            return proc, cmd_text, source_file

    def test_upload_single_artifact_cleans_return_trap(self):
        proc, _cmd_text, source_file = self._run_upload(dataset_exists=1, kaggle_exit=0)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("STATUS=0", proc.stdout)
        self.assertIn("TRAP=", proc.stdout)
        self.assertIn(f"Uploaded artifact source path: {source_file}", proc.stdout)

    def test_upload_single_artifact_uses_version_for_existing_dataset(self):
        proc, cmd_text, _source_file = self._run_upload(dataset_exists=1, kaggle_exit=0)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("datasets version -p", cmd_text)
        self.assertNotIn("datasets create -p", cmd_text)

    def test_upload_single_artifact_uses_create_for_new_dataset(self):
        proc, cmd_text, _source_file = self._run_upload(dataset_exists=0, kaggle_exit=0)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("datasets create -p", cmd_text)
        self.assertNotIn("datasets version -p", cmd_text)

    def test_upload_single_artifact_returns_error_and_no_success_path_on_failure(self):
        proc, cmd_text, _source_file = self._run_upload(dataset_exists=1, kaggle_exit=7)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("STATUS=7", proc.stdout)
        self.assertIn("datasets version -p", cmd_text)
        self.assertNotIn("Uploaded artifact source path:", proc.stdout)

    def test_upload_single_artifact_rejects_cli_error_text_with_zero_exit(self):
        proc, cmd_text, _source_file = self._run_upload(
            dataset_exists=0,
            kaggle_exit=0,
            kaggle_output=(
                "Dataset creation error: "
                "Dataset url's dataset slugs and hashlink are all null"
            ),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("STATUS=1", proc.stdout)
        self.assertIn("datasets create -p", cmd_text)
        self.assertNotIn("Uploaded artifact source path:", proc.stdout)
        self.assertIn(
            "Kaggle CLI reported an upload failure despite returning exit code 0",
            proc.stderr,
        )

    def test_probe_kaggle_dataset_distinguishes_missing_from_other_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_kaggle = tmp_path / "fake-kaggle.sh"
            fake_kaggle.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$PROBE_OUTPUT\" >&2\n"
                "exit \"$PROBE_EXIT\"\n",
                encoding="utf-8",
            )
            fake_kaggle.chmod(0o755)

            script = f"""
set -u -o pipefail
source "{TRANSFER_SH}"
KAGGLE_CMD=("$FAKE_KAGGLE")
KAGGLE_UPLOAD_USERNAME="owner"
set +e
probe_kaggle_dataset "owner/dataset"
status="$?"
set -e
echo "STATUS=$status"
"""
            missing = subprocess.run(
                ["bash", "-lc", script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "FAKE_KAGGLE": str(fake_kaggle),
                    "PROBE_OUTPUT": "404 - Not Found",
                    "PROBE_EXIT": "1",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(missing.returncode, 0, missing.stderr)
            self.assertIn("STATUS=1", missing.stdout)
            self.assertNotIn("could not determine", missing.stderr)

            auth_error = subprocess.run(
                ["bash", "-lc", script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "FAKE_KAGGLE": str(fake_kaggle),
                    "PROBE_OUTPUT": "401 - Unauthorized",
                    "PROBE_EXIT": "1",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(auth_error.returncode, 0, auth_error.stderr)
            self.assertIn("STATUS=2", auth_error.stdout)
            self.assertIn("could not determine", auth_error.stderr)

            own_missing_403 = subprocess.run(
                ["bash", "-lc", script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "FAKE_KAGGLE": str(fake_kaggle),
                    "PROBE_OUTPUT": (
                        "403 Client Error: Forbidden for url: "
                        "https://api.kaggle.com/v1/datasets.DatasetApiService/"
                        "ListDatasetFiles"
                    ),
                    "PROBE_EXIT": "1",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(own_missing_403.returncode, 0, own_missing_403.stderr)
            self.assertIn("STATUS=1", own_missing_403.stdout)
            self.assertNotIn("could not determine", own_missing_403.stderr)

            other_owner_script = script.replace(
                'probe_kaggle_dataset "owner/dataset"',
                'probe_kaggle_dataset "someone-else/dataset"',
            )
            other_owner_403 = subprocess.run(
                ["bash", "-lc", other_owner_script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "FAKE_KAGGLE": str(fake_kaggle),
                    "PROBE_OUTPUT": "403 Client Error: Forbidden",
                    "PROBE_EXIT": "1",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(other_owner_403.returncode, 0, other_owner_403.stderr)
            self.assertIn("STATUS=2", other_owner_403.stdout)
            self.assertIn("could not determine", other_owner_403.stderr)

    def test_run_flow_returns_upload_failure_and_skips_state_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            source_file = repo / "artifact.bin"
            metadata_file = repo / "dataset-metadata.json"
            state_log = tmp_path / "state.log"
            source_file.write_text("payload\n", encoding="utf-8")
            metadata_file.write_text(
                '{"id":"your-kaggle-username/repo-datasets"}\n',
                encoding="utf-8",
            )

            script = f"""
set -euo pipefail
source "{TRANSFER_SH}"
SCRIPT_DIR="{REPO_ROOT}"
REPO_ROOT="$REPO_DIR"
UPLOAD_ROOT="$UPLOAD_ROOT_ENV"
PYTHON_BIN="python3"
BUILD_WHEEL_ONLY=0
STATE_FILE="$STATE_FILE_ENV"
FORCE_UPLOAD=0
VERSION_MESSAGE="test upload"
DIR_MODE="zip"
KAGGLE_CMD=("false")
declare -A CURRENT_FP
declare -A CURRENT_META_FP
syk4y_resolve_python_bin_or_die() {{ printf '%s\\n' "python3"; }}
resolve_wheelhouse_python() {{ printf '%s\\n' "python3"; }}
ensure_pip() {{ :; }}
ensure_kaggle_upload_prereqs() {{ :; }}
syk4y_resolve_kaggle_username() {{ printf '%s\\n' "ducphan1001"; }}
resolve_initialized_artifacts() {{ ARTIFACT_IDS=("datasets"); ALL_ARTIFACT_IDS=("datasets"); }}
verify_dataset_structure() {{ :; }}
artifact_source_path() {{ printf '%s\\n' "$SOURCE_FILE"; }}
artifact_metadata_file() {{ printf '%s\\n' "$METADATA_FILE"; }}
artifact_item_name() {{ printf '%s\\n' "artifact.bin"; }}
fingerprint_path() {{ printf '%s\\n' "fp"; }}
read_state_value() {{ printf '\\n'; }}
extract_dataset_ref() {{
  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" extract-dataset-ref "$1"
}}
probe_kaggle_dataset() {{ return 1; }}
remote_missing_expected_artifacts() {{ printf '\\n'; }}
write_state_file() {{ printf 'write-state\\n' >> "$STATE_LOG"; }}
upload_single_artifact() {{ return 7; }}
set +e
kaggle_upload_run_flow
status="$?"
set -e
echo "STATUS=$status"
cat "$METADATA_FILE"
if [[ -f "$STATE_LOG" ]]; then cat "$STATE_LOG"; fi
"""
            proc = subprocess.run(
                ["bash", "-lc", script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "REPO_DIR": str(repo),
                    "UPLOAD_ROOT_ENV": str(repo / "kaggle_upload"),
                    "SOURCE_FILE": str(source_file),
                    "METADATA_FILE": str(metadata_file),
                    "STATE_FILE_ENV": str(tmp_path / ".upload-state.json"),
                    "STATE_LOG": str(state_log),
                },
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("STATUS=7", proc.stdout)
            self.assertIn('"id": "ducphan1001/repo-datasets"', proc.stdout)
            self.assertNotIn("write-state", proc.stdout)

    def test_run_flow_build_wheel_only_writes_state_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            state_log = tmp_path / "state.log"
            wheelhouse_path = tmp_path / "wheelhouse.zip"
            wheelhouse_path.write_text("fake-zip\n", encoding="utf-8")

            script = f"""
set -euo pipefail
source "{TRANSFER_SH}"
SCRIPT_DIR="{REPO_ROOT}"
REPO_ROOT="$REPO_DIR"
UPLOAD_ROOT="$UPLOAD_ROOT_ENV"
PYTHON_BIN="python3"
BUILD_WHEEL_ONLY=1
STATE_FILE="$STATE_FILE_ENV"
WHEELHOUSE_INPUT_KEY="__wheelhouse_input__"
WHEELHOUSE_INPUT_HASH="test-wheelhouse-hash"
WHEELHOUSE_PATH="$WHEELHOUSE_PATH_ENV"
declare -A CURRENT_FP
declare -A CURRENT_META_FP
syk4y_resolve_python_bin_or_die() {{ printf '%s\\n' "python3"; }}
resolve_wheelhouse_python() {{ printf '%s\\n' "python3"; }}
ensure_pip() {{ :; }}
build_wheelhouse_if_needed() {{ :; }}
read_state_value() {{ printf '\\n'; }}
write_state_file() {{ printf 'write-state\\n' >> "$STATE_LOG"; }}
kaggle_upload_run_flow
status="$?"
echo "STATUS=$status"
"""
            proc = subprocess.run(
                ["bash", "-lc", script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "REPO_DIR": str(repo),
                    "UPLOAD_ROOT_ENV": str(repo / "kaggle_upload"),
                    "STATE_FILE_ENV": str(tmp_path / ".upload-state.json"),
                    "WHEELHOUSE_PATH_ENV": str(wheelhouse_path),
                    "STATE_LOG": str(state_log),
                },
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("STATUS=0", proc.stdout)
            self.assertTrue(state_log.exists())
            self.assertIn("write-state", state_log.read_text(encoding="utf-8"))

    def test_parallel_zip_reproduces_same_content(self):
        import sys
        import zipfile
        if str(REPO_ROOT / "syk4y-lib") not in sys.path:
            sys.path.insert(0, str(REPO_ROOT / "syk4y-lib"))
        import kaggle_upload_py_cli

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_dir = tmp_path / "source"
            source_dir.mkdir()
            (source_dir / "file1.txt").write_text("content1", encoding="utf-8")
            (source_dir / "file2.txt").write_text("content2" * 100, encoding="utf-8")
            (source_dir / "subdir").mkdir()
            (source_dir / "subdir" / "file3.txt").write_text("content3", encoding="utf-8")

            output_zip = tmp_path / "output.zip"
            status = kaggle_upload_py_cli.cmd_pack_artifact_dir_zip(str(source_dir), str(output_zip), "deflate")
            self.assertEqual(status, 0)
            self.assertTrue(output_zip.exists())

            # Read back and verify
            with zipfile.ZipFile(output_zip, "r") as zf:
                self.assertEqual(sorted(zf.namelist()), ["file1.txt", "file2.txt", "subdir/", "subdir/file3.txt"])
                self.assertEqual(zf.read("file1.txt"), b"content1")
                self.assertEqual(zf.read("subdir/file3.txt"), b"content3")

    def test_upload_single_artifact_uses_cached_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_file = tmp_path / "artifact"
            source_file.mkdir()
            (source_file / "file.txt").write_text("data\n", encoding="utf-8")

            metadata_file = tmp_path / "dataset-metadata.json"
            metadata_file.write_text('{"id":"owner/dataset"}\n', encoding="utf-8")

            fake_kaggle = tmp_path / "fake-kaggle.sh"
            cmd_log = tmp_path / "kaggle-cmd.log"
            fake_kaggle.write_text(
                "#!/usr/bin/env bash\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_kaggle.chmod(0o755)

            # Pre-create cached zip file
            cache_dir = tmp_path / ".syk4y-temp" / "kaggle-zip-cache"
            cache_dir.mkdir(parents=True)
            cached_zip = cache_dir / "datasets.zip"
            # Create a simple valid zip
            import zipfile
            with zipfile.ZipFile(cached_zip, "w") as zf:
                zf.writestr("file.txt", "cached_data\n")

            # Create metadata JSON file
            import json
            cached_meta = cache_dir / "datasets.zip.metadata.json"
            cached_meta.write_text(
                json.dumps({"fingerprint": "myfingerprint123", "files": {"file.txt": [0, 12]}}),
                encoding="utf-8"
            )

            script = f"""
set -u -o pipefail
source "{TRANSFER_SH}"
clear_resume_markers() {{ :; }}
artifact_item_name() {{ printf '%s\\n' "artifact.zip"; }}
artifact_source_path() {{ printf '%s\\n' "$SOURCE_FILE"; }}
artifact_metadata_file() {{ printf '%s\\n' "$METADATA_FILE"; }}
KAGGLE_CMD=("$FAKE_KAGGLE")
VERSION_MESSAGE="test upload"
DIR_MODE="zip"
REPO_ROOT="$REPO_DIR"
PYTHON_BIN="python3"
SCRIPT_DIR="{REPO_ROOT}"
ARTIFACT_ZIP_MODE="deflate"
upload_single_artifact "datasets" "owner/dataset" "1" "1" "0" "" "myfingerprint123"
status="$?"
echo "STATUS=$status"
"""

            proc = subprocess.run(
                ["bash", "-lc", script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "REPO_DIR": str(tmp_path),
                    "SOURCE_FILE": str(source_file),
                    "METADATA_FILE": str(metadata_file),
                    "FAKE_KAGGLE": str(fake_kaggle),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Using cached zip for 'datasets' (fingerprint: myfingerprint123)", proc.stdout)
            self.assertIn("STATUS=0", proc.stdout)

    def test_upload_single_artifact_fails_when_zip_cache_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_file = tmp_path / "artifact"
            source_file.mkdir()
            (source_file / "file.txt").write_text("data\n", encoding="utf-8")

            metadata_file = tmp_path / "dataset-metadata.json"
            metadata_file.write_text('{"id":"owner/dataset"}\n', encoding="utf-8")

            fake_kaggle = tmp_path / "fake-kaggle.sh"
            fake_kaggle.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_kaggle.chmod(0o755)

            script = f"""
set -u -o pipefail
source "{TRANSFER_SH}"
clear_resume_markers() {{ :; }}
artifact_item_name() {{ printf '%s\\n' "artifact.zip"; }}
artifact_source_path() {{ printf '%s\\n' "$SOURCE_FILE"; }}
artifact_metadata_file() {{ printf '%s\\n' "$METADATA_FILE"; }}
KAGGLE_CMD=("$FAKE_KAGGLE")
VERSION_MESSAGE="test upload"
DIR_MODE="zip"
REPO_ROOT="$REPO_DIR"
PYTHON_BIN="python3"
SCRIPT_DIR="{REPO_ROOT}"
ARTIFACT_ZIP_MODE="deflate"
upload_single_artifact "datasets" "owner/dataset" "1" "1" "0" "" "myfingerprint_missing"
status="$?"
echo "STATUS=$status"
"""

            proc = subprocess.run(
                ["bash", "-lc", script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "REPO_DIR": str(tmp_path),
                    "SOURCE_FILE": str(source_file),
                    "METADATA_FILE": str(metadata_file),
                    "FAKE_KAGGLE": str(fake_kaggle),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Error: ZIP file for artifact 'datasets' not found or stale in cache", proc.stderr)
            self.assertIn("Please run: syk4y kaggle zip", proc.stderr)
            self.assertIn("STATUS=1", proc.stdout)

    def test_zip_command_generates_cache_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            source_file = repo / "artifact"
            source_file.mkdir()
            (source_file / "file.txt").write_text("data\n", encoding="utf-8")

            # Setup metadata for resolution
            upload_root = repo / "kaggle_upload"
            upload_root.mkdir()
            dataset_dir = upload_root / "repo-slug-datasets"
            dataset_dir.mkdir()
            metadata_file = dataset_dir / "dataset-metadata.json"
            metadata_file.write_text('{"id":"owner/repo-slug-datasets"}\n', encoding="utf-8")

            zip_sh = REPO_ROOT / "syk4y-cli-lib" / "kaggle_zip.sh"

            script = f"""
set -euo pipefail
# Mock functions and source environment
SCRIPT_DIR="{REPO_ROOT}"
REPO_ROOT="$REPO_DIR"
KAGGLE_UPLOAD_ROOT="$UPLOAD_ROOT_ENV"
PYTHON_BIN="python3"
DIR_MODE="zip"
ARTIFACT_ZIP_MODE="deflate"
FORCE_UPLOAD=0
VERSION_MESSAGE="test zip"

# Source the transfer library to get helper methods
source "{TRANSFER_SH}"
source "{zip_sh}"

# Mock CLI/Prepare functions
syk4y_resolve_python_bin_or_die() {{ printf '%s\\n' "python3"; }}
ensure_kaggle_upload_prereqs() {{ :; }}
resolve_initialized_artifacts() {{
  ARTIFACT_IDS=("datasets")
  ALL_ARTIFACT_IDS=("datasets")
}}
artifact_source_path() {{ printf '%s\\n' "$SOURCE_FILE"; }}
artifact_metadata_file() {{ printf '%s\\n' "$METADATA_FILE"; }}
artifact_item_name() {{ printf '%s\\n' "artifact.zip"; }}
fingerprint_path() {{ printf '%s\\n' "myfingerprint_zip_test"; }}
syk4y_ensure_temp_dir_gitignore() {{ :; }}

kaggle_zip --repo-root "$REPO_DIR"
"""
            proc = subprocess.run(
                ["bash", "-lc", script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "REPO_DIR": str(repo),
                    "UPLOAD_ROOT_ENV": str(upload_root),
                    "SOURCE_FILE": str(source_file),
                    "METADATA_FILE": str(metadata_file),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Processing zip cache for 'datasets'...", proc.stdout)
            
            # Check zip was generated
            cached_zip = repo / ".syk4y-temp" / "kaggle-zip-cache" / "datasets.zip"
            self.assertTrue(cached_zip.exists())
            
            cached_meta = repo / ".syk4y-temp" / "kaggle-zip-cache" / "datasets.zip.metadata.json"
            self.assertTrue(cached_meta.exists())

            # Verify zip contents
            import zipfile
            with zipfile.ZipFile(cached_zip, "r") as zf:
                self.assertEqual(zf.read("file.txt"), b"data\n")


if __name__ == "__main__":
    unittest.main()
