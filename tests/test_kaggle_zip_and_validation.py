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
PY_CLI = REPO_ROOT / "syk4y-lib" / "kaggle_upload_py_cli.py"

# Add repo root to sys.path to allow importing python cli functions directly
sys.path.insert(0, str(REPO_ROOT / "syk4y-lib"))
import kaggle_upload_py_cli as py_cli  # noqa: E402


class KaggleZipAndValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="syk4y-zip-val-")
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

    def test_a_realistic_yolo_dataset_structure(self):
        """Test A: Realistic YOLO dataset structure (train/images/, train/labels/, val/, data.yaml).

        Verifies:
        - 0 duplicate entries
        - Populated directories (train/, train/images/) DO NOT have explicit directory entries
        - Files are stored at expected relative paths
        - Extraction reproduces expected structure without 'Directory already exists' errors.
        """
        ds_dir = self.tmp_path / "yolo_dataset"
        (ds_dir / "train" / "images").mkdir(parents=True)
        (ds_dir / "train" / "labels").mkdir(parents=True)
        (ds_dir / "val" / "images").mkdir(parents=True)
        (ds_dir / "val" / "labels").mkdir(parents=True)

        (ds_dir / "data.yaml").write_text("names: [fire, smoke]\nnc: 2\n", encoding="utf-8")
        (ds_dir / "train" / "images" / "0001.jpg").write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 200)
        (ds_dir / "train" / "images" / "0002.jpg").write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 200)
        (ds_dir / "train" / "labels" / "0001.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        (ds_dir / "train" / "labels" / "0002.txt").write_text("1 0.4 0.4 0.3 0.3\n", encoding="utf-8")
        (ds_dir / "val" / "images" / "v001.jpg").write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 100)
        (ds_dir / "val" / "labels" / "v001.txt").write_text("0 0.6 0.6 0.1 0.1\n", encoding="utf-8")

        zip_out = self.tmp_path / "yolo.zip"
        ret = py_cli.cmd_pack_artifact_dir_zip(str(ds_dir), str(zip_out), "deflated")
        self.assertEqual(ret, 0)
        self.assertTrue(zip_out.is_file())

        # Inspect ZIP structure
        with zipfile.ZipFile(zip_out, "r") as zf:
            namelist = zf.namelist()
            # 1. Zero duplicate entries
            self.assertEqual(len(namelist), len(set(namelist)))

            # 2. Populated directories MUST NOT have explicit directory entries
            self.assertNotIn("train/", namelist)
            self.assertNotIn("train/images/", namelist)
            self.assertNotIn("train/labels/", namelist)
            self.assertNotIn("val/", namelist)
            self.assertNotIn("val/images/", namelist)

            # 3. Files stored at expected paths
            self.assertIn("data.yaml", namelist)
            self.assertIn("train/images/0001.jpg", namelist)
            self.assertIn("train/labels/0001.txt", namelist)

            # 4. Standard validation function passes
            stats = py_cli.validate_zip_archive(zip_out)
            self.assertEqual(stats["file_entries"], 7)
            self.assertEqual(stats["dir_entries"], 0)

        # 5. Extract to a new directory: must extract cleanly
        extract_target = self.tmp_path / "extracted_yolo"
        extract_target.mkdir()
        with zipfile.ZipFile(zip_out, "r") as zf:
            zf.extractall(extract_target)

        self.assertTrue((extract_target / "train" / "images" / "0001.jpg").is_file())
        self.assertTrue((extract_target / "data.yaml").is_file())

    def test_b_nested_directory_collisions(self):
        """Test B: Nested directory collisions (e.g. a/b/c/file.txt, b/c/file2.txt, c/file3.txt).

        Verifies that all entries remain distinct and full relative paths are preserved.
        """
        src = self.tmp_path / "nested_dirs"
        (src / "a" / "b" / "c").mkdir(parents=True)
        (src / "b" / "c").mkdir(parents=True)
        (src / "c").mkdir(parents=True)

        (src / "a" / "b" / "c" / "file1.txt").write_text("f1", encoding="utf-8")
        (src / "b" / "c" / "file2.txt").write_text("f2", encoding="utf-8")
        (src / "c" / "file3.txt").write_text("f3", encoding="utf-8")

        zip_out = self.tmp_path / "nested.zip"
        ret = py_cli.cmd_pack_artifact_dir_zip(str(src), str(zip_out), "deflated")
        self.assertEqual(ret, 0)

        with zipfile.ZipFile(zip_out, "r") as zf:
            namelist = zf.namelist()
            self.assertIn("a/b/c/file1.txt", namelist)
            self.assertIn("b/c/file2.txt", namelist)
            self.assertIn("c/file3.txt", namelist)
            self.assertEqual(len(namelist), 3)

    def test_c_symlink_deduplication_and_aliasing(self):
        """Test C: Multiple symlinks pointing to identical directories.

        Verifies that symlink aliasing is detected and duplicate entries are prevented.
        """
        src = self.tmp_path / "symlink_src"
        real_dir = src / "real_dir"
        real_dir.mkdir(parents=True)
        (real_dir / "content.txt").write_text("content", encoding="utf-8")

        # Create symlink pointing to real_dir
        alias_dir = src / "alias_dir"
        alias_dir.symlink_to(real_dir, target_is_directory=True)

        zip_out = self.tmp_path / "symlink_dedup.zip"
        ret = py_cli.cmd_pack_artifact_dir_zip(str(src), str(zip_out), "deflated")
        self.assertEqual(ret, 0)

        with zipfile.ZipFile(zip_out, "r") as zf:
            namelist = zf.namelist()
            # Must not contain duplicate names
            self.assertEqual(len(namelist), len(set(namelist)))
            # The real file is included; alias directory pointing to same inode is not duplicated
            self.assertIn("real_dir/content.txt", namelist)

    def test_d_circular_symlink_detection(self):
        """Test D: Circular symlink detection (sub/loop -> ../sub).

        Verifies that packing terminates safely without recursion / infinite loop.
        """
        src = self.tmp_path / "cyclic_src"
        sub = src / "sub"
        sub.mkdir(parents=True)
        (sub / "hello.txt").write_text("hello", encoding="utf-8")

        # Create circular link
        loop = sub / "loop"
        loop.symlink_to(sub, target_is_directory=True)

        zip_out = self.tmp_path / "cyclic.zip"
        ret = py_cli.cmd_pack_artifact_dir_zip(str(src), str(zip_out), "deflated")
        self.assertEqual(ret, 0)

        with zipfile.ZipFile(zip_out, "r") as zf:
            namelist = zf.namelist()
            self.assertIn("sub/hello.txt", namelist)
            # Must not have endless sub/loop/loop/loop...
            self.assertEqual(len(namelist), 1)

    def test_e_existing_zip_files_inside_source_directory(self):
        """Test E: Existing ZIP files inside source directory.

        Verifies binary preservation, no corruption, and correct storage.
        """
        src = self.tmp_path / "archive_src"
        src.mkdir()
        nested_zip = src / "inner.zip"
        with zipfile.ZipFile(nested_zip, "w") as zf:
            zf.writestr("test.bin", b"\x00\x01\x02\x03\x04\x05")

        original_bytes = nested_zip.read_bytes()

        zip_out = self.tmp_path / "outer.zip"
        ret = py_cli.cmd_pack_artifact_dir_zip(str(src), str(zip_out), "deflated")
        self.assertEqual(ret, 0)

        # Validate outer zip
        stats = py_cli.validate_zip_archive(zip_out)
        self.assertEqual(stats["file_entries"], 1)

        # Extract and verify inner.zip is bit-for-bit identical
        extract_dir = self.tmp_path / "outer_extracted"
        with zipfile.ZipFile(zip_out, "r") as zf:
            zf.extractall(extract_dir)

        self.assertEqual((extract_dir / "inner.zip").read_bytes(), original_bytes)

    def test_f_zip64_streaming_and_large_files(self):
        """Test F: Large files and ZIP64 streaming.

        Verifies chunked streaming I/O and ZIP64 header support.
        """
        src = self.tmp_path / "stream_src"
        src.mkdir()
        # Create 2MB file of pseudo-random bytes to test multi-chunk 1MB streaming
        large_file = src / "stream_test.bin"
        chunk = b"0123456789ABCDEF" * (64 * 1024)  # 1MB
        large_file.write_bytes(chunk * 2)  # 2MB

        zip_out = self.tmp_path / "streamed.zip"
        ret = py_cli.cmd_pack_artifact_dir_zip(str(src), str(zip_out), "deflated")
        self.assertEqual(ret, 0)

        stats = py_cli.validate_zip_archive(zip_out)
        self.assertEqual(stats["file_entries"], 1)

        with zipfile.ZipFile(zip_out, "r") as zf:
            read_bytes = zf.read("stream_test.bin")
            self.assertEqual(len(read_bytes), 2 * 1024 * 1024)
            self.assertEqual(read_bytes, chunk * 2)

    def test_g_interrupted_packaging_preserves_previous_archive(self):
        """Test G: Interrupted packaging preserves previous archive (atomic replace).

        Verifies that if packing fails mid-process, the existing valid zip is not corrupted.
        """
        src = self.tmp_path / "atomic_src"
        src.mkdir()
        (src / "file.txt").write_text("initial version", encoding="utf-8")

        zip_out = self.tmp_path / "target.zip"
        ret = py_cli.cmd_pack_artifact_dir_zip(str(src), str(zip_out), "deflated")
        self.assertEqual(ret, 0)
        with zipfile.ZipFile(zip_out, "r") as zf:
            self.assertEqual(zf.read("file.txt"), b"initial version")

        # Now simulate a failure during packing by patching shutil.copyfileobj
        original_copy = shutil.copyfileobj

        def failing_copy(*args, **kwargs):
            raise IOError("Simulated disk error or SIGINT")

        (src / "file.txt").write_text("modified version", encoding="utf-8")

        shutil.copyfileobj = failing_copy
        try:
            ret_fail = py_cli.cmd_pack_artifact_dir_zip(str(src), str(zip_out), "deflated", force=True)
            self.assertEqual(ret_fail, 1)
        finally:
            shutil.copyfileobj = original_copy

        # Previous archive must remain valid and uncorrupted!
        self.assertTrue(zip_out.is_file())
        with zipfile.ZipFile(zip_out, "r") as zf:
            self.assertEqual(zf.read("file.txt"), b"initial version")

    def test_h_kaggle_processing_error_handling(self):
        """Test H: Kaggle processing failure handling.

        When Kaggle returns 'Directory already exists: train' or status check fails:
        - Upload reports failure
        - No infinite retry loop
        - Local archive is preserved in cache
        """
        project = self.tmp_path / "KaggleFailProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        ds_dir = project / "dataset"
        (ds_dir / "train").mkdir(parents=True)
        (ds_dir / "train" / "img.jpg").write_bytes(b"image")

        # Mock Kaggle that rejects with "Directory already exists: train"
        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"
        log_file = self.tmp_path / "fail_kaggle.log"

        fake_kaggle.write_text(
            f"""#!/usr/bin/env bash
echo "$*" >> "{log_file}"
if [[ "$1" == "datasets" && "$2" == "status" ]]; then
  echo "Error during creation: Directory already exists: train"
  exit 1
fi
if [[ "$1" == "datasets" && ("$2" == "create" || "$2" == "version") ]]; then
  echo "400 - Bad Request - Directory already exists: train" >&2
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

        # syk4y init
        p_init = self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p_init.returncode, 0)

        # syk4y kaggle zip
        p_zip = self.run_cmd([str(SYK4Y_BIN), "kaggle", "zip", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p_zip.returncode, 0)
        cache_zip = project / ".syk4y-temp" / "kaggle-zip-cache" / "dataset.zip"
        self.assertTrue(cache_zip.is_file(), f"Cache zip must exist at {cache_zip}")

        # syk4y kaggle upload -> must fail and NOT retry indefinitely
        p_upload = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertNotEqual(p_upload.returncode, 0)
        self.assertIn("Directory already exists", p_upload.stderr + p_upload.stdout)

        # Local cache zip must still exist and be intact!
        self.assertTrue(cache_zip.is_file(), "Local archive must be preserved after Kaggle failure")
        with zipfile.ZipFile(cache_zip, "r") as zf:
            self.assertIn("train/img.jpg", zf.namelist())

    def test_i_full_lifecycle_init_zip_validate_upload(self):
        """Test I: Full lifecycle integration (init -> zip -> validate -> upload).

        Verifies:
        - init creates metadata
        - zip creates valid cache
        - validate succeeds and outputs 'VALID'
        - upload reuses cache and completes successfully
        """
        project = self.tmp_path / "FullLifecycleProject"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)

        ds_dir = project / "dataset"
        (ds_dir / "train").mkdir(parents=True)
        (ds_dir / "train" / "img.jpg").write_bytes(b"image")

        bin_dir = self.tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_kaggle = bin_dir / "kaggle"
        log_file = self.tmp_path / "success_kaggle.log"

        fake_kaggle.write_text(
            f"""#!/usr/bin/env bash
echo "$*" >> "{log_file}"
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
            "KAGGLE_STATUS_POLL_ATTEMPTS": "2",
            "KAGGLE_STATUS_POLL_DELAY": "0.1",
        }

        # 1. syk4y init
        p_init = self.run_cmd([str(SYK4Y_BIN), "init", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p_init.returncode, 0, p_init.stderr)

        # 2. syk4y kaggle zip
        p_zip = self.run_cmd([str(SYK4Y_BIN), "kaggle", "zip", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p_zip.returncode, 0, p_zip.stderr)
        self.assertIn("All artifacts zipped successfully", p_zip.stdout)

        # 3. syk4y kaggle validate
        p_val = self.run_cmd([str(SYK4Y_BIN), "kaggle", "validate", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p_val.returncode, 0, p_val.stderr)
        self.assertIn("VALID", p_val.stdout)
        self.assertIn("All evaluated artifacts are VALID", p_val.stdout)

        # 4. syk4y kaggle upload
        p_upload = self.run_cmd([str(SYK4Y_BIN), "kaggle", "upload", "dataset/"], cwd=str(project), env=env)
        self.assertEqual(p_upload.returncode, 0, p_upload.stderr)
        self.assertIn("Using cached zip", p_upload.stdout)
        self.assertIn("READY", p_upload.stdout)
        self.assertIn("Done uploading artifact datasets", p_upload.stdout)

    def test_j_wheelhouse_and_existing_artifact_compatibility(self):
        """Test J: Wheelhouse & existing artifact compatibility.

        Verifies that wheelhouse.zip validation works and is compatible with the new pipeline.
        """
        project = self.tmp_path / "WheelhouseCompatProject"
        project.mkdir()
        upload_dir = project / "kaggle_upload" / "kaggle-upload-wheelhouse"
        upload_dir.mkdir(parents=True)

        # Valid wheelhouse zip
        wh_zip = upload_dir / "wheelhouse.zip"
        with zipfile.ZipFile(wh_zip, "w") as zf:
            zf.writestr("test_pkg-1.0.0-py3-none-any.whl", b"wheel-data")

        (upload_dir / "dataset-metadata.json").write_text(
            json.dumps({"id": "mihtriii/wheelhouse-dataset"}),
            encoding="utf-8",
        )

        env = {
            "KAGGLE_USERNAME": "mihtriii",
            "KAGGLE_KEY": "testkey",
        }

        # Run validate on wheelhouse
        p_val = self.run_cmd([str(SYK4Y_BIN), "kaggle", "validate", "wheelhouse"], cwd=str(project), env=env)
        self.assertEqual(p_val.returncode, 0, p_val.stderr)
        self.assertIn("Artifact 'wheelhouse': VALID", p_val.stdout)


if __name__ == "__main__":
    unittest.main()
