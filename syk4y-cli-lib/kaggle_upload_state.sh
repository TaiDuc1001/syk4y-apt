# State helpers for `syk4y kaggle upload`.

read_state_value() {
  local key="$1"
  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" read-state-value "$STATE_FILE" "$key"
}

write_state_file() {
  local state_tmp state_tsv wheelhouse_input_hash
  state_tmp="$(mktemp "/tmp/kaggle-upload-state.XXXXXX.json")"
  state_tsv="$(mktemp "/tmp/kaggle-upload-state.XXXXXX.tsv")"

  if [[ -n "$WHEELHOUSE_INPUT_HASH" ]]; then
    wheelhouse_input_hash="$WHEELHOUSE_INPUT_HASH"
  else
    wheelhouse_input_hash="$(read_state_value "$WHEELHOUSE_INPUT_KEY")"
  fi
  printf '%s\t%s\n' "$WHEELHOUSE_INPUT_KEY" "$wheelhouse_input_hash" >> "$state_tsv"

  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" write-state-file "$state_tmp" "$state_tsv"
  rm -f "$state_tsv"
  mv -f "$state_tmp" "$STATE_FILE"
}
