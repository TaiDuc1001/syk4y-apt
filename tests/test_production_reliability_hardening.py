import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYK4Y_BIN = REPO_ROOT / "syk4y"

sys.path.insert(0, str(REPO_ROOT / "syk4y-lib"))
import kaggle_upload_py_cli as py_cli  # noqa: E402


class ProductionReliabilityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="syk4y-p0p1-")
        self.tmp_path = Path(self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def run_cmd(self, args, cwd=None, env=None):
        run_env = {**os.environ, **(env or {})}
        return subprocess.run(
            args,
            cwd=cwd or str(self.tmp_path),
            env=run_env,
            capture_output=True,
            text=True,
        )

    def test_t01_already_exists_in_kaggle_error_is_failed_not_ready(self):
        """T01: 'Directory already exists: train' must be parsed as FAILED, never READY."""
        # 1. Python parser check
        err_msg = "Error during creation: Directory already exists: train"
        # Must return exit code 20 (FAILED) and print FAILED
        ret = py_cli.cmd_parse_kaggle_status(err_msg, "1")
        self.assertEqual(ret, 20)

        # Even if exit code passed was 0 (broken CLI reporting):
        ret_zero_exit = py_cli.cmd_parse_kaggle_status(err_msg, "0")
        self.assertEqual(ret_zero_exit, 20)

    def test_t02_kaggle_status_ready_is_confirmed_success(self):
        """T02: Kaggle status READY parses to confirmed success."""
        ret = py_cli.cmd_parse_kaggle_status("Dataset status: ready", "0")
        self.assertEqual(ret, 0)

        ret2 = py_cli.cmd_parse_kaggle_status("ready\n", "0")
        self.assertEqual(ret2, 0)

    def test_t03_kaggle_processing_remains_pending(self):
        """T03: Kaggle status pending/processing returns PROCESSING (code 10)."""
        ret = py_cli.cmd_parse_kaggle_status("Dataset status: pending", "0")
        self.assertEqual(ret, 10)

        ret2 = py_cli.cmd_parse_kaggle_status("Your dataset is being processed", "0")
        self.assertEqual(ret2, 10)

    def test_t04_kaggle_status_unavailable_returns_unknown(self):
        """T04: Kaggle status unavailable or unparseable returns UNKNOWN (code 40)."""
        ret = py_cli.cmd_parse_kaggle_status("Some random unparseable banner", "0")
        self.assertEqual(ret, 40)

    def test_t05_source_changed_after_zip_generation_rebuilds_and_does_not_skip(self):
        """T05: Modifying a source file after previous upload rebuilds archive and does NOT skip upload."""
        project = self.tmp_path / "SourceChangeProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        ds_dir = project / "dataset"
        ds_dir.mkdir()
        img = ds_dir / "image1.jpg"
        img.write_bytes(b"initial-image-data")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"
        upload_calls = self.tmp_path / "calls.log"

        fake_kaggle.write_text(
            f"""#!/usr/bin/env bash
echo "$*" >> "{upload_calls}"
if [[ "$1" == "datasets" && "$2" == "status" ]]; then
  echo "ready"
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

        # 1. Init and first upload
        p_init = self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p_init.returncode, 0)

        p_upload1 = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p_upload1.returncode, 0, p_upload1.stderr)
        self.assertIn("Done uploading artifact dataset", p_upload1.stdout)

        # 2. Modify source file
        img.write_bytes(b"modified-image-data-second-version")

        # 3. Second upload must NOT skip!
        upload_calls.write_text("", encoding="utf-8")
        p_upload2 = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p_upload2.returncode, 0, p_upload2.stderr)
        self.assertNotIn("Skipping re-upload", p_upload2.stdout)
        self.assertIn("Source changed: rebuilding", p_upload2.stdout)

        # Verify new archive was uploaded
        calls = upload_calls.read_text(encoding="utf-8")
        self.assertIn("datasets version -p", calls)

    def test_t06_source_unchanged_reuses_verified_cache(self):
        """T06: When source is unchanged, upload reuses verified cache and skips network transfer."""
        project = self.tmp_path / "UnchangedProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        ds_dir = project / "dataset"
        ds_dir.mkdir()
        (ds_dir / "image.jpg").write_bytes(b"data")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"

        fake_kaggle.write_text(
            """#!/usr/bin/env bash
if [[ "$1" == "datasets" && "$2" == "status" ]]; then
  echo "ready"
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

        # 1. Init and first upload
        self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        p1 = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p1.returncode, 0)

        # 2. Second upload without changes: must skip re-upload
        p2 = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p2.returncode, 0)
        self.assertIn("is up to date on remote dataset", p2.stdout)
        self.assertIn("Skipping re-upload", p2.stdout)

    def test_t07_empty_directories_have_stable_fingerprint(self):
        """T07: Pure empty directories produce stable fingerprints matching cache metadata."""
        src = self.tmp_path / "empty_dirs_src"
        (src / "empty1").mkdir(parents=True)
        (src / "empty2" / "sub_empty").mkdir(parents=True)
        (src / "populated" / "file.txt").parent.mkdir(parents=True)
        (src / "populated" / "file.txt").write_text("content", encoding="utf-8")

        fp1 = py_cli._compute_source_fingerprint(src)
        zip_out = self.tmp_path / "out.zip"
        ret = py_cli.cmd_pack_artifact_dir_zip(str(src), str(zip_out), "deflated")
        self.assertEqual(ret, 0)

        meta = json.loads(zip_out.with_name(f"{zip_out.name}.metadata.json").read_text(encoding="utf-8"))
        fp2 = meta["fingerprint"]

        # Unified fingerprint check: fp1 must equal fp2 exactly!
        self.assertEqual(fp1, fp2)

        # Repeated calculation remains identical
        fp3 = py_cli._compute_source_fingerprint(src)
        self.assertEqual(fp1, fp3)

    def test_t08_symlink_escapes_source_root_rejected(self):
        """T08: Symlink escaping source root directory is rejected with explicit error."""
        src = self.tmp_path / "escape_src"
        src.mkdir()
        (src / "normal.txt").write_text("safe", encoding="utf-8")

        secret = self.tmp_path / "secret.env"
        secret.write_text("TOP_SECRET=true", encoding="utf-8")

        # Create escaping symlink
        bad_link = src / "leak.env"
        bad_link.symlink_to(secret)

        zip_out = self.tmp_path / "escape.zip"
        with self.assertRaises(ValueError) as ctx:
            py_cli._safe_walk_artifact_dir(src)
        self.assertIn("Symlink escape detected", str(ctx.exception))

    def test_t09_unreadable_source_file_fails_without_replacing_cache(self):
        """T09: Unreadable source file fails fast and does not replace valid cache."""
        src = self.tmp_path / "unreadable_src"
        src.mkdir()
        valid_file = src / "ok.txt"
        valid_file.write_text("valid content", encoding="utf-8")

        zip_out = self.tmp_path / "cached.zip"
        # Build first valid archive
        ret1 = py_cli.cmd_pack_artifact_dir_zip(str(src), str(zip_out), "deflated")
        self.assertEqual(ret1, 0)
        self.assertTrue(zip_out.is_file())

        # Add unreadable file (chmod 000)
        bad_file = src / "no_read.txt"
        bad_file.write_text("unreadable", encoding="utf-8")
        bad_file.chmod(0o000)

        try:
            ret2 = py_cli.cmd_pack_artifact_dir_zip(str(src), str(zip_out), "deflated", force=True)
            self.assertEqual(ret2, 1, "Must fail with exit code 1 on unreadable file")
        finally:
            bad_file.chmod(0o644)

        # Cached zip must be preserved intact with original content
        with zipfile.ZipFile(zip_out, "r") as zf:
            self.assertEqual(zf.read("ok.txt"), b"valid content")
            self.assertNotIn("no_read.txt", zf.namelist())

    def test_t10_upload_succeeds_but_processing_fails_does_not_mark_synced(self):
        """T10: If upload network command succeeds but Kaggle remote processing fails, do not mark synced."""
        project = self.tmp_path / "ProcessingFailProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        ds_dir = project / "dataset"
        ds_dir.mkdir()
        (ds_dir / "file.txt").write_text("data", encoding="utf-8")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"

        fake_kaggle.write_text(
            """#!/usr/bin/env bash
if [[ "$1" == "datasets" && "$2" == "status" ]]; then
  echo "Dataset status: failed"
  exit 1
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

        self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertNotEqual(p.returncode, 0)

        # Check state: must NOT be marked READY
        state_file = project / "kaggle_upload" / ".upload-state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        art_state = state.get("artifacts", {}).get("dataset", {})
        self.assertNotEqual(art_state.get("status"), "READY")
        self.assertEqual(art_state.get("status"), "PROCESSING_FAILED")

    def test_t11_polling_times_out_persists_pending_state(self):
        """T11: When polling times out while Kaggle is PROCESSING, state remains PROCESSING and not synced."""
        project = self.tmp_path / "TimeoutProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        ds_dir = project / "dataset"
        ds_dir.mkdir()
        (ds_dir / "file.txt").write_text("data", encoding="utf-8")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"

        fake_kaggle.write_text(
            """#!/usr/bin/env bash
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
            "KAGGLE_STATUS_POLL_ATTEMPTS": "2",
            "KAGGLE_STATUS_POLL_DELAY": "0.1",
        }

        self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p.returncode, 0)
        self.assertIn("Remote ingestion is currently PROCESSING", p.stdout)

        # State must be recorded as PROCESSING
        state_file = project / "kaggle_upload" / ".upload-state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        art_state = state.get("artifacts", {}).get("dataset", {})
        self.assertEqual(art_state.get("status"), "PROCESSING")

    def test_t12_restart_after_pending_upload_reconciles_before_retry(self):
        """T12: Restarting after pending upload reconciles with Kaggle and skips redundant transfer if READY."""
        project = self.tmp_path / "ReconcileProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        ds_dir = project / "dataset"
        ds_dir.mkdir()
        (ds_dir / "file.txt").write_text("data", encoding="utf-8")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"
        call_log = self.tmp_path / "kaggle_calls.log"

        # Initially returns pending, then on second run returns ready
        status_flag = self.tmp_path / "status.txt"
        status_flag.write_text("pending", encoding="utf-8")

        fake_kaggle.write_text(
            f"""#!/usr/bin/env bash
echo "$*" >> "{call_log}"
if [[ "$1" == "datasets" && "$2" == "status" ]]; then
  cat "{status_flag}"
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

        # 1. Run upload -> times out in PROCESSING
        self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        p1 = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p1.returncode, 0)
        self.assertIn("PROCESSING", p1.stdout)

        # 2. Remote dataset finishes processing on Kaggle
        status_flag.write_text("Dataset status: ready\n", encoding="utf-8")
        call_log.write_text("", encoding="utf-8")

        # 3. Next upload invocation reconciles without uploading again!
        p2 = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p2.returncode, 0)
        self.assertIn("was previously left in PROCESSING state. Reconciling", p2.stdout)
        self.assertIn("Remote dataset is now confirmed READY", p2.stdout)

        # Must NOT have called datasets version or create
        calls = call_log.read_text(encoding="utf-8")
        self.assertNotIn("datasets version -p", calls)
        self.assertNotIn("datasets create -p", calls)

    def test_t13_interrupted_cache_update_previous_valid_cache_recoverable(self):
        """T13: Interrupted cache update preserves previous valid archive."""
        src = self.tmp_path / "atomic_cache_src"
        src.mkdir()
        (src / "data.txt").write_text("v1 content", encoding="utf-8")

        zip_out = self.tmp_path / "cached.zip"
        ret1 = py_cli.cmd_pack_artifact_dir_zip(str(src), str(zip_out), "deflated")
        self.assertEqual(ret1, 0)

        # Simulate interruption during pack of v2
        import shutil
        original_copy = shutil.copyfileobj

        def broken_copy(*args, **kwargs):
            raise IOError("Simulated power loss during write")

        (src / "data.txt").write_text("v2 content", encoding="utf-8")
        shutil.copyfileobj = broken_copy
        try:
            ret2 = py_cli.cmd_pack_artifact_dir_zip(str(src), str(zip_out), "deflated", force=True)
            self.assertEqual(ret2, 1)
        finally:
            shutil.copyfileobj = original_copy

        # Previous archive must remain valid with v1 content
        with zipfile.ZipFile(zip_out, "r") as zf:
            self.assertEqual(zf.read("data.txt"), b"v1 content")

    def test_t14_concurrent_upload_attempts_prevented(self):
        """T14: Concurrent upload attempts abort with concurrency lock."""
        project = self.tmp_path / "LockProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        upload_dir = project / "kaggle_upload"
        upload_dir.mkdir()
        lock_dir = upload_dir / ".upload.lock"
        lock_dir.mkdir()
        (lock_dir / "pid").write_text(str(os.getpid()), encoding="utf-8")

        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload"], cwd=str(project), env={"KAGGLE_USERNAME": "usr", "KAGGLE_KEY": "key"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("concurrent syk4y upload in progress", p.stderr.lower())

    def test_t15_existing_wheelhouse_no_unnecessary_rebuild(self):
        """T15: Existing wheelhouse archive is preserved without unnecessary rebuild."""
        project = self.tmp_path / "WheelhouseProject"
        project.mkdir()
        upload_dir = project / "kaggle_upload" / "kaggle-upload-wheelhouse"
        upload_dir.mkdir(parents=True)

        wh_zip = upload_dir / "wheelhouse.zip"
        with zipfile.ZipFile(wh_zip, "w") as zf:
            zf.writestr("test_pkg-1.0.0-py3-none-any.whl", b"wheel-data")

        (upload_dir / "dataset-metadata.json").write_text(
            json.dumps({"id": "mihtriii/wheelhouse-dataset"}),
            encoding="utf-8",
        )

        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "validate", "wheelhouse", "--level", "full"], cwd=str(project))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("VALID", p.stdout)

    def test_t16_no_sudo_source_installation_layout(self):
        """T16: Unprivileged layout execution without sudo."""
        p = self.run_cmd([str(SYK4Y_BIN), "doctor", "--json"])
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data.get("status"), "READY")
        self.assertIn("bash_version", data)

    def test_t17_kaggle_api_401_403_fails_without_transient_retry(self):
        """T17: HTTP 401/403 fails immediately without burning transient retry attempts."""
        project = self.tmp_path / "AuthFailProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        ds_dir = project / "dataset"
        ds_dir.mkdir()
        (ds_dir / "file.txt").write_text("data", encoding="utf-8")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"
        call_log = self.tmp_path / "kaggle_calls.log"

        fake_kaggle.write_text(
            f"""#!/usr/bin/env bash
echo "$*" >> "{call_log}"
echo "403 - Forbidden: Invalid Kaggle API credentials" >&2
exit 1
""",
            encoding="utf-8",
        )
        fake_kaggle.chmod(0o755)

        env = {
            "KAGGLE_BIN": str(fake_kaggle),
            "KAGGLE_USERNAME": "mihtriii",
            "KAGGLE_KEY": "badkey",
            "KAGGLE_MAX_RETRIES": "3",
        }

        self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Authentication or permission error detected. Not retrying.", p.stderr + p.stdout)

        # Count occurrences in call_log
        lines = [l for l in call_log.read_text(encoding="utf-8").splitlines() if "datasets create" in l or "datasets version" in l]
        self.assertEqual(len(lines), 1, "Must not retry on 403 Forbidden")

    def test_t18_transient_http_429_5xx_retries_with_backoff(self):
        """T18: Transient 429/500 errors are retried up to KAGGLE_MAX_RETRIES."""
        project = self.tmp_path / "TransientRetryProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        ds_dir = project / "dataset"
        ds_dir.mkdir()
        (ds_dir / "file.txt").write_text("data", encoding="utf-8")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"
        call_log = self.tmp_path / "kaggle_calls.log"

        # Fail twice with 429, succeed on third attempt
        attempt_file = self.tmp_path / "attempts.txt"
        attempt_file.write_text("0", encoding="utf-8")

        fake_kaggle.write_text(
            f"""#!/usr/bin/env bash
echo "$*" >> "{call_log}"
if [[ "$1" == "datasets" && "$2" == "status" ]]; then
  echo "ready"
  exit 0
fi

if [[ "$1" == "datasets" && ("$2" == "create" || "$2" == "version") ]]; then
  cur=$(cat "{attempt_file}")
  cur=$((cur + 1))
  echo "$cur" > "{attempt_file}"
  if [[ "$cur" -lt 3 ]]; then
    echo "429 - Too Many Requests" >&2
    exit 1
  fi
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
            "KAGGLE_MAX_RETRIES": "3",
            "KAGGLE_INITIAL_DELAY": "1",
            "KAGGLE_STATUS_POLL_ATTEMPTS": "1",
            "KAGGLE_STATUS_POLL_DELAY": "0.1",
        }

        self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p.returncode, 0, f"stdout: {p.stdout}\nstderr: {p.stderr}")
        self.assertIn("Transient upload error encountered (attempt 1/3)", p.stderr)
        self.assertIn("Transient upload error encountered (attempt 2/3)", p.stderr)

    def test_t19_existing_dataset_metadata_id_unchanged(self):
        """T19: Existing dataset metadata ID is preserved during root resolution and operations."""
        project = self.tmp_path / "PreserveMetaProject"
        project.mkdir()
        upload_dir = project / "kaggle_upload" / "preserve-dataset"
        upload_dir.mkdir(parents=True)
        meta_file = upload_dir / "dataset-metadata.json"
        meta_file.write_text(json.dumps({"id": "custom-owner/special-dataset-slug"}), encoding="utf-8")

        p = self.run_cmd([str(SYK4Y_BIN), "doctor", "--json"], cwd=str(project))
        self.assertEqual(p.returncode, 0)

        # Ensure metadata ID was untouched
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        self.assertEqual(meta["id"], "custom-owner/special-dataset-slug")

    def test_t20_valid_yolo_dataset_no_directory_already_exists_conflict(self):
        """T20: Valid YOLO dataset produces archive without directory entries, preventing Kaggle train/ conflict."""
        ds_dir = self.tmp_path / "yolo_test"
        (ds_dir / "train" / "images").mkdir(parents=True)
        (ds_dir / "train" / "labels").mkdir(parents=True)
        (ds_dir / "train" / "images" / "img.jpg").write_bytes(b"jpg")
        (ds_dir / "train" / "labels" / "img.txt").write_text("0 0.5 0.5 0.1 0.1\n", encoding="utf-8")

        zip_out = self.tmp_path / "yolo.zip"
        ret = py_cli.cmd_pack_artifact_dir_zip(str(ds_dir), str(zip_out), "deflated")
        self.assertEqual(ret, 0)

        with zipfile.ZipFile(zip_out, "r") as zf:
            namelist = zf.namelist()
            self.assertNotIn("train/", namelist)
            self.assertNotIn("train/images/", namelist)
            self.assertIn("train/images/img.jpg", namelist)
            self.assertIn("train/labels/img.txt", namelist)


if __name__ == "__main__":
    unittest.main()
