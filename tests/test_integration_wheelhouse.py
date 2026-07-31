import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYK4Y_BIN = REPO_ROOT / "syk4y"


class IntegrationWheelhouseTests(unittest.TestCase):
    def run_syk4y(self, args, cwd, env=None):
        cmd = [str(SYK4Y_BIN)] + args
        run_env = {**os.environ}
        if env:
            run_env.update(env)
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=run_env,
            capture_output=True,
            text=True,
        )

    def test_native_build(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "add", "six"], cwd=tmp_path, check=True)

            res = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"stdout: {res.stdout}\nstderr: {res.stderr}")

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)

            with zipfile.ZipFile(zip_paths[0], "r") as zf:
                namelist = zf.namelist()
                six_wheels = [name for name in namelist if "six-" in name and name.endswith(".whl")]
                self.assertEqual(len(six_wheels), 1, f"Expected exactly 1 six wheel, got: {six_wheels}")
                self.assertIn("_requirements.txt", namelist)

                reqs_content = zf.read("_requirements.txt").decode("utf-8")
                self.assertIn("six==", reqs_content)

    def test_gpu_and_custom_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            pyproject_content = """
[project]
name = "gpu-test"
version = "0.1.0"
dependencies = [
    "six",
]
[[tool.uv.index]]
name = "pypi-custom"
url = "https://pypi.org/simple"
explicit = true

[tool.uv.sources]
six = { index = "pypi-custom" }
"""
            (tmp_path / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")
            subprocess.run(["uv", "lock"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "venv"], cwd=tmp_path, check=True)

            res = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"stdout: {res.stdout}\nstderr: {res.stderr}")

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)

            with zipfile.ZipFile(zip_paths[0], "r") as zf:
                namelist = zf.namelist()
                six_wheels = [name for name in namelist if "six-" in name]
                self.assertEqual(len(six_wheels), 1, f"Expected six wheel, got: {namelist}")

    def test_cross_arch_build(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "add", "six", "openai-clip==1.0.1"], cwd=tmp_path, check=True)

            # Target x86_64 to trigger Docker build
            res = self.run_syk4y(["init", "wheelhouse", "--wheel-arch", "amd64"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"stdout: {res.stdout}\nstderr: {res.stderr}")

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)

            with zipfile.ZipFile(zip_paths[0], "r") as zf:
                namelist = zf.namelist()
                six_wheels = [name for name in namelist if "six-" in name]
                clip_wheels = [name for name in namelist if "openai_clip" in name]
                self.assertEqual(len(six_wheels), 1, f"six wheel missing (likely deleted by container pruning): {namelist}")
                self.assertEqual(len(clip_wheels), 1, f"openai-clip wheel missing: {namelist}")

    def test_pruning_and_cache_reuse(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "add", "certifi==2022.12.7", "six"], cwd=tmp_path, check=True)

            # Build 1: certifi==2022.12.7
            res = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"res1: {res.stdout}\n{res.stderr}")

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)
            with zipfile.ZipFile(zip_paths[0], "r") as zf:
                namelist = zf.namelist()
                self.assertTrue(any("certifi-2022.12.7" in name for name in namelist))
                self.assertTrue(any("six-" in name for name in namelist))

            # Build 2: upgrade certifi to 2026.2.25
            subprocess.run(["uv", "add", "certifi==2026.2.25"], cwd=tmp_path, check=True)

            res = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"res2: {res.stdout}\n{res.stderr}")

            with zipfile.ZipFile(zip_paths[0], "r") as zf:
                namelist = zf.namelist()
                self.assertTrue(any("certifi-2026.2.25" in name for name in namelist), f"New version missing: {namelist}")
                self.assertFalse(any("certifi-2022.12.7" in name for name in namelist), f"Old version not pruned: {namelist}")
                self.assertTrue(any("six-" in name for name in namelist), f"Cached unchanged wheel pruned: {namelist}")

    def test_fallback_to_pip_freeze(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            subprocess.run(["uv", "venv"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "pip", "install", "six", "--python", ".venv/bin/python"], cwd=tmp_path, check=True)

            res = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"stdout: {res.stdout}\nstderr: {res.stderr}")

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)
            with zipfile.ZipFile(zip_paths[0], "r") as zf:
                self.assertTrue(any("six-" in name for name in zf.namelist()))

    def test_build_failure_on_missing_package(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "add", "six"], cwd=tmp_path, check=True)
            
            # Edit uv.lock to refer to a non-existent package
            lock_file = tmp_path / "uv.lock"
            lock_content = lock_file.read_text(encoding="utf-8")
            lock_content = lock_content.replace('name = "six"', 'name = "non-existent-package-abc"')
            lock_file.write_text(lock_content, encoding="utf-8")

            res = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path, env={"WHEEL_FAIL_ON_MISSING": "1"})
            self.assertNotEqual(res.returncode, 0)

    def test_incremental_build_skipped_when_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "add", "six"], cwd=tmp_path, check=True)

            res1 = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res1.returncode, 0)
            self.assertNotIn("wheelhouse.zip is up-to-date", res1.stdout)

            res2 = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res2.returncode, 0)
            self.assertIn("wheelhouse.zip is up-to-date", res2.stdout)

    def test_source_package_cache_reuse(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "add", "openai-clip==1.0.1"], cwd=tmp_path, check=True)

            # Build 1: compile from source
            res1 = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res1.returncode, 0, f"res1: {res1.stdout}\n{res1.stderr}")

            # Delete zip and state file to force build, but keep build_dir cache
            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            for p in zip_paths:
                p.unlink()
            for p in tmp_path.glob(".syk4y-temp/**/.upload-state.json"):
                p.unlink()

            # Build 2: should use --find-links
            res2 = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res2.returncode, 0, f"res2: {res2.stdout}\n{res2.stderr}")
            self.assertNotIn("Building wheels for collected packages", res2.stdout)
            self.assertNotIn("Building wheel for openai-clip", res2.stdout)

    def test_local_whl_file_in_requirements(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)

            whl_file = tmp_path / "mylocalpkg-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(whl_file, "w") as zf:
                zf.writestr("mylocalpkg/__init__.py", "")
                zf.writestr("mylocalpkg-0.1.0.dist-info/METADATA",
                            "Metadata-Version: 2.1\nName: mylocalpkg\nVersion: 0.1.0\n")
                zf.writestr("mylocalpkg-0.1.0.dist-info/WHEEL",
                            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
                zf.writestr("mylocalpkg-0.1.0.dist-info/RECORD", "")

            pyproject_content = f"""
[project]
name = "local-wheel-test"
version = "0.1.0"
dependencies = [
    "mylocalpkg @ file://{whl_file.as_posix()}",
]
"""
            (tmp_path / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")
            subprocess.run(["uv", "lock"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "venv"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "pip", "install", "--python", ".venv/bin/python", "-e", "."], cwd=tmp_path, check=True)

            res = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"stdout: {res.stdout}\nstderr: {res.stderr}")

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)
            with zipfile.ZipFile(zip_paths[0], "r") as zf:
                self.assertIn("mylocalpkg-0.1.0-py3-none-any.whl", zf.namelist())

    def test_platform_marker_filtering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            pyproject_content = """
[project]
name = "marker-test"
version = "0.1.0"
dependencies = [
    "six",
    "colorama ; sys_platform == 'win32'",
]
"""
            (tmp_path / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")
            subprocess.run(["uv", "lock"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "venv"], cwd=tmp_path, check=True)

            res = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"stdout: {res.stdout}\nstderr: {res.stderr}")

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)
            with zipfile.ZipFile(zip_paths[0], "r") as zf:
                namelist = zf.namelist()
                self.assertTrue(any("six-" in name for name in namelist))
                self.assertFalse(any("colorama-" in name for name in namelist), f"colorama was not filtered out: {namelist}")
