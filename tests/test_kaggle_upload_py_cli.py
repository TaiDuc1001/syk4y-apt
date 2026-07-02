import importlib.util
import io
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PY_CLI = REPO_ROOT / "syk4y-lib" / "kaggle_upload_py_cli.py"


def load_py_cli_module():
    spec = importlib.util.spec_from_file_location("kaggle_upload_py_cli", PY_CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_py_cli(*args: str):
    return subprocess.run(
        ["python3", str(PY_CLI), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class KaggleUploadPyCliTests(unittest.TestCase):
    def test_sanitize_maps_kaggle_working_wheel_to_repo_wheels(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            wheels = repo / "wheels"
            wheels.mkdir(parents=True)
            wheel_name = "learn2learn-0.2.1-cp312-cp312-linux_x86_64.whl"
            (wheels / wheel_name).write_bytes(b"wheel")
            requirements = Path(tmp) / "requirements.txt"
            sanitized = Path(tmp) / "sanitized.txt"
            requirements.write_text(
                "learn2learn @ "
                f"file:///kaggle/working/wheels/{wheel_name}\n",
                encoding="utf-8",
            )

            proc = run_py_cli(
                "sanitize-wheelhouse-requirements",
                str(requirements),
                str(sanitized),
                str(repo),
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                sanitized.read_text(encoding="utf-8"),
                f"wheels/{wheel_name}\n",
            )
            self.assertIn("Mapped local wheel requirement", proc.stderr)

    def test_zip_packers_force_zip64_for_streamed_files(self):
        module = load_py_cli_module()
        open_calls = []

        class RecordingZipFile:
            def __init__(self, output, mode, compression):
                self.output = output
                self.mode = mode
                self.compression = compression

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def open(self, info, mode, force_zip64=False):
                open_calls.append((info.filename, mode, force_zip64))
                return io.BytesIO()

            def writestr(self, info, data):
                pass

        original_zip_file = module.zipfile.ZipFile
        module.zipfile.ZipFile = RecordingZipFile
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)

                wheelhouse = tmp_path / "wheelhouse"
                wheelhouse.mkdir()
                (wheelhouse / "pkg.whl").write_bytes(b"0" * (17 * 1024 * 1024))
                module.cmd_pack_wheelhouse_zip(str(wheelhouse), str(tmp_path / "wheelhouse.zip"), "store")

                artifacts = tmp_path / "artifacts"
                artifacts.mkdir()
                (artifacts / "model.bin").write_bytes(b"0" * (17 * 1024 * 1024))
                module.cmd_pack_artifact_dir_zip(str(artifacts), str(tmp_path / "artifacts.zip"), "store")
        finally:
            module.zipfile.ZipFile = original_zip_file

        self.assertIn(("pkg.whl", "w", True), open_calls)
        self.assertIn(("model.bin", "w", True), open_calls)

    def test_pack_artifact_dir_zip_follows_nested_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            external = tmp_path / "external" / "UCF101"
            external.mkdir(parents=True)
            (external / "clip.txt").write_text("video payload\n", encoding="utf-8")

            source = tmp_path / "datasets"
            source.mkdir()
            (source / "UCF101").symlink_to(external, target_is_directory=True)

            output_zip = tmp_path / "datasets.zip"
            proc = run_py_cli("pack-artifact-dir-zip", str(source), str(output_zip), "zip")

            self.assertEqual(proc.returncode, 0, proc.stderr)
            with zipfile.ZipFile(output_zip) as zf:
                self.assertIn("UCF101/clip.txt", zf.namelist())
                self.assertEqual(zf.read("UCF101/clip.txt"), b"video payload\n")

    def test_fingerprint_path_tracks_nested_symlink_target_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            external = tmp_path / "external" / "UCF101"
            external.mkdir(parents=True)
            payload = external / "clip.txt"
            payload.write_text("before\n", encoding="utf-8")

            source = tmp_path / "datasets"
            source.mkdir()
            (source / "UCF101").symlink_to(external, target_is_directory=True)

            before = run_py_cli("fingerprint-path", str(source))
            self.assertEqual(before.returncode, 0, before.stderr)

            payload.write_text("after\n", encoding="utf-8")
            after = run_py_cli("fingerprint-path", str(source))
            self.assertEqual(after.returncode, 0, after.stderr)

            self.assertNotEqual(before.stdout.strip(), after.stdout.strip())

    def test_rewrite_dataset_owner_repairs_stale_metadata_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            metadata = Path(tmp) / "dataset-metadata.json"
            metadata.write_text(
                '{"id":"your-kaggle-username/repo-wheelhouse","title":"repo wheelhouse"}\n',
                encoding="utf-8",
            )

            proc = run_py_cli("rewrite-dataset-owner", str(metadata), "ducphan1001")

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), "ducphan1001/repo-wheelhouse")
            self.assertIn(
                '"id": "ducphan1001/repo-wheelhouse"',
                metadata.read_text(encoding="utf-8"),
            )

    def test_pack_wheelhouse_zip_sanitizes_local_wheel_paths(self):
        module = load_py_cli_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_dir = tmp_path / "source"
            source_dir.mkdir()

            req_txt = source_dir / "_requirements.txt"
            req_sanitized = source_dir / "_requirements_sanitized.txt"

            req_content = (
                "./dist/qpth-0.0.18-py3-none-any.whl\n"
                "qpth @ file:///home/user/dist/qpth-0.0.18-py3-none-any.whl\n"
                "numpy==1.21.0\n"
            )
            req_txt.write_text(req_content, encoding="utf-8")
            req_sanitized.write_text(req_content, encoding="utf-8")

            output_zip = tmp_path / "wheelhouse.zip"
            ret = module.cmd_pack_wheelhouse_zip(str(source_dir), str(output_zip), "store")
            self.assertEqual(ret, 0)

            # Check that the files on disk inside source_dir were sanitized
            expected_sanitized_content = (
                "qpth==0.0.18\n"
                "qpth==0.0.18\n"
                "numpy==1.21.0\n"
            )
            self.assertEqual(req_txt.read_text(encoding="utf-8"), expected_sanitized_content)
            self.assertEqual(req_sanitized.read_text(encoding="utf-8"), expected_sanitized_content)

            # Check zip file contents
            with zipfile.ZipFile(output_zip, "r") as zf:
                self.assertIn("_requirements.txt", zf.namelist())
                self.assertIn("_requirements_sanitized.txt", zf.namelist())
                self.assertEqual(
                    zf.read("_requirements.txt").decode("utf-8"),
                    expected_sanitized_content,
                )


if __name__ == "__main__":
    unittest.main()
