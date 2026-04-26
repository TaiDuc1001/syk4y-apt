import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WHEELHOUSE_SH = REPO_ROOT / "syk4y-cli-lib" / "kaggle_upload_wheelhouse.sh"


def run_bash(script: str, env=None):
    return subprocess.run(
        ["bash", "-lc", script],
        cwd=REPO_ROOT,
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
        check=False,
    )


class KaggleUploadWheelhouseTests(unittest.TestCase):
    def test_build_wheelhouse_does_not_leave_broken_return_trap(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_python = tmp_path / "fake-python.sh"
            repo_dir = tmp_path / "repo"
            upload_root = tmp_path / "upload"
            repo_dir.mkdir()
            upload_root.mkdir()

            fake_python.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-V" ]]; then
  echo "Python 3.11.0"
  exit 0
fi

if [[ "${1:-}" == "-m" && "${2:-}" == "pip" && "${3:-}" == "freeze" ]]; then
  echo "alpha==1.0.0"
  exit 0
fi

if [[ "${1:-}" == "-m" && "${2:-}" == "pip" && "${3:-}" == "wheel" ]]; then
  wheel_dir=""
  prev=""
  for arg in "$@"; do
    if [[ "$prev" == "--wheel-dir" ]]; then
      wheel_dir="$arg"
      break
    fi
    prev="$arg"
  done
  mkdir -p "$wheel_dir"
  touch "$wheel_dir/fakepkg-1.0.0-py3-none-any.whl"
  exit 0
fi

if [[ "${1:-}" == *"/kaggle_upload_py_cli.py" ]]; then
  case "${2:-}" in
    sanitize-wheelhouse-requirements)
      cp "$3" "$4"
      ;;
    pyproject-extra-indexes)
      ;;
    pack-wheelhouse-zip)
      printf 'zip-data\\n' > "$4"
      ;;
    *)
      echo "unexpected helper command: ${2:-}" >&2
      exit 2
      ;;
  esac
  exit 0
fi

echo "unexpected invocation: $*" >&2
exit 2
""",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            script = f"""
set -euo pipefail
SCRIPT_DIR="{REPO_ROOT}"
source "{WHEELHOUSE_SH}"

REPO_ROOT="$REPO_DIR"
PYTHON_BIN="$FAKE_PY"
WHEELHOUSE_PYTHON="$FAKE_PY"
WHEEL_JOBS=1
WHEEL_FAIL_ON_MISSING=0
WHEELHOUSE_ZIP_MODE="store"
WHEELHOUSE_DATASET_DIR="$UPLOAD_ROOT/repo-wheelhouse"
WHEELHOUSE_PATH="$WHEELHOUSE_DATASET_DIR/wheelhouse.zip"
WHEELHOUSE_INPUT_HASH=""

runner() {{
  build_wheelhouse_if_needed ""
}}
runner

post_return_probe() {{
  :
}}
post_return_probe

[[ -f "$WHEELHOUSE_PATH" ]]
"""

            proc = run_bash(
                script,
                env={
                    "REPO_DIR": str(repo_dir),
                    "UPLOAD_ROOT": str(upload_root),
                    "FAKE_PY": str(fake_python),
                },
            )

            # This should pass when RETURN trap handling is correct.
            self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
