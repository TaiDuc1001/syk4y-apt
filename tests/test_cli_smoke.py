import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SYK4Y = REPO_ROOT / "syk4y"
SYK4Y_INIT = REPO_ROOT / "syk4y-init"
SYK4Y_GEN = REPO_ROOT / "syk4y-gen"


def run_cmd(args, cwd=REPO_ROOT):
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


class CliSmokeTests(unittest.TestCase):
    def test_syk4y_requires_command(self):
        proc = run_cmd([str(SYK4Y)])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Usage:", proc.stderr)

    def test_syk4y_rejects_unknown_command(self):
        proc = run_cmd([str(SYK4Y), "unknown"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Unknown command: unknown", proc.stderr)

    def test_syk4y_rejects_invalid_repo_root(self):
        proc = run_cmd([str(SYK4Y), "--repo-root", "/definitely/not/exist", "init", "datasets"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("invalid --repo-root directory", proc.stderr)

    def test_init_requires_at_least_one_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            proc = run_cmd([str(SYK4Y_INIT), "--repo-root", str(repo)])
            self.assertEqual(proc.returncode, 2)
            self.assertIn("init requires at least one artifact", proc.stderr)

    def test_gen_skip_gen_requires_artifact_when_no_snapshot_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            proc = run_cmd([str(SYK4Y_GEN), "--repo-root", str(repo), "--skip-gen"])
            self.assertEqual(proc.returncode, 2)
            self.assertIn("nothing to do", proc.stderr)


if __name__ == "__main__":
    unittest.main()
