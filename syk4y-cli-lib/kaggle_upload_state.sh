# State helpers for `syk4y kaggle upload`.

# shellcheck source=/dev/null
source "${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}/syk4y-cli-lib/fs_utils.sh"

read_state_value() {
  local key="$1"
  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" read-state-value "$STATE_FILE" "$key"
}

write_state_file() {
  local state_tmp state_tsv wheelhouse_input_hash temp_dir
  temp_dir="${REPO_ROOT:-$(pwd)}/.syk4y-temp"
  mkdir -p "$temp_dir" 2>/dev/null || temp_dir="${TMPDIR:-/tmp}"
  state_tmp="$(syk4y_mktemp "$temp_dir" "state-tmp")"
  state_tsv="$(syk4y_mktemp "$temp_dir" "state-tsv")"

  if [[ -n "${WHEELHOUSE_INPUT_HASH:-}" ]]; then
    wheelhouse_input_hash="$WHEELHOUSE_INPUT_HASH"
  else
    wheelhouse_input_hash="$(read_state_value "$WHEELHOUSE_INPUT_KEY")"
  fi
  printf '%s\t%s\n' "$WHEELHOUSE_INPUT_KEY" "$wheelhouse_input_hash" >> "$state_tsv"

  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" write-state-file "$state_tmp" "$state_tsv" "$STATE_FILE"
  rm -f "$state_tsv"
  mv -f "$state_tmp" "$STATE_FILE"
}
