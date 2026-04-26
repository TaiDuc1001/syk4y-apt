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


if __name__ == "__main__":
    unittest.main()
