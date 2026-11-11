import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SYK4Y_GEN = REPO_ROOT / "syk4y-gen"
GEN_SNAPSHOT_CLI = REPO_ROOT / "syk4y-lib" / "gen_snapshot_cli.py"
TEMPLATES_DIR = REPO_ROOT / "templates"


def run_cmd(args, cwd, env=None):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env=merged_env,
    )


class SnapshotIgnoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        run_cmd(["git", "init", "-q"], cwd=self.repo)
        run_cmd(["git", "config", "user.name", "Test User"], cwd=self.repo)
        run_cmd(["git", "config", "user.email", "test@example.com"], cwd=self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_ignore_filters_redundant_files_without_warning_or_gitignore(self):
        """Default ignore automatically excludes CSVs, models, wandb, and evidence dirs without warning spam."""
        # 1. Valid source code files
        (self.repo / "train.py").write_text("print('training...')\n", encoding="utf-8")
        (self.repo / "config.yaml").write_text("model: yolov8\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# Wildfire Detection\n", encoding="utf-8")

        # 2. Large CSV files reported by user in prompt (> 1MB each)
        large_content = "col1,col2,col3\n" + ("val1,val2,val3\n" * 80000)  # ~1.2MB
        cfg_dir = self.repo / "configs" / "protocols"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "tfs_btrain_source_v1.csv").write_text(large_content, encoding="utf-8")

        res_dir = self.repo / "research" / "q1_next" / "20261109_R1"
        res_dir.mkdir(parents=True)
        (res_dir / "R1_COHORT_AUDIT.csv").write_text(large_content, encoding="utf-8")
        (res_dir / "R1_EXCEPTIONS.csv").write_text(large_content, encoding="utf-8")

        ev_dir = res_dir / "evidence" / "audit02"
        ev_dir.mkdir(parents=True)
        (ev_dir / "cohort.csv").write_text(large_content, encoding="utf-8")
        (ev_dir / "exceptions.csv").write_text(large_content, encoding="utf-8")
        (ev_dir / "notes.txt").write_text("audit note", encoding="utf-8")  # inside evidence/

        # 3. Model weights and caches
        ckpt_dir = self.repo / "checkpoints"
        ckpt_dir.mkdir()
        (ckpt_dir / "best.pt").write_bytes(b"dummy pytorch weights" * 100)

        wandb_dir = self.repo / "wandb" / "run-2026"
        wandb_dir.mkdir(parents=True)
        (wandb_dir / "events.out.tfevents.123").write_text("metrics", encoding="utf-8")

        # Run syk4y gen with NO .gitignore rules added by user
        res = run_cmd([str(SYK4Y_GEN), "--repo-root", str(self.repo)], cwd=self.repo)
        self.assertEqual(res.returncode, 0, f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")

        # Assert no large snapshot file warnings in stderr
        self.assertNotIn("skipping large snapshot file", res.stderr)
        self.assertNotIn("Warning:", res.stderr)

        # Assert stdout indicates auto-ignored files
        self.assertIn("Snapshot auto-ignored files:", res.stdout)
        self.assertIn("Snapshot script updated:", res.stdout)

        # Execute gen-full.py in a fresh directory to verify restored files
        restore_dir = Path(self.tmp.name) / "restore"
        restore_dir.mkdir()
        gen_full_script = self.repo / "kaggle_upload" / "gen-full.py"
        self.assertTrue(gen_full_script.is_file())

        run_restore = run_cmd([sys.executable, str(gen_full_script)], cwd=restore_dir)
        self.assertEqual(run_restore.returncode, 0, run_restore.stderr)

        restored_repo = restore_dir / "repo"
        # Source code is restored
        self.assertTrue((restored_repo / "train.py").is_file())
        self.assertTrue((restored_repo / "config.yaml").is_file())
        self.assertTrue((restored_repo / "README.md").is_file())

        # Redundant data, weights, evidence files are NOT restored
        self.assertFalse((restored_repo / "configs" / "protocols" / "tfs_btrain_source_v1.csv").exists())
        self.assertFalse((restored_repo / "research" / "q1_next" / "20261109_R1" / "R1_COHORT_AUDIT.csv").exists())
        self.assertFalse((restored_repo / "research" / "q1_next" / "20261109_R1" / "evidence").exists())
        self.assertFalse((restored_repo / "checkpoints").exists())
        self.assertFalse((restored_repo / "wandb").exists())

    def test_syk4yignore_custom_patterns_and_negation(self):
        """.syk4yignore allows custom ignore patterns and un-ignoring default patterns via '!'."""
        (self.repo / "app.py").write_text("print(1)\n", encoding="utf-8")
        (self.repo / "scratch.py").write_text("print('scratch')\n", encoding="utf-8")
        (self.repo / "small_classes.csv").write_text("id,name\n1,tree\n", encoding="utf-8")

        # Custom .syk4yignore: ignore scratch.py, but include small_classes.csv (overriding default *.csv ignore)
        syk4yignore = self.repo / ".syk4yignore"
        syk4yignore.write_text(
            "# Custom ignores without touching .gitignore\n"
            "scratch.py\n"
            "!small_classes.csv\n",
            encoding="utf-8",
        )

        res = run_cmd([str(SYK4Y_GEN), "--repo-root", str(self.repo)], cwd=self.repo)
        self.assertEqual(res.returncode, 0, res.stderr)

        restore_dir = Path(self.tmp.name) / "restore_ign"
        restore_dir.mkdir()
        gen_full_script = self.repo / "kaggle_upload" / "gen-full.py"
        run_restore = run_cmd([sys.executable, str(gen_full_script)], cwd=restore_dir)
        self.assertEqual(run_restore.returncode, 0, run_restore.stderr)

        restored_repo = restore_dir / "repo"
        self.assertTrue((restored_repo / "app.py").is_file())
        self.assertTrue((restored_repo / "small_classes.csv").is_file(), "small_classes.csv was un-ignored via !")
        self.assertFalse((restored_repo / "scratch.py").exists(), "scratch.py was excluded via .syk4yignore")

    def test_cli_exclude_flag(self):
        """CLI --exclude flag excludes matching patterns."""
        (self.repo / "main.py").write_text("print('main')\n", encoding="utf-8")
        (self.repo / "temp_dev.py").write_text("print('temp')\n", encoding="utf-8")

        res = run_cmd(
            [str(SYK4Y_GEN), "--repo-root", str(self.repo), "--exclude", "temp_*.py"],
            cwd=self.repo,
        )
        self.assertEqual(res.returncode, 0, res.stderr)

        restore_dir = Path(self.tmp.name) / "restore_cli"
        restore_dir.mkdir()
        gen_full_script = self.repo / "kaggle_upload" / "gen-full.py"
        run_restore = run_cmd([sys.executable, str(gen_full_script)], cwd=restore_dir)
        self.assertEqual(run_restore.returncode, 0, run_restore.stderr)

        restored_repo = restore_dir / "repo"
        self.assertTrue((restored_repo / "main.py").is_file())
        self.assertFalse((restored_repo / "temp_dev.py").exists(), "temp_dev.py excluded via --exclude")

    def test_no_default_ignore_flag(self):
        """CLI --no-default-ignore disables default ignore rules."""
        (self.repo / "main.py").write_text("print('main')\n", encoding="utf-8")
        (self.repo / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")

        res = run_cmd(
            [str(SYK4Y_GEN), "--repo-root", str(self.repo), "--no-default-ignore"],
            cwd=self.repo,
        )
        self.assertEqual(res.returncode, 0, res.stderr)

        restore_dir = Path(self.tmp.name) / "restore_no_def"
        restore_dir.mkdir()
        gen_full_script = self.repo / "kaggle_upload" / "gen-full.py"
        run_restore = run_cmd([sys.executable, str(gen_full_script)], cwd=restore_dir)
        self.assertEqual(run_restore.returncode, 0, run_restore.stderr)

        restored_repo = restore_dir / "repo"
        self.assertTrue((restored_repo / "main.py").is_file())
        self.assertTrue((restored_repo / "data.csv").is_file(), "data.csv included because defaults disabled")

    def test_max_size_and_allow_large_snapshot(self):
        """CLI --max-size and --allow-large-snapshot allow custom size boundaries."""
        (self.repo / "main.py").write_text("print('hello')\n" * 5000, encoding="utf-8")

        # Exceeds artificially low max-size limit
        res_fail = run_cmd(
            [str(SYK4Y_GEN), "--repo-root", str(self.repo), "--max-size", "100"],
            cwd=self.repo,
        )
        self.assertNotEqual(res_fail.returncode, 0)
        self.assertIn("generated snapshot script is too large", res_fail.stderr)

        # Passes with allow-large-snapshot
        res_pass = run_cmd(
            [str(SYK4Y_GEN), "--repo-root", str(self.repo), "--allow-large-snapshot"],
            cwd=self.repo,
        )
        self.assertEqual(res_pass.returncode, 0, res_pass.stderr)


if __name__ == "__main__":
    unittest.main()
