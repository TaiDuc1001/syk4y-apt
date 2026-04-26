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
  local state_tmp state_tsv artifact_id artifact_key metadata_key artifact_fp metadata_fp wheelhouse_input_hash
  local -a state_artifact_ids
  state_tmp="$(mktemp "/tmp/kaggle-upload-state.XXXXXX.json")"
  state_tsv="$(mktemp "/tmp/kaggle-upload-state.XXXXXX.tsv")"

  if [[ "${#ALL_ARTIFACT_IDS[@]}" -gt 0 ]]; then
    state_artifact_ids=("${ALL_ARTIFACT_IDS[@]}")
  else
    state_artifact_ids=("${ARTIFACT_IDS[@]}")
  fi

  for artifact_id in "${state_artifact_ids[@]}"; do
    artifact_key="$(state_key_artifact_fp "$artifact_id")"
    metadata_key="$(state_key_metadata_fp "$artifact_id")"

    if [[ -n "${CURRENT_FP[$artifact_id]+x}" ]]; then
      artifact_fp="${CURRENT_FP[$artifact_id]}"
    else
      artifact_fp="$(read_state_value "$artifact_key")"
    fi

    if [[ -n "${CURRENT_META_FP[$artifact_id]+x}" ]]; then
      metadata_fp="${CURRENT_META_FP[$artifact_id]}"
    else
      metadata_fp="$(read_state_value "$metadata_key")"
    fi

    printf '%s\t%s\n' "$artifact_key" "$artifact_fp" >> "$state_tsv"
    printf '%s\t%s\n' "$metadata_key" "$metadata_fp" >> "$state_tsv"
  done

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
