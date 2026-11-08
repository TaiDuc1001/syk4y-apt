import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SYK4Y_BIN = REPO_ROOT / "syk4y"
PY_CLI = REPO_ROOT / "syk4y-lib" / "kaggle_upload_py_cli.py"


class SymlinkPreflightAndDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

        # Set up a fake kaggle executable
        self.bin_dir = self.tmp_path / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.fake_kaggle = self.bin_dir / "kaggle"
        self.fake_kaggle.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.fake_kaggle.chmod(0o755)

        self.env = os.environ.copy()
        self.env["PATH"] = f"{self.bin_dir}:{self.env.get('PATH', '')}"
        self.env["KAGGLE_BIN"] = str(self.fake_kaggle)
        self.env["KAGGLE_USERNAME"] = "testuser"
        self.env["KAGGLE_KEY"] = "testkey"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def run_cmd(self, cmd, cwd=None, env=None):
        run_env = self.env.copy()
        if env:
            run_env.update(env)
        return subprocess.run(
            cmd,
            cwd=str(cwd or self.tmp_path),
            env=run_env,
            capture_output=True,
            text=True,
        )

    def create_yolo_dataset(self, root: Path, include_dangling: bool = False, include_escape: bool = False, include_cycle: bool = False):
        ds = root / "dataset"
        images_train = ds / "images" / "train"
        images_val = ds / "images" / "val"
        labels_train = ds / "labels" / "train"
        labels_val = ds / "labels" / "val"

        for d in (images_train, images_val, labels_train, labels_val):
            d.mkdir(parents=True, exist_ok=True)

        (ds / "data.yaml").write_text("train: images/train\nval: images/val\nnc: 1\nnames: ['fire']\n", encoding="utf-8")
        (images_train / "img1.jpg").write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 32)
        (labels_train / "img1.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")

        # Valid relative symlink within dataset: val/img1.jpg -> ../train/img1.jpg
        (images_val / "img1.jpg").symlink_to(Path("..") / "train" / "img1.jpg")
        (labels_val / "img1.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")

        if include_dangling:
            # Recreate the exact HPC incident: dangling symlink pointing to missing raw path
            dangling_link = images_train / "video10_frame_00096.jpg"
            dangling_link.symlink_to(Path("data") / "raw" / "rgbt_3m" / "rgb" / "train" / "video10_frame_00096.jpg")

        if include_escape:
            secret = self.tmp_path / "external_file.txt"
            secret.write_text("outside", encoding="utf-8")
            escape_link = images_train / "leak.jpg"
            escape_link.symlink_to(secret)

        if include_cycle:
            l1 = images_train / "cycle_a"
            l2 = images_train / "cycle_b"
            l1.symlink_to(l2)
            l2.symlink_to(l1)

        return ds

    def test_01_valid_yolo_dataset_passes_preflight(self):
        """Valid YOLO dataset with internal relative symlink passes preflight check."""
        ds = self.create_yolo_dataset(self.tmp_path)
        report_file = self.tmp_path / "report.json"

        p = self.run_cmd([
            str(SYK4Y_BIN), "kaggle", "preflight", str(ds),
            "--report", str(report_file)
        ])
        self.assertEqual(p.returncode, 0, f"stdout: {p.stdout}\nstderr: {p.stderr}")
        self.assertIn("Status: PASSED", p.stdout)
        self.assertIn("1 valid", p.stdout)

        self.assertTrue(report_file.is_file())
        rep = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertTrue(rep["is_valid"])
        self.assertEqual(rep["summary"]["broken_symlinks_count"], 0)
        self.assertEqual(rep["summary"]["valid_symlinks"], 1)

    def test_02_dangling_symlink_fails_preflight_cleanly_without_traceback(self):
        """Dangling symlink produces concise diagnostic summary with NO Python traceback."""
        ds = self.create_yolo_dataset(self.tmp_path, include_dangling=True)
        report_file = self.tmp_path / "dangling_report.json"

        p = self.run_cmd([
            str(SYK4Y_BIN), "kaggle", "preflight", str(ds),
            "--report", str(report_file)
        ])
        self.assertNotEqual(p.returncode, 0)
        # Verify concise summary output
        self.assertIn("Status: FAILED", p.stderr)
        self.assertIn("Broken / Dangling Symlinks", p.stderr)
        self.assertIn("video10_frame_00096.jpg", p.stderr)
        # Requirement 1: NO unhandled Python traceback
        self.assertNotIn("Traceback (most recent call last)", p.stderr)

        self.assertTrue(report_file.is_file())
        rep = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertFalse(rep["is_valid"])
        self.assertEqual(rep["summary"]["broken_symlinks_count"], 1)
        self.assertIn("video10_frame_00096.jpg", rep["broken_symlinks"][0]["link"])

    def test_03_escaping_symlink_detected_and_reported(self):
        """Symlink escaping dataset root is caught as external symlink."""
        ds = self.create_yolo_dataset(self.tmp_path, include_escape=True)
        report_file = self.tmp_path / "escape_report.json"

        p = self.run_cmd([
            str(SYK4Y_BIN), "kaggle", "preflight", str(ds),
            "--report", str(report_file)
        ])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Status: FAILED", p.stderr)
        self.assertIn("External / Escaping Symlinks", p.stderr)
        self.assertIn("leak.jpg", p.stderr)
        self.assertNotIn("Traceback (most recent call last)", p.stderr)

        rep = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertFalse(rep["is_valid"])
        self.assertEqual(rep["summary"]["external_symlinks_count"], 1)

    def test_04_circular_symlink_loop_detected(self):
        """Circular symlink loop is detected without hanging or recursion error."""
        ds = self.create_yolo_dataset(self.tmp_path, include_cycle=True)
        report_file = self.tmp_path / "cycle_report.json"

        p = self.run_cmd([
            str(SYK4Y_BIN), "kaggle", "preflight", str(ds),
            "--report", str(report_file)
        ])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Status: FAILED", p.stderr)
        self.assertIn("Circular / Looping Symlinks", p.stderr)
        self.assertNotIn("Traceback (most recent call last)", p.stderr)

        rep = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertFalse(rep["is_valid"])
        self.assertGreaterEqual(rep["summary"]["circular_symlinks_count"], 1)

    def test_05_explicit_exclude_allows_omitting_broken_subset(self):
        """Explicit --exclude allows intentionally omitted subsets to pass preflight."""
        ds = self.create_yolo_dataset(self.tmp_path, include_dangling=True)
        report_file = self.tmp_path / "exclude_report.json"

        # Preflight with --exclude for the dangling file
        p = self.run_cmd([
            str(SYK4Y_BIN), "kaggle", "preflight", str(ds),
            "--exclude", "images/train/video10_*",
            "--report", str(report_file)
        ])
        self.assertEqual(p.returncode, 0, f"stdout: {p.stdout}\nstderr: {p.stderr}")
        self.assertIn("Status: PASSED", p.stdout)
        self.assertIn("Excluded: 1 entries", p.stdout)

        rep = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertTrue(rep["is_valid"])
        self.assertEqual(rep["summary"]["broken_symlinks_count"], 0)
        self.assertEqual(rep["summary"]["excluded_count"], 1)

    def test_06_compute_source_fingerprint_no_traceback_on_error(self):
        """Public CLI entrypoint compute-source-fingerprint outputs concise error without traceback."""
        ds = self.create_yolo_dataset(self.tmp_path, include_dangling=True)

        p = self.run_cmd([
            sys.executable, str(PY_CLI),
            "compute-source-fingerprint", str(ds)
        ])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Error: Dangling symlink detected", p.stderr)
        self.assertNotIn("Traceback (most recent call last)", p.stderr)

    def test_07_upload_fails_fast_before_network_when_preflight_invalid(self):
        """syk4y kaggle upload fails before any network transfer when dataset has broken symlinks."""
        project = self.tmp_path / "project"
        project.mkdir()
        ds = self.create_yolo_dataset(project, include_dangling=True)

        # Initialize artifact
        init_res = self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=project)
        self.assertEqual(init_res.returncode, 0)

        # Upload attempt must fail fast
        up_res = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=project)
        self.assertNotEqual(up_res.returncode, 0)
        self.assertIn("dataset preflight failed", up_res.stderr.lower())
        self.assertIn("broken / dangling symlinks", up_res.stderr.lower())
        self.assertNotIn("Traceback (most recent call last)", up_res.stderr)

    def test_08_preserve_existing_valid_zip_cache_on_failure(self):
        """Existing valid ZIP cache is preserved untouched when a subsequent preflight or zip fails."""
        project = self.tmp_path / "project_cache"
        project.mkdir()
        ds = self.create_yolo_dataset(project, include_dangling=False)

        # Initialize and build valid zip cache
        self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=project)
        zip_res = self.run_cmd([str(SYK4Y_BIN), "kaggle", "zip", "dataset/"], cwd=project)
        self.assertEqual(zip_res.returncode, 0, f"stdout: {zip_res.stdout}\nstderr: {zip_res.stderr}")

        cache_zip = project / ".syk4y-temp" / "kaggle-zip-cache" / "wildfire-detection-dataset.zip"
        if not cache_zip.exists():
            # Find the actual zip
            zips = list(project.glob(".syk4y-temp/kaggle-zip-cache/*.zip"))
            self.assertEqual(len(zips), 1)
            cache_zip = zips[0]

        original_size = cache_zip.stat().st_size
        original_mtime = cache_zip.stat().st_mtime_ns

        # Now introduce a broken symlink in the source dataset
        bad_link = ds / "images" / "train" / "corrupt_link.jpg"
        bad_link.symlink_to(Path("missing_target.jpg"))

        # Re-run kaggle zip; must fail fast
        zip_fail = self.run_cmd([str(SYK4Y_BIN), "kaggle", "zip", "dataset/"], cwd=project)
        self.assertNotEqual(zip_fail.returncode, 0)
        self.assertIn("preflight failed", zip_fail.stderr.lower())

        # Original cache must still exist with identical size and mtime!
        self.assertTrue(cache_zip.is_file())
        self.assertEqual(cache_zip.stat().st_size, original_size)
        self.assertEqual(cache_zip.stat().st_mtime_ns, original_mtime)

    def test_09_detect_source_modification_between_fingerprint_and_pack(self):
        """Detect source modification between fingerprinting and packaging."""
        ds = self.create_yolo_dataset(self.tmp_path)
        out_zip = self.tmp_path / "test.zip"

        # Compute initial fingerprint
        p_fp = self.run_cmd([sys.executable, str(PY_CLI), "compute-source-fingerprint", str(ds)])
        self.assertEqual(p_fp.returncode, 0)
        initial_fp = p_fp.stdout.strip()

        # Modify dataset
        (ds / "images" / "train" / "new_img.jpg").write_text("modified", encoding="utf-8")

        # Attempt to pack with outdated expected fingerprint
        p_pack = self.run_cmd([
            sys.executable, str(PY_CLI),
            "pack-artifact-dir-zip", str(ds), str(out_zip), "store",
            "--expected-fp", initial_fp
        ])
        self.assertNotEqual(p_pack.returncode, 0)
        self.assertIn("source dataset modified between fingerprinting and packaging", p_pack.stderr.lower())
        self.assertFalse(out_zip.exists())

    def test_10_system_junk_auto_exclusion(self):
        """OS metadata junk (.DS_Store, __MACOSX, Thumbs.db) is auto-omitted and reported."""
        ds = self.create_yolo_dataset(self.tmp_path)
        (ds / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        (ds / "images" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        (ds / "Thumbs.db").write_bytes(b"\xD0\xCF\x11\xE0")
        macosx_dir = ds / "__MACOSX"
        macosx_dir.mkdir()
        (macosx_dir / "._data.yaml").write_bytes(b"mac resource fork")

        out_zip = self.tmp_path / "cleaned.zip"
        p_pack = self.run_cmd([
            sys.executable, str(PY_CLI),
            "pack-artifact-dir-zip", str(ds), str(out_zip), "store"
        ])
        self.assertEqual(p_pack.returncode, 0, p_pack.stderr)
        self.assertTrue(out_zip.is_file())

        with zipfile.ZipFile(out_zip, "r") as zf:
            names = zf.namelist()
            self.assertNotIn(".DS_Store", names)
            self.assertNotIn("images/.DS_Store", names)
            self.assertNotIn("Thumbs.db", names)
            self.assertFalse(any("__MACOSX" in n for n in names))

    def test_11_case_insensitive_duplicate_collision_detected(self):
        """Case-insensitive duplicate file collision is caught and reported."""
        # 1. Test validate-artifact-archive against a zip with case collisions
        zip_path = self.tmp_path / "case_dup.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("data/train.csv", b"col1,col2\n")
            zf.writestr("data/TRAIN.CSV", b"COL1,COL2\n")

        meta_path = self.tmp_path / "dataset-metadata.json"
        meta_path.write_text(json.dumps({"id": "user/dataset"}), encoding="utf-8")

        p_val = self.run_cmd([
            sys.executable, str(PY_CLI),
            "validate-artifact-archive", str(zip_path), str(meta_path)
        ])
        self.assertNotEqual(p_val.returncode, 0)
        self.assertIn("case-insensitive duplicate entry", p_val.stderr.lower())

        # 2. Test pack-artifact-dir-zip if filesystem actually supports distinct case files
        ds = self.create_yolo_dataset(self.tmp_path / "ds_case")
        try:
            (ds / "data.YAML").write_text("uppercase extension", encoding="utf-8")
        except OSError:
            return

        # On case-sensitive filesystems (Linux ext4), both distinct entries will exist in iterdir:
        matching = [f.name for f in ds.iterdir() if f.name.lower() == "data.yaml"]
        if len(matching) > 1:
            out_zip = self.tmp_path / "case_test.zip"
            p_pack = self.run_cmd([
                sys.executable, str(PY_CLI),
                "pack-artifact-dir-zip", str(ds), str(out_zip), "store"
            ])
            self.assertNotEqual(p_pack.returncode, 0)
            self.assertIn("case-insensitive", p_pack.stderr.lower())

    def test_12_nested_archive_directory_collision_detected(self):
        """Archive coexisting with directory of same stem (train.zip and train/) is caught."""
        ds = self.create_yolo_dataset(self.tmp_path)
        # Create a nested train.zip at dataset root while train/ is an existing directory
        train_dir = ds / "train"
        train_dir.mkdir(exist_ok=True)
        (train_dir / "sample.txt").write_text("hello", encoding="utf-8")
        (ds / "train.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # empty valid zip header

        p_preflight = self.run_cmd([
            sys.executable, str(PY_CLI),
            "preflight-dataset", str(ds)
        ])
        self.assertNotEqual(p_preflight.returncode, 0)
        self.assertIn("archive vs directory collision", p_preflight.stderr.lower())
        self.assertIn("train.zip", p_preflight.stderr)


if __name__ == "__main__":
    unittest.main()
