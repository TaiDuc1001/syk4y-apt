import platform
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-macos-pkg.sh"


@unittest.skipUnless(platform.system() == "Darwin", "macOS package builds only on macOS")
class MacOSPackageTests(unittest.TestCase):
    def test_builds_pkg_with_runtime_payload_and_bash_guarded_launchers(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "dist"
            result = subprocess.run(
                [
                    str(BUILD_SCRIPT),
                    "--version",
                    "1.2.3",
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            package = output_dir / "syk4y-1.2.3-macos-universal.pkg"
            self.assertTrue(package.is_file(), result.stdout)

            payload = subprocess.run(
                ["pkgutil", "--payload-files", str(package)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(payload.returncode, 0, payload.stderr)
            payload_files = payload.stdout.splitlines()
            expected_suffixes = {
                "/usr/local/bin/syk4y",
                "/usr/local/bin/syk4y-init",
                "/usr/local/bin/syk4y-gen",
                "/usr/local/bin/syk4y-kaggle",
                "/usr/local/bin/syk4y-doctor",
                "/usr/local/bin/make-gen-full-repo.sh",
                "/usr/local/lib/syk4y/syk4y",
                "/usr/local/lib/syk4y/syk4y-cli-lib/kaggle_upload_args.sh",
                "/usr/local/lib/syk4y/syk4y-lib/kaggle_upload_py_cli.py",
                "/usr/local/lib/syk4y/templates/syk4y-main-usage.txt",
            }
            missing = {
                suffix
                for suffix in expected_suffixes
                if not any(path.endswith(suffix) for path in payload_files)
            }
            self.assertFalse(missing, f"Missing payload paths: {sorted(missing)}\n{payload.stdout}")

            expanded_dir = Path(tmp) / "expanded"
            expanded = subprocess.run(
                ["pkgutil", "--expand-full", str(package), str(expanded_dir)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(expanded.returncode, 0, expanded.stderr)
            launcher = next(expanded_dir.glob("**/usr/local/bin/syk4y"))
            launcher_text = launcher.read_text(encoding="utf-8")
            self.assertIn("/opt/homebrew/bin/bash", launcher_text)
            self.assertIn("/usr/local/bin/bash", launcher_text)
            self.assertIn("brew install bash", launcher_text)
            self.assertIn("/usr/local/lib/syk4y/syk4y", launcher_text)
            compat_launcher = next(expanded_dir.glob("**/usr/local/bin/make-gen-full-repo.sh"))
            self.assertIn("/usr/local/lib/syk4y/syk4y-gen", compat_launcher.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
