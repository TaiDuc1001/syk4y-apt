import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_SH = REPO_ROOT / "syk4y-cli-lib" / "kaggle_upload_state.sh"


def run_bash(script: str, env=None):
    return subprocess.run(
        ["bash", "-lc", script],
        cwd=REPO_ROOT,
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
        check=False,
    )


class KaggleUploadStateTests(unittest.TestCase):
    def test_write_state_file_preserves_wheelhouse_hash_when_not_provided(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / ".upload-state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "__wheelhouse_input__": "old-wheelhash",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            script = f"""
set -euo pipefail
SCRIPT_DIR="{REPO_ROOT}"
PYTHON_BIN="python3"
STATE_FILE="$STATE_FILE_ENV"
WHEELHOUSE_INPUT_KEY="__wheelhouse_input__"
WHEELHOUSE_INPUT_HASH=""
source "{STATE_SH}"
write_state_file
"""
            proc = run_bash(script, env={"STATE_FILE_ENV": str(state_file)})
            self.assertEqual(proc.returncode, 0, proc.stderr)

            data = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(data["__wheelhouse_input__"], "old-wheelhash")

    def test_write_state_file_overrides_wheelhouse_hash_when_provided(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / ".upload-state.json"
            state_file.write_text(
                json.dumps({"__wheelhouse_input__": "old-wheelhash"}) + "\n",
                encoding="utf-8",
            )

            script = f"""
set -euo pipefail
SCRIPT_DIR="{REPO_ROOT}"
PYTHON_BIN="python3"
STATE_FILE="$STATE_FILE_ENV"
WHEELHOUSE_INPUT_KEY="__wheelhouse_input__"
WHEELHOUSE_INPUT_HASH="new-wheelhash"
source "{STATE_SH}"
write_state_file
"""
            proc = run_bash(script, env={"STATE_FILE_ENV": str(state_file)})
            self.assertEqual(proc.returncode, 0, proc.stderr)

            data = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(data["__wheelhouse_input__"], "new-wheelhash")


if __name__ == "__main__":
    unittest.main()
