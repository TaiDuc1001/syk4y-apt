import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYK4Y_BIN = REPO_ROOT / "syk4y"
SYK4Y_UPDATE_BIN = REPO_ROOT / "syk4y-update"
PY_CLI = REPO_ROOT / "syk4y-lib" / "update_cli.py"

sys.path.insert(0, str(REPO_ROOT / "syk4y-lib"))
import update_cli  # noqa: E402


class Syk4yUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="syk4y-update-test-")
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

    def test_01_update_help_messages(self):
        """Both 'syk4y update --help' and 'syk4y-update --help' display usage."""
        p1 = self.run_cmd([str(SYK4Y_BIN), "update", "--help"])
        self.assertEqual(p1.returncode, 0, p1.stderr)
        self.assertIn("syk4y update - Check for and install updates to syk4y", p1.stdout)
        self.assertIn("--check", p1.stdout)
        self.assertIn("--force", p1.stdout)

        p2 = self.run_cmd([str(SYK4Y_UPDATE_BIN), "--help"])
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("syk4y update - Check for and install updates to syk4y", p2.stdout)

    def test_02_update_version_and_json(self):
        """'syk4y update --version' and 'syk4y --version' report version/commit info."""
        p1 = self.run_cmd([str(SYK4Y_BIN), "update", "--version"])
        self.assertEqual(p1.returncode, 0, p1.stderr)
        self.assertIn("syk4y (git commit", p1.stdout)

        p2 = self.run_cmd([str(SYK4Y_BIN), "--version"])
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("syk4y (git commit", p2.stdout)

        p3 = self.run_cmd([str(SYK4Y_UPDATE_BIN), "--version", "--json"])
        self.assertEqual(p3.returncode, 0, p3.stderr)
        data = json.loads(p3.stdout)
        self.assertEqual(data["install_type"], "git")
        self.assertIn("commit", data)
        self.assertIn("branch", data)

    def test_03_detect_install_types(self):
        """Detect installation type correctly across git, apt, macos, and standalone."""
        # Git dir
        git_dir = self.tmp_path / "git_inst"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(git_dir), check=True, capture_output=True)
        self.assertEqual(update_cli.detect_install_type(git_dir), "git")

        # APT path
        apt_dir = Path("/usr/lib/syk4y")
        self.assertEqual(update_cli.detect_install_type(apt_dir), "apt")

        # macOS PKG path
        macos_dir = Path("/usr/local/lib/syk4y")
        self.assertEqual(update_cli.detect_install_type(macos_dir), "macos_pkg")

        # Standalone
        standalone_dir = self.tmp_path / "standalone"
        standalone_dir.mkdir()
        self.assertEqual(update_cli.detect_install_type(standalone_dir), "standalone")

    def test_04_git_up_to_date_and_update_available_flow(self):
        """Full lifecycle of git-based update: check up-to-date, detect new commit, dry-run, and pull."""
        remote_repo = self.tmp_path / "upstream.git"
        subprocess.run(["git", "init", "--bare", str(remote_repo)], check=True, capture_output=True)

        # Create origin clone to populate main branch
        seed_repo = self.tmp_path / "seed"
        subprocess.run(["git", "clone", str(remote_repo), str(seed_repo)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(seed_repo), check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(seed_repo), check=True)

        (seed_repo / "syk4y").write_text("#!/bin/bash\necho v1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(seed_repo), check=True)
        subprocess.run(["git", "commit", "-m", "Initial v1"], cwd=str(seed_repo), check=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=str(seed_repo), check=True)

        # Clone target install dir
        local_install = self.tmp_path / "installed_syk4y"
        subprocess.run(["git", "clone", "-b", "main", str(remote_repo), str(local_install)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(local_install), check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(local_install), check=True)

        # 1. Check when up to date
        p_check = self.run_cmd([
            sys.executable,
            str(PY_CLI),
            "--install-dir",
            str(local_install),
            "--check",
            "--json",
        ])
        self.assertEqual(p_check.returncode, 0, p_check.stderr)
        data = json.loads(p_check.stdout)
        self.assertEqual(data["status"], "up_to_date")
        self.assertFalse(data["update_available"])

        # 2. Push an update to remote
        (seed_repo / "feature.txt").write_text("v2 content\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(seed_repo), check=True)
        subprocess.run(["git", "commit", "-m", "Release v2 with exciting features"], cwd=str(seed_repo), check=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=str(seed_repo), check=True)

        # 3. Check again -> should detect update available
        p_check2 = self.run_cmd([
            sys.executable,
            str(PY_CLI),
            "--install-dir",
            str(local_install),
            "--check",
        ])
        self.assertEqual(p_check2.returncode, 0, p_check2.stderr)
        self.assertIn("Update available for syk4y!", p_check2.stdout)
        self.assertIn("Release v2 with exciting features", p_check2.stdout)

        # 4. Dry-run
        p_dry = self.run_cmd([
            sys.executable,
            str(PY_CLI),
            "--install-dir",
            str(local_install),
            "--dry-run",
        ])
        self.assertEqual(p_dry.returncode, 0, p_dry.stderr)
        self.assertIn("[DRY-RUN] Would update syk4y", p_dry.stdout)
        self.assertFalse((local_install / "feature.txt").exists())

        # 5. Apply update
        p_update = self.run_cmd([
            sys.executable,
            str(PY_CLI),
            "--install-dir",
            str(local_install),
        ])
        self.assertEqual(p_update.returncode, 0, p_update.stderr)
        self.assertIn("Successfully updated syk4y", p_update.stdout)
        self.assertTrue((local_install / "feature.txt").is_file())
        self.assertEqual((local_install / "feature.txt").read_text(encoding="utf-8"), "v2 content\n")

        # 6. Check again -> up to date
        p_check3 = self.run_cmd([
            sys.executable,
            str(PY_CLI),
            "--install-dir",
            str(local_install),
            "--check",
        ])
        self.assertEqual(p_check3.returncode, 0, p_check3.stderr)
        self.assertIn("syk4y is up to date", p_check3.stdout)

    def test_05_git_dirty_tree_safety_and_force(self):
        """Dirty working tree blocks update unless --force is specified."""
        remote_repo = self.tmp_path / "upstream.git"
        subprocess.run(["git", "init", "--bare", str(remote_repo)], check=True, capture_output=True)

        seed_repo = self.tmp_path / "seed"
        subprocess.run(["git", "clone", str(remote_repo), str(seed_repo)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(seed_repo), check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(seed_repo), check=True)

        (seed_repo / "file.txt").write_text("v1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(seed_repo), check=True)
        subprocess.run(["git", "commit", "-m", "v1"], cwd=str(seed_repo), check=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=str(seed_repo), check=True)

        local_install = self.tmp_path / "installed_syk4y"
        subprocess.run(["git", "clone", "-b", "main", str(remote_repo), str(local_install)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(local_install), check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(local_install), check=True)

        # Upstream new commit
        (seed_repo / "file.txt").write_text("v2\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(seed_repo), check=True)
        subprocess.run(["git", "commit", "-m", "v2"], cwd=str(seed_repo), check=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=str(seed_repo), check=True)

        # Make local directory dirty
        (local_install / "file.txt").write_text("local uncommitted changes\n", encoding="utf-8")

        # Update without --force must fail
        p_fail = self.run_cmd([
            sys.executable,
            str(PY_CLI),
            "--install-dir",
            str(local_install),
        ])
        self.assertNotEqual(p_fail.returncode, 0)
        self.assertIn("Local modifications detected", p_fail.stderr + p_fail.stdout)
        self.assertIn("--force", p_fail.stderr + p_fail.stdout)

        # Update with --force must succeed
        p_force = self.run_cmd([
            sys.executable,
            str(PY_CLI),
            "--install-dir",
            str(local_install),
            "--force",
        ])
        self.assertEqual(p_force.returncode, 0, p_force.stderr)
        self.assertIn("Successfully updated syk4y", p_force.stdout)
        self.assertEqual((local_install / "file.txt").read_text(encoding="utf-8"), "v2\n")

    def test_06_package_instructions_output(self):
        """Package-manager installations provide clear and accurate commands."""
        # APT
        apt_dir = self.tmp_path / "usr_lib"
        apt_dir.mkdir()
        p_apt = self.run_cmd([
            sys.executable,
            str(PY_CLI),
            "--install-dir",
            str(apt_dir),
            "--check",
        ], env={"PATH": "/usr/bin:/bin"})
        ret = update_cli.handle_package_update("apt", apt_dir, check_only=True, json_mode=False)
        self.assertEqual(ret, 0)

        # Homebrew check
        ret_brew = update_cli.handle_package_update("homebrew", apt_dir, check_only=True, json_mode=False)
        self.assertEqual(ret_brew, 0)


if __name__ == "__main__":
    unittest.main()
