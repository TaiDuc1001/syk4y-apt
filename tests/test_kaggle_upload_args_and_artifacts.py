import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STRING_UTILS_SH = REPO_ROOT / "syk4y-cli-lib" / "string_utils.sh"
ARGS_SH = REPO_ROOT / "syk4y-cli-lib" / "kaggle_upload_args.sh"
ARTIFACTS_SH = REPO_ROOT / "syk4y-cli-lib" / "kaggle_upload_artifacts.sh"
TRANSFER_SH = REPO_ROOT / "syk4y-cli-lib" / "kaggle_upload_transfer.sh"


def run_bash(script: str, env=None):
    return subprocess.run(
        ["bash", "-lc", script],
        cwd=REPO_ROOT,
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
        check=False,
    )


class KaggleUploadArgsAndArtifactsTests(unittest.TestCase):
    def test_parse_args_collects_unique_artifact_filters(self):
        script = f"""
set -euo pipefail
source "{STRING_UTILS_SH}"
source "{ARGS_SH}"
kaggle_upload_parse_args datasets datasets wheelhouse
printf '%s\\n' "${{ARTIFACT_FILTER_IDS[@]}}"
"""
        proc = run_bash(script)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip().splitlines(), ["datasets", "wheelhouse"])

    def test_parse_args_rejects_slug_collision(self):
        script = f"""
set -euo pipefail
source "{STRING_UTILS_SH}"
source "{ARGS_SH}"
kaggle_upload_parse_args "data.sets" "data-sets"
"""
        proc = run_bash(script)
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("artifact slug collision", proc.stderr)

    def test_prepare_context_maps_store_dir_mode_to_zip_with_store_packing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()

            script = f"""
set -euo pipefail
source "{STRING_UTILS_SH}"
source "{ARGS_SH}"
kaggle_upload_parse_args --repo-root "$REPO_DIR"
kaggle_upload_prepare_context
echo "DIR_MODE=$DIR_MODE"
echo "ARTIFACT_ZIP_MODE=$ARTIFACT_ZIP_MODE"
"""
            proc = run_bash(
                script,
                env={"REPO_DIR": str(repo), "KAGGLE_DIR_MODE": "store"},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("DIR_MODE=zip", proc.stdout)
            self.assertIn("ARTIFACT_ZIP_MODE=store", proc.stdout)

    def test_upload_single_artifact_packs_store_but_uploads_as_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_dir = tmp_path / "models"
            source_dir.mkdir()
            (source_dir / "model.bin").write_text("weights\n", encoding="utf-8")

            upload_root = tmp_path / "kaggle_upload"
            metadata_dir = upload_root / "repo-models"
            metadata_dir.mkdir(parents=True)
            (metadata_dir / "dataset-metadata.json").write_text(
                '{"id":"owner/repo-models"}\n',
                encoding="utf-8",
            )

            log_path = tmp_path / "calls.log"
            fake_python = tmp_path / "fake-python.sh"
            fake_python.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == *"/kaggle_upload_py_cli.py" ]]; then
  case "${2:-}" in
    pack-artifact-dir-zip)
      printf 'PACK_MODE=%s\\n' "$5" >> "$CALL_LOG"
      printf 'zip-data\\n' > "$4"
      ;;
    kaggle-resume-dir)
      printf '%s\\n' "$TMP_ROOT/resume"
      ;;
    kaggle-resume-marker)
      printf '%s\\n' "$TMP_ROOT/resume/marker.json"
      ;;
    *)
      echo "unexpected helper command: ${2:-}" >&2
      exit 2
      ;;
  esac
  exit 0
fi
echo "unexpected python invocation: $*" >&2
exit 2
""",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            fake_kaggle = tmp_path / "fake-kaggle.sh"
            fake_kaggle.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
prev=""
for arg in "$@"; do
  if [[ "$prev" == "-r" ]]; then
    printf 'KAGGLE_DIR_MODE=%s\\n' "$arg" >> "$CALL_LOG"
  fi
  prev="$arg"
done
exit 0
""",
                encoding="utf-8",
            )
            fake_kaggle.chmod(0o755)

            zip_sh = REPO_ROOT / "syk4y-cli-lib" / "kaggle_zip.sh"

            script = f"""
set -euo pipefail
SCRIPT_DIR="{REPO_ROOT}"
REPO_ROOT="$REPO_DIR"
KAGGLE_UPLOAD_ROOT="$UPLOAD_ROOT_ENV"
PYTHON_BIN="$FAKE_PY"
DIR_MODE="zip"
ARTIFACT_ZIP_MODE="store"
FORCE_UPLOAD=0
VERSION_MESSAGE="test"
KAGGLE_CMD=("$FAKE_KAGGLE")

source "{TRANSFER_SH}"
source "{zip_sh}"

syk4y_resolve_python_bin_or_die() {{ printf '%s\\n' "$FAKE_PY"; }}
ensure_kaggle_upload_prereqs() {{ :; }}
resolve_initialized_artifacts() {{
  ARTIFACT_IDS=("models")
  ALL_ARTIFACT_IDS=("models")
}}
artifact_source_path() {{ printf '%s\\n' "$SOURCE_DIR"; }}
artifact_metadata_file() {{ printf '%s\\n' "$METADATA_FILE"; }}
artifact_item_name() {{ printf '%s\\n' "models"; }}
fingerprint_path() {{ printf '%s\\n' "myfingerprint123"; }}
syk4y_ensure_temp_dir_gitignore() {{ :; }}

kaggle_zip --repo-root "$REPO_DIR"
"""
            proc = run_bash(
                script,
                env={
                    "CALL_LOG": str(log_path),
                    "FAKE_PY": str(fake_python),
                    "FAKE_KAGGLE": str(fake_kaggle),
                    "SOURCE_DIR": str(source_dir),
                    "METADATA_FILE": str(dataset_dir / "dataset-metadata.json"),
                    "UPLOAD_ROOT_ENV": str(upload_root),
                    "TMP_ROOT": str(tmp_path),
                    "REPO_DIR": str(tmp_path),
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            calls = log_path.read_text(encoding="utf-8")
            self.assertIn("PACK_MODE=store", calls)

    def test_resolve_initialized_artifacts_filters_selected_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload_root = Path(tmp) / "kaggle_upload"
            ds_dir = upload_root / "repo-datasets"
            model_dir = upload_root / "repo-models"
            ds_dir.mkdir(parents=True)
            model_dir.mkdir(parents=True)
            (ds_dir / "dataset-metadata.json").write_text(
                '{"id":"owner/repo-datasets","syk4y_source":"datasets","syk4y_item_name":"datasets"}\n',
                encoding="utf-8",
            )
            (model_dir / "dataset-metadata.json").write_text(
                '{"id":"owner/repo-models","syk4y_source":"models","syk4y_item_name":"models"}\n',
                encoding="utf-8",
            )

            script = f"""
set -euo pipefail
SCRIPT_DIR="{REPO_ROOT}"
PYTHON_BIN="python3"
BASE_DATASET_SLUG="repo"
UPLOAD_ROOT="$UPLOAD_ROOT_ENV"
ARTIFACT_FILTER_IDS=("datasets")
declare -A ARTIFACT_SOURCE_SPEC
declare -A ARTIFACT_ITEM_NAMES
source "{ARTIFACTS_SH}"
resolve_initialized_artifacts
echo "IDS=${{ARTIFACT_IDS[*]}}"
echo "ALL=${{ALL_ARTIFACT_IDS[*]}}"
"""
            proc = run_bash(script, env={"UPLOAD_ROOT_ENV": str(upload_root)})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("IDS=datasets", proc.stdout)
            self.assertIn("ALL=datasets models", proc.stdout)

    def test_resolve_initialized_artifacts_fails_for_missing_selected_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload_root = Path(tmp) / "kaggle_upload"
            ds_dir = upload_root / "repo-datasets"
            ds_dir.mkdir(parents=True)
            (ds_dir / "dataset-metadata.json").write_text(
                '{"id":"owner/repo-datasets"}\n',
                encoding="utf-8",
            )

            script = f"""
set -euo pipefail
SCRIPT_DIR="{REPO_ROOT}"
PYTHON_BIN="python3"
BASE_DATASET_SLUG="repo"
UPLOAD_ROOT="$UPLOAD_ROOT_ENV"
ARTIFACT_FILTER_IDS=("models")
declare -A ARTIFACT_SOURCE_SPEC
declare -A ARTIFACT_ITEM_NAMES
source "{ARTIFACTS_SH}"
resolve_initialized_artifacts
"""
            proc = run_bash(script, env={"UPLOAD_ROOT_ENV": str(upload_root)})
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("requested artifact(s) were not initialized", proc.stderr)

    def test_remote_missing_expected_artifacts_handles_csv_quotes_and_crlf(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_kaggle = tmp_path / "fake-kaggle.sh"
            fake_kaggle.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'name,size,creationDate\\r\\n\"wheelhouse.zip\",42,2026-01-01\\r\\n'\n",
                encoding="utf-8",
            )
            fake_kaggle.chmod(0o755)

            script = f"""
set -euo pipefail
SCRIPT_DIR="{REPO_ROOT}"
PYTHON_BIN="python3"
KAGGLE_CMD=("$FAKE_KAGGLE")
source "{ARTIFACTS_SH}"
missing="$(remote_missing_expected_artifacts "owner/repo-wheelhouse" "wheelhouse" "wheelhouse.zip")"
echo "MISSING=$missing"
"""
            proc = run_bash(script, env={"FAKE_KAGGLE": str(fake_kaggle)})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("MISSING=", proc.stdout)
            self.assertNotIn("MISSING=wheelhouse.zip", proc.stdout)

    def test_remote_missing_expected_artifacts_returns_missing_when_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_kaggle = tmp_path / "fake-kaggle.sh"
            fake_kaggle.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'name,size,creationDate\\nother.zip,42,2026-01-01\\n'\n",
                encoding="utf-8",
            )
            fake_kaggle.chmod(0o755)

            script = f"""
set -euo pipefail
SCRIPT_DIR="{REPO_ROOT}"
PYTHON_BIN="python3"
KAGGLE_CMD=("$FAKE_KAGGLE")
source "{ARTIFACTS_SH}"
missing="$(remote_missing_expected_artifacts "owner/repo-wheelhouse" "wheelhouse" "wheelhouse.zip")"
echo "MISSING=$missing"
"""
            proc = run_bash(script, env={"FAKE_KAGGLE": str(fake_kaggle)})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("MISSING=wheelhouse.zip", proc.stdout)


if __name__ == "__main__":
    unittest.main()
