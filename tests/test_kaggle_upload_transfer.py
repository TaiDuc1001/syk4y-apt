import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TRANSFER_SH = REPO_ROOT / "syk4y-cli-lib" / "kaggle_upload_transfer.sh"


class KaggleUploadTransferTests(unittest.TestCase):
    def _run_upload(self, dataset_exists: int, kaggle_exit: int):
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


if __name__ == "__main__":
    unittest.main()
