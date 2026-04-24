# State and fingerprint helpers for `syk4y kaggle upload`.

state_key_artifact_fp() {
  local artifact_id="$1"
  printf 'artifact:%s\n' "$artifact_id"
}

state_key_metadata_fp() {
  local artifact_id="$1"
  printf 'metadata:%s\n' "$artifact_id"
}

fingerprint_path() {
  local target="$1"
  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" fingerprint-path "$target"
}

read_state_value() {
  local key="$1"
  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" read-state-value "$STATE_FILE" "$key"
}

write_state_file() {
  local state_tmp state_tsv artifact_id
  state_tmp="$(mktemp "/tmp/kaggle-upload-state.XXXXXX.json")"
  state_tsv="$(mktemp "/tmp/kaggle-upload-state.XXXXXX.tsv")"
  for artifact_id in "${ARTIFACT_IDS[@]}"; do
    printf '%s\t%s\n' "$(state_key_artifact_fp "$artifact_id")" "${CURRENT_FP[$artifact_id]}" >> "$state_tsv"
    printf '%s\t%s\n' "$(state_key_metadata_fp "$artifact_id")" "${CURRENT_META_FP[$artifact_id]}" >> "$state_tsv"
  done
  printf '%s\t%s\n' "$WHEELHOUSE_INPUT_KEY" "$WHEELHOUSE_INPUT_HASH" >> "$state_tsv"

  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" write-state-file "$state_tmp" "$state_tsv"
  rm -f "$state_tsv"
  mv -f "$state_tmp" "$STATE_FILE"
}
