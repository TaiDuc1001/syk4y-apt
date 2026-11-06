import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYK4Y_BIN = REPO_ROOT / "syk4y"
PY_CLI = REPO_ROOT / "syk4y-lib" / "kaggle_upload_py_cli.py"


class CiAndP0HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, cmd, cwd=None, env=None):
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else str(REPO_ROOT),
            env=run_env,
            capture_output=True,
            text=True,
        )

    def test_missing_uv_environment_graceful_fallback(self):
        """When uv is missing, doctor and python resolution fall back gracefully without crash."""
        # Strip uv from PATH
        path_dirs = [p for p in os.environ.get("PATH", "").split(os.pathsep) if not (Path(p) / "uv").is_file()]
        clean_path = os.pathsep.join(path_dirs)

        env = {"PATH": clean_path}
        p = self.run_cmd([str(SYK4Y_BIN), "doctor", "--json"], env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertIn("python_runtime", data)
        self.assertFalse(data["python_runtime"].get("uv", True))

    def test_missing_kaggle_cli_produces_actionable_error(self):
        """When Kaggle CLI is missing from the environment, provide actionable install guidance."""
        project = self.tmp_path / "NoKaggleProject"
        project.mkdir()
        (project / "dataset").mkdir()
        (project / "dataset" / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_py = bin_dir / "fake_python"
        fake_py.write_text(
            """#!/bin/sh
if [ "$1" = "-c" ] && echo "$2" | grep -q "kaggle"; then
  exit 1
fi
exec python3 "$@"
""",
            encoding="utf-8",
        )
        fake_py.chmod(0o755)

        path_dirs = [p for p in os.environ.get("PATH", "").split(os.pathsep) if not (Path(p) / "kaggle").is_file()]
        clean_path = os.pathsep.join(path_dirs)

        env = {
            "PATH": clean_path,
            "PYTHON_BIN": str(fake_py),
            "KAGGLE_BIN": "",
            "KAGGLE_USERNAME": "testuser",
            "KAGGLE_KEY": "testkey",
        }
        self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("kaggle cli is not available in the current environment", p.stderr.lower())
        self.assertTrue(
            "pip install" in p.stderr.lower() and "kaggle" in p.stderr.lower(),
            f"Expected actionable pip/uv pip install instructions in stderr: {p.stderr}"
        )

    def test_missing_docker_cleanup_is_safe(self):
        """Native cleanup handles temporary test workspace without requiring Docker."""
        from tests.test_integration_wheelhouse import safe_temp_dir, is_docker_available

        # Test that safe_temp_dir works cleanly
        with safe_temp_dir() as tmp_path:
            sample = tmp_path / "file.txt"
            sample.write_text("hello", encoding="utf-8")
            (tmp_path / "subdir").mkdir()
            (tmp_path / "subdir" / "nested.bin").write_bytes(b"\x00\x01")

        self.assertFalse(tmp_path.exists())

    def test_metadata_validation_works_completely_offline_without_kaggle(self):
        """Offline archive validation requires no Kaggle CLI or credentials."""
        project = self.tmp_path / "OfflineValidateProject"
        project.mkdir()
        upload_dir = project / "kaggle_upload" / "kaggle-upload-wheelhouse"
        upload_dir.mkdir(parents=True)

        meta = upload_dir / "dataset-metadata.json"
        meta.write_text(json.dumps({"id": "user/wheelhouse-dataset", "title": "wheelhouse"}), encoding="utf-8")

        zip_file = upload_dir / "wheelhouse.zip"
        with zipfile.ZipFile(zip_file, "w") as zf:
            zf.writestr("pkg-0.1-py3-none-any.whl", b"payload")

        # Run with no credentials and nonexistent Kaggle binary
        env = {
            "KAGGLE_BIN": "/nonexistent/kaggle",
            "KAGGLE_USERNAME": "",
            "KAGGLE_KEY": "",
        }
        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "validate", "wheelhouse", "--level", "full"], cwd=str(project), env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("VALID", p.stdout)

    def test_processing_followed_by_another_invocation_refuses_duplicate_upload(self):
        """When an upload is PROCESSING, a subsequent upload command preserves state and refuses duplicate upload."""
        project = self.tmp_path / "DupPreventProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        ds_dir = project / "dataset"
        ds_dir.mkdir()
        (ds_dir / "file.txt").write_text("hello", encoding="utf-8")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"
        call_log = self.tmp_path / "kaggle_calls.log"

        fake_kaggle.write_text(
            f"""#!/usr/bin/env bash
echo "$*" >> "{call_log}"
if [[ "$1" == "datasets" && "$2" == "status" ]]; then
  echo "Dataset status: pending"
  exit 0
fi
exit 0
""",
            encoding="utf-8",
        )
        fake_kaggle.chmod(0o755)

        env = {
            "KAGGLE_BIN": str(fake_kaggle),
            "KAGGLE_USERNAME": "mihtriii",
            "KAGGLE_KEY": "testkey",
            "KAGGLE_STATUS_POLL_ATTEMPTS": "1",
            "KAGGLE_STATUS_POLL_DELAY": "0.1",
        }

        # 1. First upload: accepted but pending remote processing
        self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        p1 = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p1.returncode, 0)
        self.assertIn("PROCESSING", p1.stdout)

        # Clear call log
        call_log.write_text("", encoding="utf-8")

        # 2. Second upload while remote is still PROCESSING
        p2 = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p2.returncode, 0)
        self.assertIn("was previously left in PROCESSING state. Reconciling", p2.stdout)
        self.assertIn("Refusing duplicate upload while remote ingestion is active", p2.stdout)

        # Must NOT have called datasets version or create
        calls = call_log.read_text(encoding="utf-8")
        self.assertNotIn("datasets version", calls)
        self.assertNotIn("datasets create", calls)

    def test_unknown_during_reconciliation_blocks_automatic_upload(self):
        """When remote status is UNKNOWN during reconciliation, block automatic re-upload."""
        project = self.tmp_path / "UnknownReconcileProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        ds_dir = project / "dataset"
        ds_dir.mkdir()
        (ds_dir / "file.txt").write_text("hello", encoding="utf-8")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"
        status_file = self.tmp_path / "status_mode.txt"
        status_file.write_text("pending", encoding="utf-8")

        fake_kaggle.write_text(
            f"""#!/usr/bin/env bash
if [[ "$1" == "datasets" && "$2" == "status" ]]; then
  mode=$(cat "{status_file}")
  if [[ "$mode" == "pending" ]]; then
    echo "Dataset status: pending"
    exit 0
  else
    echo "Temporary gateway timeout 504"
    exit 1
  fi
fi
exit 0
""",
            encoding="utf-8",
        )
        fake_kaggle.chmod(0o755)

        env = {
            "KAGGLE_BIN": str(fake_kaggle),
            "KAGGLE_USERNAME": "mihtriii",
            "KAGGLE_KEY": "testkey",
            "KAGGLE_STATUS_POLL_ATTEMPTS": "1",
            "KAGGLE_STATUS_POLL_DELAY": "0.1",
        }

        # 1. First upload -> PROCESSING
        self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        p1 = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p1.returncode, 0)

        # 2. Status fails with unknown/gateway error
        status_file.write_text("error", encoding="utf-8")
        p2 = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertNotEqual(p2.returncode, 0)
        self.assertIn("Blocking automatic upload", p2.stderr)

    def test_dataset_is_not_ready_parsed_as_processing_never_ready(self):
        """'Dataset is not ready' and 'not ready' must return PROCESSING (10), never READY (0)."""
        p1 = subprocess.run(
            ["python3", str(PY_CLI), "parse-kaggle-status", "Dataset is not ready", "0"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(p1.returncode, 10, f"Expected 10, got {p1.returncode}: {p1.stdout}")
        self.assertEqual(p1.stdout.strip(), "PROCESSING")

        p2 = subprocess.run(
            ["python3", str(PY_CLI), "parse-kaggle-status", "Status: not ready", "0"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(p2.returncode, 10)
        self.assertEqual(p2.stdout.strip(), "PROCESSING")

        p3 = subprocess.run(
            ["python3", str(PY_CLI), "parse-kaggle-status", "Dataset not yet ready", "0"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(p3.returncode, 10)
        self.assertEqual(p3.stdout.strip(), "PROCESSING")

    def test_structured_json_kaggle_status_parsing(self):
        """Documented Kaggle API JSON formats parse accurately."""
        # 1. JSON ready
        p1 = subprocess.run(
            ["python3", str(PY_CLI), "parse-kaggle-status", '{"status": "ready"}', "0"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(p1.returncode, 0)
        self.assertEqual(p1.stdout.strip(), "READY")

        # 2. JSON error
        p2 = subprocess.run(
            ["python3", str(PY_CLI), "parse-kaggle-status", '{"status": "error", "message": "Storage quota exceeded"}', "0"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(p2.returncode, 20)
        self.assertIn("FAILED", p2.stdout)

        # 3. Non-zero exit code cannot produce READY even if json says ready
        p3 = subprocess.run(
            ["python3", str(PY_CLI), "parse-kaggle-status", '{"status": "ready"}', "1"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(p3.returncode, 20)
        self.assertIn("FAILED", p3.stdout)

    def test_failed_upload_requires_explicit_force_decision(self):
        """When previous upload failed remotely on Kaggle, refuse re-upload without explicit --force."""
        project = self.tmp_path / "FailDecisionProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        ds_dir = project / "dataset"
        ds_dir.mkdir()
        (ds_dir / "file.txt").write_text("hello", encoding="utf-8")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"
        status_file = self.tmp_path / "status.txt"
        status_file.write_text("pending", encoding="utf-8")

        fake_kaggle.write_text(
            f"""#!/usr/bin/env bash
if [[ "$1" == "datasets" && "$2" == "status" ]]; then
  st=$(cat "{status_file}")
  if [[ "$st" == "pending" ]]; then
    echo "Dataset status: pending"
    exit 0
  elif [[ "$st" == "failed" ]]; then
    echo "Dataset status: failed"
    exit 1
  else
    echo "ready"
    exit 0
  fi
fi
exit 0
""",
            encoding="utf-8",
        )
        fake_kaggle.chmod(0o755)

        env = {
            "KAGGLE_BIN": str(fake_kaggle),
            "KAGGLE_USERNAME": "mihtriii",
            "KAGGLE_KEY": "testkey",
            "KAGGLE_STATUS_POLL_ATTEMPTS": "1",
            "KAGGLE_STATUS_POLL_DELAY": "0.1",
        }

        # 1. Initial upload -> pending
        self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)

        # 2. Server transitions to failed
        status_file.write_text("failed", encoding="utf-8")

        # 3. Next upload without --force must refuse
        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Use --force to explicitly trigger a fresh upload", p.stderr)

        # 4. With --force, upload proceeds
        status_file.write_text("ready", encoding="utf-8")
        p_force = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/", "--force"], cwd=str(project), env=env)
        self.assertEqual(p_force.returncode, 0)


if __name__ == "__main__":
    unittest.main()
