import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_SH = REPO_ROOT / "syk4y-cli-lib" / "kaggle_upload_env.sh"


class KaggleUploadEnvTests(unittest.TestCase):
    def test_resolve_kaggle_cmd_uses_console_script_next_to_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "venv" / "bin"
            bin_dir.mkdir(parents=True)
            python_bin = bin_dir / "python"
            kaggle_bin = bin_dir / "kaggle"
            python_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            kaggle_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            python_bin.chmod(0o755)
            kaggle_bin.chmod(0o755)

            script = f"""
set -euo pipefail
source "{ENV_SH}"
PYTHON_BIN="$FAKE_PYTHON"
KAGGLE_CMD=()
resolve_kaggle_cmd
printf '%s\\n' "${{KAGGLE_CMD[@]}}"
"""
            proc = subprocess.run(
                ["bash", "-lc", script],
                cwd=REPO_ROOT,
                env={**os.environ, "FAKE_PYTHON": str(python_bin)},
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), str(kaggle_bin))


if __name__ == "__main__":
    unittest.main()
