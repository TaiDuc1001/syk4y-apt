# Implement `syk4y kaggle zip` to pre-zip non-wheelhouse artifacts.
# This file is sourced in `syk4y-kaggle`.

# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/python_env.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/kaggle_env.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/string_utils.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/kaggle_upload_args.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/kaggle_upload_env.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/kaggle_upload_state.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/kaggle_upload_artifacts.sh"

kaggle_zip() (
  kaggle_upload_parse_args "$@"
  kaggle_upload_prepare_context

  PYTHON_BIN="$(syk4y_resolve_python_bin_or_die "$REPO_ROOT" "syk4y")"
  resolve_initialized_artifacts

  local artifact_id source_path temp_dir cache_dir fingerprint cache_file
  temp_dir="$REPO_ROOT/.syk4y-temp"
  cache_dir="$temp_dir/kaggle-zip-cache"
  mkdir -p "$cache_dir"
  if declare -f syk4y_ensure_temp_dir_gitignore >/dev/null; then
    syk4y_ensure_temp_dir_gitignore "$REPO_ROOT"
  fi

  local any_zipped=0
  for artifact_id in "${ARTIFACT_IDS[@]}"; do
    if [[ "$artifact_id" == "wheelhouse" ]]; then
      # User request: "việc zip cho các artifact ko phải wheelhouse sẽ tách thành command syk4y kaggle zip"
      continue
    fi

    source_path="$(artifact_source_path "$artifact_id")"
    if [[ -d "$source_path" && "$DIR_MODE" == "zip" ]]; then
      fingerprint="$(fingerprint_path "$source_path")"
      cache_file="$cache_dir/$fingerprint.zip"
      if [[ -f "$cache_file" ]]; then
        echo "Zip for '$artifact_id' is already up-to-date (fingerprint: $fingerprint)"
      else
        echo "Zipping '$artifact_id' (fingerprint: $fingerprint)..."
        local tmp_zip
        tmp_zip="$(mktemp "$cache_dir/tmp-zip.XXXXXX.zip")"
        if "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
          pack-artifact-dir-zip \
          "$source_path" \
          "$tmp_zip" \
          "$ARTIFACT_ZIP_MODE"; then
          mv "$tmp_zip" "$cache_file"
          any_zipped=1
          ls -t "$cache_dir"/*.zip 2>/dev/null | tail -n +21 | xargs rm -f -- 2>/dev/null || true
        else
          rm -f "$tmp_zip"
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
