import os
import subprocess
import tempfile
import unittest
import zipfile
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
            self.assertIn("No uv.lock found", proc.stdout)

    def test_build_wheelhouse_prefers_uv_lock_and_copies_repo_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "bin"
            fake_python = fake_bin / "fake-python.sh"
            fake_uv = fake_bin / "uv"
            command_log = tmp_path / "commands.log"
            repo_dir = tmp_path / "repo"
            upload_root = tmp_path / "upload"
            wheels_dir = repo_dir / "wheels"
            fake_bin.mkdir()
            wheels_dir.mkdir(parents=True)
            upload_root.mkdir()
            (repo_dir / "uv.lock").write_text("version = 1\n", encoding="utf-8")
            (repo_dir / "pyproject.toml").write_text(
                '[project]\nname = "app"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            (wheels_dir / "localpkg-0.1.0-py3-none-any.whl").write_bytes(b"wheel")

            fake_uv.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  echo "uv 0.test"
  exit 0
fi
if [[ "${1:-}" == "export" ]]; then
  output_file=""
  prev=""
  for arg in "$@"; do
    if [[ "$prev" == "--output-file" ]]; then
      output_file="$arg"
      break
    fi
    prev="$arg"
  done
  printf 'uv-export\\n' >> "$COMMAND_LOG"
  printf 'localpkg @ file:///kaggle/working/wheels/localpkg-0.1.0-py3-none-any.whl\\n' > "$output_file"
  exit 0
fi
echo "unexpected uv invocation: $*" >&2
exit 2
""",
                encoding="utf-8",
            )
            fake_uv.chmod(0o755)

            fake_python.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-V" ]]; then
  echo "Python 3.12.0"
  exit 0
fi
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" && "${3:-}" == "freeze" ]]; then
  printf 'pip-freeze\\n' >> "$COMMAND_LOG"
  exit 2
fi
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" && "${3:-}" == "wheel" ]]; then
  wheel_dir=""
  prev=""
  for arg in "$@"; do
    if [[ "$prev" == "--wheel-dir" ]]; then
      wheel_dir="$arg"
    fi
    prev="$arg"
  done
  req="${!#}"
  printf 'pip-wheel|%s|%s\\n' "$PWD" "$req" >> "$COMMAND_LOG"
  mkdir -p "$wheel_dir"
  touch "$wheel_dir/localpkg-0.1.0-py3-none-any.whl"
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
PATH="$FAKE_BIN:$PATH"
SCRIPT_DIR="{REPO_ROOT}"
source "{WHEELHOUSE_SH}"

REPO_ROOT="$REPO_DIR"
PYTHON_BIN="python3"
WHEELHOUSE_PYTHON="$FAKE_PY"
WHEEL_JOBS=1
WHEEL_FAIL_ON_MISSING=0
WHEELHOUSE_ZIP_MODE="store"
WHEELHOUSE_DATASET_DIR="$UPLOAD_ROOT/repo-wheelhouse"
WHEELHOUSE_PATH="$WHEELHOUSE_DATASET_DIR/wheelhouse.zip"
WHEELHOUSE_INPUT_HASH=""

build_wheelhouse_if_needed ""
"""

            proc = run_bash(
                script,
                env={
                    "REPO_DIR": str(repo_dir),
                    "UPLOAD_ROOT": str(upload_root),
                    "FAKE_PY": str(fake_python),
                    "FAKE_BIN": str(fake_bin),
                    "COMMAND_LOG": str(command_log),
                },
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            commands = command_log.read_text(encoding="utf-8").splitlines()
            self.assertIn("uv-export", commands)
            self.assertNotIn("pip-freeze", commands)
            self.assertNotIn(
                f"pip-wheel|{repo_dir}|wheels/localpkg-0.1.0-py3-none-any.whl",
                commands,
            )
            self.assertIn(
                "Copying local wheel into wheelhouse: wheels/localpkg-0.1.0-py3-none-any.whl",
                proc.stdout,
            )
            wheelhouse_path = upload_root / "repo-wheelhouse" / "wheelhouse.zip"
            with zipfile.ZipFile(wheelhouse_path) as archive:
                self.assertIn(
                    "localpkg-0.1.0-py3-none-any.whl",
                    archive.namelist(),
                )

    def test_build_wheelhouse_does_not_fallback_when_uv_lock_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "bin"
            fake_python = fake_bin / "fake-python.sh"
            fake_uv = fake_bin / "uv"
            command_log = tmp_path / "commands.log"
            repo_dir = tmp_path / "repo"
            upload_root = tmp_path / "upload"
            fake_bin.mkdir()
            repo_dir.mkdir()
            upload_root.mkdir()
            (repo_dir / "uv.lock").write_text("version = 1\n", encoding="utf-8")

            fake_uv.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "export" ]]; then
  printf 'uv-export-failed\\n' >> "$COMMAND_LOG"
  exit 1
fi
echo "uv 0.test"
""",
                encoding="utf-8",
            )
            fake_uv.chmod(0o755)

            fake_python.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" && "${3:-}" == "freeze" ]]; then
  printf 'pip-freeze\\n' >> "$COMMAND_LOG"
  echo "alpha==1.0.0"
  exit 0
fi
echo "Python 3.12.0"
""",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            script = f"""
set -euo pipefail
PATH="$FAKE_BIN:$PATH"
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

build_wheelhouse_if_needed ""
"""

            proc = run_bash(
                script,
                env={
                    "REPO_DIR": str(repo_dir),
                    "UPLOAD_ROOT": str(upload_root),
                    "FAKE_PY": str(fake_python),
                    "FAKE_BIN": str(fake_bin),
                    "COMMAND_LOG": str(command_log),
                },
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Run 'uv lock'", proc.stderr)
            commands = command_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(commands, ["uv-export-failed"])

    def test_build_wheelhouse_delegates_to_docker_for_cross_arch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_docker = fake_bin / "docker"
            docker_log = tmp_path / "docker.log"
            
            # Mock docker binary
            fake_docker.write_text(
                f"""#!/usr/bin/env bash
echo "docker called with: $*" >> "{docker_log}"
if [[ "$1" == "image" && "$2" == "inspect" ]]; then
  exit 0 # Mock python:3.10-slim image is present locally
fi
exit 0
""",
                encoding="utf-8"
            )
            fake_docker.chmod(0o755)

            # Determine opposite arch
            import platform
            host_machine = platform.machine()
            if host_machine in ("arm64", "aarch64"):
                target_arch = "x86_64"
                expected_platform = "linux/amd64"
            else:
                target_arch = "aarch64"
                expected_platform = "linux/arm64"

            script = f"""
set -euo pipefail
PATH="$FAKE_BIN:$PATH"
SCRIPT_DIR="{REPO_ROOT}"
source "{WHEELHOUSE_SH}"

REPO_ROOT="$REPO_DIR"
PYTHON_BIN="python3"
WHEELHOUSE_PYTHON="python3"
WHEEL_JOBS=1
WHEEL_FAIL_ON_MISSING=0
WHEELHOUSE_ZIP_MODE="store"
WHEELHOUSE_DATASET_DIR="$UPLOAD_ROOT/repo-wheelhouse"
WHEELHOUSE_PATH="$WHEELHOUSE_DATASET_DIR/wheelhouse.zip"
WHEELHOUSE_INPUT_HASH=""
WHEEL_ARCH="{target_arch}"
UPLOAD_ROOT="$UPLOAD_ROOT"

build_wheelhouse_if_needed ""
"""
            repo_dir = tmp_path / "repo"
            repo_dir.mkdir()
            upload_root = tmp_path / "upload"
            upload_root.mkdir()

            proc = run_bash(
                script,
                env={
                    "REPO_DIR": str(repo_dir),
                    "UPLOAD_ROOT": str(upload_root),
                    "FAKE_BIN": str(fake_bin),
                },
            )

            self.assertEqual(proc.returncode, 0, f"script failed: {proc.stderr}")
            docker_calls = docker_log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any("image inspect python:3.10-slim" in call for call in docker_calls))
            self.assertTrue(any(f"run --rm --platform {expected_platform}" in call for call in docker_calls))
            self.assertTrue(any(":/syk4y-toolkit" in call for call in docker_calls))
            self.assertTrue(any("/syk4y-toolkit/syk4y-kaggle" in call for call in docker_calls))


if __name__ == "__main__":
    unittest.main()
