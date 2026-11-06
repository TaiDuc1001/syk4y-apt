# Implement `syk4y kaggle validate` to validate artifact metadata and archives.
# This file is sourced in `syk4y-kaggle`.

# shellcheck source=/dev/null
source "${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}/syk4y-cli-lib/python_env.sh"
# shellcheck source=/dev/null
source "${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}/syk4y-cli-lib/kaggle_env.sh"
# shellcheck source=/dev/null
source "${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}/syk4y-cli-lib/string_utils.sh"
# shellcheck source=/dev/null
source "${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}/syk4y-cli-lib/kaggle_upload_args.sh"
# shellcheck source=/dev/null
source "${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}/syk4y-cli-lib/kaggle_upload_env.sh"
# shellcheck source=/dev/null
source "${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}/syk4y-cli-lib/kaggle_upload_state.sh"
# shellcheck source=/dev/null
source "${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}/syk4y-cli-lib/kaggle_upload_artifacts.sh"

kaggle_validate() (
  kaggle_upload_parse_args "$@"
  kaggle_upload_prepare_context

  PYTHON_BIN="$(syk4y_resolve_python_bin_or_die "$REPO_ROOT" "syk4y")"
  resolve_initialized_artifacts

  local artifact_id source_path metadata_file temp_dir cache_dir cache_file archive_to_validate
  temp_dir="$REPO_ROOT/.syk4y-temp"
  cache_dir="$temp_dir/kaggle-zip-cache"

  echo "Validating artifacts for Kaggle upload..."
  local any_failed=0

  for artifact_id in "${ARTIFACT_IDS[@]}"; do
    source_path="$(artifact_source_path "$artifact_id")"
    metadata_file="$(artifact_metadata_file "$artifact_id")"

    if [[ "$artifact_id" == "wheelhouse" ]]; then
      archive_to_validate="$(artifact_source_path "wheelhouse")"
    else
      archive_to_validate="$cache_dir/${artifact_id}.zip"
      # If cache zip does not exist yet, build it so it can be validated
      if [[ ! -f "$archive_to_validate" && -d "$source_path" ]]; then
        echo "Building ZIP archive for '$artifact_id' for validation..."
        mkdir -p "$cache_dir"
        if ! "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
          pack-artifact-dir-zip \
          "$source_path" \
          "$archive_to_validate" \
          "$ARTIFACT_ZIP_MODE"; then
          echo "ERROR: Failed to create ZIP archive for '$artifact_id'." >&2
          any_failed=1
          continue
        fi
      fi
    fi

    echo "==> Validating artifact: '$artifact_id'"
    if ! "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
      validate-artifact-archive \
      "$archive_to_validate" \
      "$metadata_file" \
      "$source_path"; then
      any_failed=1
    else
      echo "Artifact '$artifact_id': VALID"
    fi
  done

  if [[ "$any_failed" -eq 1 ]]; then
    echo "ERROR: Artifact validation failed for one or more artifacts." >&2
    return 1
  fi

  echo "All evaluated artifacts are VALID and ready for Kaggle upload."
  return 0
)
