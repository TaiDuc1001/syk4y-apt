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
                (wheelhouse / "pkg.whl").write_bytes(b"wheel")
                module.cmd_pack_wheelhouse_zip(str(wheelhouse), str(tmp_path / "wheelhouse.zip"), "store")

                artifacts = tmp_path / "artifacts"
                artifacts.mkdir()
                (artifacts / "model.bin").write_bytes(b"model")
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


if __name__ == "__main__":
    unittest.main()
