import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SYK4Y_GEN = REPO_ROOT / "syk4y-gen"
SYK4Y_INIT = REPO_ROOT / "syk4y-init"


def run_cmd(args, cwd):
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


class GitignoreAutofillTests(unittest.TestCase):
    def test_gen_adds_kaggle_upload_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            run_cmd(["git", "init", "-q"], cwd=repo)
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            (repo / ".gitignore").write_text("dist/\n", encoding="utf-8")

            first = run_cmd([str(SYK4Y_GEN), "--repo-root", str(repo)], cwd=REPO_ROOT)
            self.assertEqual(first.returncode, 0, first.stderr)

            second = run_cmd([str(SYK4Y_GEN), "--repo-root", str(repo)], cwd=REPO_ROOT)
            self.assertEqual(second.returncode, 0, second.stderr)

            content = (repo / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("kaggle_upload/\n", content)
            self.assertEqual(content.count("kaggle_upload/"), 1)

    def test_init_adds_kaggle_upload_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "artifacts").mkdir()

            result = run_cmd(
                [str(SYK4Y_INIT), "--repo-root", str(repo), "artifacts"],
                cwd=REPO_ROOT,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            gitignore_path = repo / ".gitignore"
            self.assertTrue(gitignore_path.exists())
            content = gitignore_path.read_text(encoding="utf-8")
            self.assertIn("kaggle_upload/\n", content)


if __name__ == "__main__":
    unittest.main()
