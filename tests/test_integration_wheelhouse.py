import contextlib
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYK4Y_BIN = REPO_ROOT / "syk4y"


@contextlib.contextmanager
def safe_temp_dir():
    # Create temp dir under REPO_ROOT/tests/tmp/ to ensure it's always in the workspace
    # and has standard write/read permissions
    tmp_parent = REPO_ROOT / "tests" / "tmp"
    tmp_parent.mkdir(parents=True, exist_ok=True)
    tmpdir = tempfile.mkdtemp(dir=tmp_parent)
    tmp_path = Path(tmpdir)
    try:
        yield tmp_path
    finally:
        # 1. Clean up root-owned files using Docker (fallback just in case)
        subprocess.run([
            "docker", "run", "--rm",
            "-v", f"{tmp_path.resolve()}:/workspace",
            "alpine", "rm", "-rf", "/workspace/.syk4y-temp", "/workspace/kaggle_upload", "/workspace/.venv"
        ], capture_output=True)
        # 2. Clean up using shutil.rmtree, ignoring permissions or locked file errors
        shutil.rmtree(tmp_path, ignore_errors=True)


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

    def verify_wheelhouse_zip_strict(self, zip_path, expected_packages):
        """
        expected_packages is a dict: {normalized_package_name: expected_version}
        Checks that:
        1. '_requirements.txt' exists in the zip.
        2. The exact set of wheels inside the zip matches expected_packages.keys() (1-to-1), ignoring pip/setuptools.
        3. The versions inside the wheels match the expected versions.
        4. Every package listed in '_requirements.txt' corresponds to exactly one of the expected wheels,
           taking platform markers (like win32) into account.
        """
        self.assertTrue(zip_path.is_file(), f"Zip file not found at {zip_path}")

        with zipfile.ZipFile(zip_path, "r") as zf:
            namelist = zf.namelist()
            self.assertIn("_requirements.txt", namelist, "Zip is missing '_requirements.txt'")

            # Parse wheels inside zip
            wheels_in_zip = {}
            for filename in namelist:
                if filename.endswith(".whl"):
                    parts = filename.split("-")
                    if len(parts) >= 2:
                        norm_name = parts[0].replace("_", "-").replace(".", "-").lower()
                        version = parts[1]
                        if norm_name not in wheels_in_zip:
                            wheels_in_zip[norm_name] = []
                        wheels_in_zip[norm_name].append(version)

            # Normalize expected package names
            normalized_expected = {
                name.replace("_", "-").replace(".", "-").lower(): ver 
                for name, ver in expected_packages.items()
            }

            IGNORE_PACKAGES = {"pip", "setuptools", "wheel", "distribute"}

            # Filter out packaging tools
            actual_names = {name for name in wheels_in_zip.keys() if name not in IGNORE_PACKAGES}
            expected_names = {name for name in normalized_expected.keys() if name not in IGNORE_PACKAGES}

            # 1. Verify exact 1-to-1 match of package names
            self.assertEqual(
                actual_names,
                expected_names,
                f"Package mismatch inside zip!\nActual wheels: {actual_names}\nExpected: {expected_names}"
            )

            # 2. Verify versions and uniqueness
            for name in expected_names:
                versions_found = wheels_in_zip[name]
                self.assertEqual(
                    len(versions_found),
                    1,
                    f"Expected exactly 1 wheel version for {name}, found: {versions_found}"
                )
                expected_ver = normalized_expected[name]
                if expected_ver is not None:
                    self.assertEqual(
                        versions_found[0],
                        expected_ver,
                        f"Version mismatch for {name}: expected {expected_ver}, got {versions_found[0]}"
                    )

            # 3. Verify _requirements.txt contents match the wheels list (handling markers)
            reqs_content = zf.read("_requirements.txt").decode("utf-8")
            req_packages = set()
            for line in reqs_content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Split off marker
                parts = line.split(";", 1)
                pkg_spec = parts[0].strip()
                marker_str = parts[1].strip() if len(parts) > 1 else ""

                if marker_str:
                    # Skip Windows-only packages since tests run on Linux
                    if "sys_platform == 'win32'" in marker_str or "sys_platform == 'win'" in marker_str:
                        continue
                    if "sys_platform == 'emscripten'" in marker_str:
                        continue

                # Parse package name from specifier
                # Handle editables: e.g. "-e ./mylocalsrcpkg" or "-e /path/to/pkg"
                if pkg_spec.startswith("-e "):
                    pkg_spec = pkg_spec[3:].strip()

                # Handle paths and URLs: e.g. "./mylocalsrcpkg", "file:///...", "/path/to/..."
                if "file://" in pkg_spec or pkg_spec.startswith("/") or pkg_spec.startswith("."):
                    # Extract the directory name at the end of the path
                    pkg_name = pkg_spec.split("/")[-1].split("\\")[-1].strip()
                elif "==" in pkg_spec:
                    pkg_name = pkg_spec.split("==", 1)[0].strip()
                elif "@" in pkg_spec:
                    pkg_name = pkg_spec.split("@")[0].strip()
                else:
                    pkg_name = pkg_spec.strip()

                norm_name = pkg_name.replace("_", "-").replace(".", "-").lower()
                if norm_name not in IGNORE_PACKAGES:
                    req_packages.add(norm_name)

            self.assertEqual(
                req_packages,
                expected_names,
                f"Mismatch in _requirements.txt vs expected packages!\nRequirements: {req_packages}\nExpected: {expected_names}"
            )

    def test_native_build(self):
        with safe_temp_dir() as tmp_path:
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "add", "six==1.17.0"], cwd=tmp_path, check=True)

            res = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"stdout: {res.stdout}\nstderr: {res.stderr}")

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)

            # Strict validation
            self.verify_wheelhouse_zip_strict(zip_paths[0], {"six": "1.17.0"})

    def test_gpu_and_custom_index(self):
        with safe_temp_dir() as tmp_path:
            pyproject_content = """
[project]
name = "gpu-test"
version = "0.1.0"
dependencies = [
    "six==1.17.0",
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

            # Strict validation
            self.verify_wheelhouse_zip_strict(zip_paths[0], {"six": "1.17.0"})

    def test_cross_arch_build(self):
        with safe_temp_dir() as tmp_path:
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "add", "ipaddr==2.2.0", "requests==2.28.1"], cwd=tmp_path, check=True)

            res = self.run_syk4y(["init", "wheelhouse", "--arch", "amd64"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"stdout: {res.stdout}\nstderr: {res.stderr}")

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)

            # Strict validation: verify exact set of resolved packages
            expected = {
                "ipaddr": "2.2.0",
                "requests": "2.28.1",
                "certifi": "2026.7.22",
                "charset-normalizer": "2.1.1",
                "idna": "3.18",
                "urllib3": "1.26.20",
            }
            self.verify_wheelhouse_zip_strict(zip_paths[0], expected)

    def test_deprecated_wheel_arch_warning(self):
        with safe_temp_dir() as tmp_path:
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "add", "six==1.17.0"], cwd=tmp_path, check=True)

            res = self.run_syk4y(["init", "wheelhouse", "--wheel-arch", "amd64"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"stdout: {res.stdout}\nstderr: {res.stderr}")
            self.assertIn("Warning: --wheel-arch is deprecated", res.stderr)

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)

    def test_pruning_and_cache_reuse(self):
        with safe_temp_dir() as tmp_path:
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "add", "certifi==2022.12.7", "six==1.17.0"], cwd=tmp_path, check=True)

            # Build 1: certifi==2022.12.7
            res = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"res1: {res.stdout}\n{res.stderr}")

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)
            self.verify_wheelhouse_zip_strict(zip_paths[0], {"six": "1.17.0", "certifi": "2022.12.7"})

            # Build 2: upgrade certifi to 2026.2.25
            subprocess.run(["uv", "add", "certifi==2026.2.25"], cwd=tmp_path, check=True)

            res = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"res2: {res.stdout}\n{res.stderr}")
            self.verify_wheelhouse_zip_strict(zip_paths[0], {"six": "1.17.0", "certifi": "2026.2.25"})

    def test_fallback_to_pip_freeze(self):
        with safe_temp_dir() as tmp_path:
            subprocess.run(["uv", "venv"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "pip", "install", "six==1.17.0", "--python", ".venv/bin/python"], cwd=tmp_path, check=True)

            res = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res.returncode, 0, f"stdout: {res.stdout}\nstderr: {res.stderr}")

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)
            self.verify_wheelhouse_zip_strict(zip_paths[0], {"six": "1.17.0"})


    def test_incremental_build_skipped_when_unchanged(self):
        with safe_temp_dir() as tmp_path:
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "add", "six==1.17.0"], cwd=tmp_path, check=True)

            res1 = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res1.returncode, 0)
            self.assertNotIn("wheelhouse.zip is up-to-date", res1.stdout)

            res2 = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res2.returncode, 0)
            self.assertIn("wheelhouse.zip is up-to-date", res2.stdout)
            
            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.verify_wheelhouse_zip_strict(zip_paths[0], {"six": "1.17.0"})

    def test_source_package_cache_reuse(self):
        with safe_temp_dir() as tmp_path:
            subprocess.run(["uv", "init", "--no-workspace", "--name", "app"], cwd=tmp_path, check=True)
            subprocess.run(["uv", "add", "ipaddr==2.2.0"], cwd=tmp_path, check=True)

            res1 = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res1.returncode, 0, f"res1: {res1.stdout}\n{res1.stderr}")

            zip_paths = list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))
            self.assertEqual(len(zip_paths), 1)
            
            expected = {
                "ipaddr": "2.2.0",
            }
            self.verify_wheelhouse_zip_strict(zip_paths[0], expected)

            # Delete zip and state file to force build, but keep build_dir cache
            for p in zip_paths:
                p.unlink()
            for p in tmp_path.glob(".syk4y-temp/**/.upload-state.json"):
                p.unlink()

            res2 = self.run_syk4y(["init", "wheelhouse"], cwd=tmp_path)
            self.assertEqual(res2.returncode, 0, f"res2: {res2.stdout}\n{res2.stderr}")
            self.assertNotIn("Building wheels for collected packages", res2.stdout)
            self.assertNotIn("Building wheel for ipaddr", res2.stdout)
            self.verify_wheelhouse_zip_strict(list(tmp_path.glob("kaggle_upload/**/wheelhouse.zip"))[0], expected)

    def test_local_whl_file_in_requirements(self):
        with safe_temp_dir() as tmp_path:
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
            self.verify_wheelhouse_zip_strict(zip_paths[0], {"mylocalpkg": "0.1.0"})

    def test_platform_marker_filtering(self):
        with safe_temp_dir() as tmp_path:
            pyproject_content = """
[project]
name = "marker-test"
version = "0.1.0"
dependencies = [
    "six==1.17.0",
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
            self.verify_wheelhouse_zip_strict(zip_paths[0], {"six": "1.17.0"})
