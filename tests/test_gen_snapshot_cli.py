import os
import py_compile
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GEN_SNAPSHOT_CLI = REPO_ROOT / "syk4y-lib" / "gen_snapshot_cli.py"
TEMPLATE_DIR = REPO_ROOT / "templates"


def run_cmd(args, cwd):
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


class GenSnapshotCliTests(unittest.TestCase):
    def test_generated_snapshot_has_valid_newlines_and_compiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()

            init = run_cmd(["git", "init", "-q"], cwd=repo)
            self.assertEqual(init.returncode, 0, init.stderr)

            (repo / "README.md").write_text("hello\n", encoding="utf-8")

            out_abs = repo / "gen-full.py"
            gen = run_cmd(
                ["python3", str(GEN_SNAPSHOT_CLI), str(repo), str(out_abs), str(TEMPLATE_DIR)],
                cwd=REPO_ROOT,
            )
            self.assertEqual(gen.returncode, 0, gen.stderr)
            self.assertTrue(out_abs.exists(), "generator did not create output file")

            generated = out_abs.read_text(encoding="utf-8")
            self.assertIn("PAYLOAD_B64 = ", generated)
            self.assertNotIn("\\n\\ngen_full()", generated)
            self.assertIn("\n\ngen_full()\n", generated)

            py_compile.compile(str(out_abs), doraise=True)

    def test_generated_snapshot_restores_files_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()

            init = run_cmd(["git", "init", "-q"], cwd=repo)
            self.assertEqual(init.returncode, 0, init.stderr)

            data_file = repo / "data.txt"
            data_file.write_text("payload\n", encoding="utf-8")

            script_file = repo / "run.sh"
            script_file.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
            script_file.chmod(0o755)

            link_file = repo / "data-link.txt"
            os.symlink("data.txt", link_file)

            out_abs = repo / "gen-full.py"
            gen = run_cmd(
                ["python3", str(GEN_SNAPSHOT_CLI), str(repo), str(out_abs), str(TEMPLATE_DIR)],
                cwd=REPO_ROOT,
            )
            self.assertEqual(gen.returncode, 0, gen.stderr)

            restore_dir = Path(tmp) / "restored"
            restored = run_cmd(
                ["python3", str(out_abs), str(restore_dir)],
                cwd=REPO_ROOT,
            )
            self.assertEqual(restored.returncode, 0, restored.stderr)

            self.assertEqual(
                (restore_dir / "data.txt").read_text(encoding="utf-8"),
                "payload\n",
            )
            self.assertEqual(
                (restore_dir / "run.sh").read_text(encoding="utf-8"),
                "#!/usr/bin/env bash\necho ok\n",
            )
            self.assertTrue((restore_dir / "data-link.txt").is_symlink())
            self.assertEqual(os.readlink(restore_dir / "data-link.txt"), "data.txt")

            restored_mode = stat.S_IMODE((restore_dir / "run.sh").stat().st_mode)
            self.assertEqual(restored_mode, 0o755)


if __name__ == "__main__":
    unittest.main()
