# Implement `syk4y kaggle zip` to pre-zip non-wheelhouse artifacts.
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

kaggle_zip() (
  kaggle_upload_parse_args "$@"
  kaggle_upload_prepare_context

  PYTHON_BIN="$(syk4y_resolve_python_bin_or_die "$REPO_ROOT" "syk4y")"
  resolve_initialized_artifacts

  local artifact_id source_path temp_dir cache_dir cache_file
  temp_dir="$REPO_ROOT/.syk4y-temp"
  cache_dir="$temp_dir/kaggle-zip-cache"
  mkdir -p "$cache_dir"
  if declare -f syk4y_ensure_temp_dir_gitignore >/dev/null; then
    syk4y_ensure_temp_dir_gitignore "$REPO_ROOT"
  fi

  local force_args=()
  if [[ "$FORCE_UPLOAD" -eq 1 ]]; then
    force_args+=(--force)
  fi

  local any_zipped=0
  for artifact_id in "${ARTIFACT_IDS[@]}"; do
    if [[ "$artifact_id" == "wheelhouse" ]]; then
      # User request: "việc zip cho các artifact ko phải wheelhouse sẽ tách thành command syk4y kaggle zip"
      echo "Skipping 'wheelhouse' (wheelhouse zipping is managed automatically by other commands and not supported by 'syk4y kaggle zip')."
      continue
    fi

    source_path="$(artifact_source_path "$artifact_id")"
    if [[ -d "$source_path" && "$DIR_MODE" == "zip" ]]; then
      # Run preflight check before expensive zip creation
      local preflight_cmd=("$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" preflight-dataset "$source_path")
      if [[ -n "$REPORT_PATH" ]]; then
        preflight_cmd+=(--report "$REPORT_PATH")
      fi
      for pat in "${EXCLUDE_PATTERNS[@]}"; do
        preflight_cmd+=(--exclude "$pat")
      done
      if ! "${preflight_cmd[@]}"; then
        echo "Error: dataset preflight failed for artifact '$artifact_id'." >&2
        return 1
      fi

      cache_file="$cache_dir/${artifact_id}.zip"
      local check_cache_cmd=(
        "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py"
        check-zip-cache
        "$source_path"
        "$cache_file"
      )
      for pat in "${EXCLUDE_PATTERNS[@]}"; do
        check_cache_cmd+=(--exclude "$pat")
      done
      local cache_status
      cache_status="$("${check_cache_cmd[@]}" 2>/dev/null || echo "invalid")"

      if [[ "$FORCE_UPLOAD" -eq 0 && "$cache_status" == "valid" ]]; then
        echo "Cache valid: reusing $(basename "$cache_file")"
      else
        if [[ "$cache_status" == "changed" ]]; then
          echo "Source changed: rebuilding $(basename "$cache_file")"
        else
          echo "Cache invalid: rebuilding $(basename "$cache_file")"
        fi

        local pack_cmd=(
          "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py"
          pack-artifact-dir-zip
          "$source_path"
          "$cache_file"
          "$ARTIFACT_ZIP_MODE"
        )
        if [[ -n "$REPORT_PATH" ]]; then
          pack_cmd+=(--report "$REPORT_PATH")
        fi
        for pat in "${EXCLUDE_PATTERNS[@]}"; do
          pack_cmd+=(--exclude "$pat")
        done
        if [[ "$FORCE_UPLOAD" -eq 1 ]]; then
          pack_cmd+=(--force)
        fi

        if "${pack_cmd[@]}"; then
          any_zipped=1
        else
          echo "Error: failed to zip '$artifact_id'" >&2
          return 1
        fi
      fi
    fi
  done

  if [[ "$any_zipped" -eq 1 ]]; then
    echo "All artifacts zipped successfully."
  else
    echo "Nothing to zip (artifacts up-to-date or not configured to zip)."
  fi
)
