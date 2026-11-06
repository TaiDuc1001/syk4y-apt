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


class Syk4yRegressionSuite(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="syk4y-test-")
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

    def test_root_resolution_from_root_and_kaggle_upload(self):
        """Invoke from root, from kaggle_upload, and verify same project resolution."""
        project = self.tmp_path / "MyProject"
        project.mkdir()
        (project / "pyproject.toml").write_text("[project]\nname = 'myproject'\n", encoding="utf-8")
        upload_dir = project / "kaggle_upload"
        upload_dir.mkdir()

        # 1. From project root
        p1 = self.run_cmd([str(SYK4Y_BIN), "doctor", "--json"], cwd=str(project))
        self.assertEqual(p1.returncode, 0, p1.stderr)
        data1 = json.loads(p1.stdout)
        self.assertEqual(Path(data1["project_root"]["path"]).resolve(), project.resolve())

        # 2. From inside kaggle_upload
        p2 = self.run_cmd([str(SYK4Y_BIN), "doctor", "--json"], cwd=str(upload_dir))
        self.assertEqual(p2.returncode, 0, p2.stderr)
        data2 = json.loads(p2.stdout)
        self.assertEqual(Path(data2["project_root"]["path"]).resolve(), project.resolve())

    def test_invoke_with_explicit_repo_root(self):
        """Invoke from unrelated directory with explicit --repo-root."""
        project = self.tmp_path / "TargetProject"
        project.mkdir()
        unrelated = self.tmp_path / "UnrelatedDir"
        unrelated.mkdir()

        p = self.run_cmd(
            [str(SYK4Y_BIN), "--repo-root", str(project), "doctor", "--json"],
            cwd=str(unrelated),
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(Path(data["project_root"]["path"]).resolve(), project.resolve())

    def test_artifact_directory_with_spaces(self):
        """Artifact discovery handles spaces in paths and metadata correctly."""
        project = self.tmp_path / "Space Project"
        project.mkdir()
        upload_dir = project / "kaggle_upload"
        art_dir = upload_dir / "space-project-my weights"
        art_dir.mkdir(parents=True)
        (art_dir / "dataset-metadata.json").write_text(
            json.dumps({"id": "testuser/space-project-my-weights"}),
            encoding="utf-8",
        )

        p = self.run_cmd([str(SYK4Y_BIN), "doctor", "--json"], cwd=str(project))
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data["artifacts"]["count"], 1)
        self.assertIn("space-project-my weights", data["artifacts"]["items"])

    def test_missing_metadata_detection(self):
        """Missing metadata in candidate directory produces an actionable error."""
        project = self.tmp_path / "MissingMetaProj"
        project.mkdir()
        upload_dir = project / "kaggle_upload"
        art_dir = upload_dir / "missingmetaproj-models"
        art_dir.mkdir(parents=True)
        # No dataset-metadata.json inside art_dir

        env = {"KAGGLE_USERNAME": "usr", "KAGGLE_KEY": "key"}
        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "models"], cwd=str(project), env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("missing required metadata file", p.stderr.lower())

    def test_invalid_metadata_validation_failure(self):
        """Invalid or corrupt metadata fails safely with diagnostic."""
        project = self.tmp_path / "InvalidMetaProj"
        project.mkdir()
        upload_dir = project / "kaggle_upload"
        art_dir = upload_dir / "invalidmetaproj-models"
        art_dir.mkdir(parents=True)
        (art_dir / "dataset-metadata.json").write_text("{broken json...", encoding="utf-8")

        env = {"KAGGLE_USERNAME": "usr", "KAGGLE_KEY": "key"}
        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "models"], cwd=str(project), env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("invalid or corrupt", p.stderr.lower())

    def test_legacy_nested_layout_detection_and_dryrun_and_migration(self):
        """Detect legacy project/kaggle_upload/kaggle_upload, verify dry-run, then migrate."""
        project = self.tmp_path / "Wildfire-Detection"
        project.mkdir()
        nested_dir = project / "kaggle_upload" / "kaggle_upload"
        art_dir = nested_dir / "kaggle-upload-wheelhouse"
        art_dir.mkdir(parents=True)

        meta_file = art_dir / "dataset-metadata.json"
        meta_file.write_text(json.dumps({"id": "mihtriii/kaggle-upload-wheelhouse"}), encoding="utf-8")

        wh_zip = art_dir / "wheelhouse.zip"
        with zipfile.ZipFile(wh_zip, "w") as zf:
            zf.writestr("_requirements.txt", "torch==2.1.0\n")
            zf.writestr("pkg-0.1-py3-none-any.whl", b"fake-wheel-content")

        # 1. Doctor detects legacy nested layout
        p_doc = self.run_cmd([str(SYK4Y_BIN), "doctor"], cwd=str(project))
        self.assertIn("Legacy nested layout detected", p_doc.stdout)

        # 2. Dry run reports planned migration without disk changes
        p_dry = self.run_cmd([str(SYK4Y_BIN), "migrate-layout", "--dry-run"], cwd=str(project))
        self.assertEqual(p_dry.returncode, 0, p_dry.stderr)
        self.assertIn("DRY RUN COMPLETE", p_dry.stdout)
        self.assertTrue(wh_zip.exists(), "Dry-run must not modify files")

        # 3. Apply migration
        p_mig = self.run_cmd([str(SYK4Y_BIN), "migrate-layout"], cwd=str(project))
        self.assertEqual(p_mig.returncode, 0, p_mig.stderr)
        self.assertIn("Migration finished successfully", p_mig.stdout)

        # 4. Verify migrated location
        migrated_zip = project / "kaggle_upload" / "kaggle-upload-wheelhouse" / "wheelhouse.zip"
        self.assertTrue(migrated_zip.is_file(), "Wheelhouse archive must exist in standard layout")

        # Verify integrity was preserved
        with zipfile.ZipFile(migrated_zip, "r") as zf:
            self.assertIn("pkg-0.1-py3-none-any.whl", zf.namelist())

        # Verify legacy directory was backed up, not deleted
        backups = list((project / "kaggle_upload").glob("kaggle_upload_legacy_*"))
        self.assertEqual(len(backups), 1, "Legacy directory must be preserved as backup")

    def test_concurrent_upload_lock_prevents_conflict(self):
        """Simulate concurrent operation and verify lock prevents state corruption."""
        project = self.tmp_path / "LockedProj"
        project.mkdir()
        upload_dir = project / "kaggle_upload"
        upload_dir.mkdir()
        lock_dir = upload_dir / ".upload.lock"
        lock_dir.mkdir()
        (lock_dir / "pid").write_text(str(os.getpid()), encoding="utf-8")

        env = {"KAGGLE_USERNAME": "usr", "KAGGLE_KEY": "key"}
        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload"], cwd=str(project), env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("concurrent syk4y upload in progress", p.stderr.lower())

    def test_failed_upload_preserves_built_archive(self):
        """Failed Kaggle upload must not destroy the local wheelhouse archive."""
        project = self.tmp_path / "UploadFailProj"
        project.mkdir()
        upload_dir = project / "kaggle_upload"
        art_dir = upload_dir / "uploadfailproj-wheelhouse"
        art_dir.mkdir(parents=True)

        (art_dir / "dataset-metadata.json").write_text(
            json.dumps({"id": "mihtriii/uploadfailproj-wheelhouse"}),
            encoding="utf-8",
        )
        wh_zip = art_dir / "wheelhouse.zip"
        with zipfile.ZipFile(wh_zip, "w") as zf:
            zf.writestr("test.whl", b"wheel-data")

        # Fake failing kaggle command
        fake_kaggle = self.tmp_path / "fake-failing-kaggle.sh"
        fake_kaggle.write_text("#!/bin/sh\necho 'Kaggle: 500 Internal Server Error' >&2\nexit 1\n", encoding="utf-8")
        fake_kaggle.chmod(0o755)

        env = {
            "PATH": f"{self.tmp_path}:{os.environ.get('PATH', '')}",
            "KAGGLE_USERNAME": "mihtriii",
            "KAGGLE_KEY": "key",
        }
        p = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "wheelhouse"], cwd=str(project), env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue(wh_zip.is_file(), "Local archive must be preserved after upload failure")
        with zipfile.ZipFile(wh_zip, "r") as zf:
            self.assertEqual(zf.read("test.whl"), b"wheel-data")

    def test_init_discovery_upload_integration_mocked_kaggle(self):
        """Integration test covering init -> discovery -> upload with mocked Kaggle CLI."""
        project = self.tmp_path / "Wildfire-Detection"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        dataset_dir = project / "dataset"
        dataset_dir.mkdir()
        (dataset_dir / "samples.csv").write_text("id,val\n1,100\n", encoding="utf-8")

        # Mock Kaggle executable
        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"
        log_file = self.tmp_path / "kaggle_calls.log"

        fake_kaggle.write_text(
            f"""#!/usr/bin/env bash
echo "$*" >> "{log_file}"
for ((i=1; i<=$#; i++)); do
  if [[ "${{!i}}" == "-p" ]]; then
    next_idx=$((i+1))
    stage_dir="${{!next_idx}}"
    echo "STAGE_DIR=$stage_dir" >> "{log_file}"
    ls -la "$stage_dir" >> "{log_file}"
  fi
done
exit 0
""",
            encoding="utf-8",
        )
        fake_kaggle.chmod(0o755)

        env = {
            "KAGGLE_BIN": str(fake_kaggle),
            "KAGGLE_USERNAME": "hpcuser",
            "KAGGLE_KEY": "hpckey",
        }

        # 1. Run syk4y init dataset/ from project root
        p_init = self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p_init.returncode, 0, f"init stdout: {p_init.stdout}\nstderr: {p_init.stderr}")
        expected_meta = project / "kaggle_upload" / "wildfire-detection-dataset" / "dataset-metadata.json"
        self.assertTrue(expected_meta.is_file(), "dataset-metadata.json must be created by init")

        # 2. Run syk4y kaggle upload dataset/ from same project root
        p_upload = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p_upload.returncode, 0, f"upload stdout: {p_upload.stdout}\nstderr: {p_upload.stderr}")
        self.assertNotIn("no initialized artifacts found", p_upload.stderr)
        self.assertIn("Done uploading artifact datasets", p_upload.stdout)

        # 3. Verify directory upload ZIP staging
        calls = log_file.read_text(encoding="utf-8")
        self.assertIn("dataset.zip", calls, "Staging directory must contain dataset.zip")
        self.assertIn("dataset-metadata.json", calls, "Staging directory must contain dataset-metadata.json")
        self.assertIn("datasets version -p", calls)

    def test_multiple_artifacts_with_spaces_and_staging(self):
        """Test multiple artifacts including directory paths containing spaces."""
        project = self.tmp_path / "MultiSpaceProj"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        data_dir = project / "my raw data"
        data_dir.mkdir()
        (data_dir / "data.bin").write_bytes(b"binary-payload")

        models_dir = project / "my trained models"
        models_dir.mkdir()
        (models_dir / "weights.pt").write_bytes(b"torch-weights")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"
        log_file = self.tmp_path / "kaggle_multi_calls.log"

        fake_kaggle.write_text(
            f"""#!/usr/bin/env bash
echo "$*" >> "{log_file}"
for ((i=1; i<=$#; i++)); do
  if [[ "${{!i}}" == "-p" ]]; then
    next_idx=$((i+1))
    stage_dir="${{!next_idx}}"
    echo "STAGE_DIR=$stage_dir" >> "{log_file}"
    ls -la "$stage_dir" >> "{log_file}"
  fi
done
exit 0
""",
            encoding="utf-8",
        )
        fake_kaggle.chmod(0o755)

        env = {
            "KAGGLE_BIN": str(fake_kaggle),
            "KAGGLE_USERNAME": "testuser",
            "KAGGLE_KEY": "testkey",
        }

        # Init multiple artifacts
        p_init = self.run_cmd(
            [str(SYK4Y_BIN), "init", "my raw data", "my trained models"],
            cwd=str(project),
            env=env,
        )
        self.assertEqual(p_init.returncode, 0, f"init error: {p_init.stderr}")

        # Upload multiple artifacts from project root
        p_upload = self.run_cmd(
            [str(SYK4Y_BIN), "kaggle", "upload", "my raw data", "my trained models"],
            cwd=str(project),
            env=env,
        )
        self.assertEqual(p_upload.returncode, 0, f"upload error: {p_upload.stderr}")
        self.assertNotIn("no initialized artifacts found", p_upload.stderr)

        calls = log_file.read_text(encoding="utf-8")
        self.assertIn("my raw data.zip", calls)
        self.assertIn("my trained models.zip", calls)

    def test_no_sudo_source_installation_layout(self):
        """Simulate cloning to $HOME/.local/share/syk4y and running via PATH."""
        fake_home = self.tmp_path / "fake_home"
        install_dir = fake_home / ".local" / "share" / "syk4y"
        install_dir.parent.mkdir(parents=True)

        # Copy repository content to install_dir
        shutil.copytree(
            REPO_ROOT,
            install_dir,
            ignore=shutil.ignore_patterns(".git", "tests", "__pycache__", "*.pyc"),
        )

        test_project = fake_home / "ResearchProject"
        test_project.mkdir()
        (test_project / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")

        env = {
            "HOME": str(fake_home),
            "PATH": f"{install_dir}:{os.environ.get('PATH', '')}",
        }
        # Run syk4y from PATH without sudo
        p = self.run_cmd(["syk4y", "doctor", "--json"], cwd=str(test_project), env=env)
        self.assertEqual(p.returncode, 0, f"stdout: {p.stdout}\nstderr: {p.stderr}")
        data = json.loads(p.stdout)
        self.assertEqual(Path(data["project_root"]["path"]).resolve(), test_project.resolve())
        self.assertEqual(data["status"], "READY")


if __name__ == "__main__":
    unittest.main()
