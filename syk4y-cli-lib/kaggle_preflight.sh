# Implement `syk4y kaggle preflight` and `syk4y kaggle check` to diagnose datasets before packaging or upload.
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

kaggle_preflight() (
  kaggle_upload_parse_args "$@"
  kaggle_upload_prepare_context

  PYTHON_BIN="$(syk4y_resolve_python_bin_or_die "$REPO_ROOT" "syk4y")"

  local any_failed=0
  local scanned_any=0

  # 1. First check if explicit targets are existing directories on disk
  if [[ "${#ARTIFACT_FILTERS[@]}" -gt 0 ]]; then
    for target in "${ARTIFACT_FILTERS[@]}"; do
      local target_dir="$target"
      if [[ ! -d "$target_dir" && -d "$REPO_ROOT/$target" ]]; then
        target_dir="$REPO_ROOT/$target"
      fi

      if [[ -d "$target_dir" ]]; then
        scanned_any=1
        echo "==> Running dataset preflight for target directory: $target_dir..."
        local preflight_cmd=("$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" preflight-dataset "$target_dir")
        if [[ -n "$REPORT_PATH" ]]; then
          preflight_cmd+=(--report "$REPORT_PATH")
        fi
        for pat in "${EXCLUDE_PATTERNS[@]}"; do
          preflight_cmd+=(--exclude "$pat")
        done

        if ! "${preflight_cmd[@]}"; then
          any_failed=1
        fi
      fi
    done
  fi

  # 2. If no direct directories matched, check initialized artifacts
  if [[ "$scanned_any" -eq 0 ]]; then
    if [[ -d "$UPLOAD_ROOT" ]]; then
      resolve_initialized_artifacts || return 1
      for artifact_id in "${ARTIFACT_IDS[@]}"; do
        if [[ "$artifact_id" == "wheelhouse" ]]; then
          continue
        fi

        local source_path
        source_path="$(artifact_source_path "$artifact_id")"
        if [[ -d "$source_path" ]]; then
          scanned_any=1
          echo "==> Running dataset preflight for artifact '$artifact_id' ($source_path)..."
          local preflight_cmd=("$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" preflight-dataset "$source_path")
          if [[ -n "$REPORT_PATH" ]]; then
            preflight_cmd+=(--report "$REPORT_PATH")
          fi
          for pat in "${EXCLUDE_PATTERNS[@]}"; do
            preflight_cmd+=(--exclude "$pat")
          done

          if ! "${preflight_cmd[@]}"; then
            any_failed=1
          fi
        fi
      done
    fi
  fi

  if [[ "$scanned_any" -eq 0 ]]; then
    echo "Error: no directory artifacts or directories found to preflight." >&2
    return 1
  fi

  if [[ "$any_failed" -ne 0 ]]; then
    echo "Preflight check FAILED for one or more dataset artifacts." >&2
    return 1
  fi

  echo "Preflight check PASSED for all dataset artifacts."
  return 0
)
