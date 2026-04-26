# Upload transfer and high-level sync flow for `syk4y kaggle upload`.

clear_resume_markers() {
  local stage_dir="$1"
  local resume_dir
  resume_dir="$("$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" kaggle-resume-dir)"

  local f marker
  for f in "$stage_dir"/*; do
    if [[ -f "$f" ]]; then
      marker="$("$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" kaggle-resume-marker "$f" "$resume_dir")"
      rm -f "$marker"
    fi
  done
}

upload_single_artifact() {
  local artifact_id="$1"
  local dataset_ref="$2"
  local dataset_exists="$3"
  local local_changed="$4"
  local meta_changed="$5"
  local force_reason="$6"

  local item_name source_path metadata_file stage_dir upload_status
  item_name="$(artifact_item_name "$artifact_id")"
  source_path="$(artifact_source_path "$artifact_id")"
  metadata_file="$(artifact_metadata_file "$artifact_id")"
  stage_dir="$(mktemp -d "/tmp/kaggle-upload-stage.${artifact_id}.XXXXXX")"
  upload_status=0

  cp "$metadata_file" "$stage_dir/dataset-metadata.json" || upload_status=$?
  if [[ "$upload_status" -eq 0 ]]; then
    ln -sfn "$source_path" "$stage_dir/$item_name" || upload_status=$?
  fi
  if [[ "$upload_status" -eq 0 ]]; then
    clear_resume_markers "$stage_dir" || upload_status=$?
  fi

  if [[ "$upload_status" -eq 0 ]]; then
    if [[ "$dataset_exists" -eq 1 ]]; then
      if [[ -n "$force_reason" ]]; then
        echo "Forcing '$artifact_id' dataset update: $force_reason"
      elif [[ "$local_changed" -eq 1 ]]; then
        echo "Updating '$artifact_id' dataset. Changed artifact: $item_name"
      elif [[ "$meta_changed" -eq 1 ]]; then
        echo "Updating '$artifact_id' dataset metadata only."
      else
        echo "Updating '$artifact_id' dataset."
      fi
      "${KAGGLE_CMD[@]}" datasets version -p "$stage_dir" -m "${VERSION_MESSAGE} [$artifact_id]" -r "$DIR_MODE" || upload_status=$?
    else
      echo "Dataset '$dataset_ref' does not exist yet; creating with artifact '$item_name'."
      "${KAGGLE_CMD[@]}" datasets create -p "$stage_dir" -r "$DIR_MODE" || upload_status=$?
    fi
  fi

  if [[ "$upload_status" -eq 0 ]]; then
    echo "Uploaded artifact source path: $source_path"
  fi

  rm -rf -- "$stage_dir"
  return "$upload_status"
}

kaggle_upload_run_flow() {
  local artifact_id source_path metadata_file item_name
  local current_fp previous_fp current_meta_fp previous_meta_fp
  local local_changed meta_changed dataset_ref dataset_exists
  local force_reason remote_missing should_upload

  cd "$REPO_ROOT"

  PYTHON_BIN="$(syk4y_resolve_python_bin_or_die "$REPO_ROOT" "syk4y")"
  WHEELHOUSE_PYTHON="$(resolve_wheelhouse_python)"
  ensure_pip "$PYTHON_BIN"
  ensure_pip "$WHEELHOUSE_PYTHON"

  mkdir -p "$UPLOAD_ROOT"

  if [[ "$BUILD_WHEEL_ONLY" -eq 1 ]]; then
    local prev_wheelhouse_input
    prev_wheelhouse_input="$(read_state_value "$WHEELHOUSE_INPUT_KEY")"
    build_wheelhouse_if_needed "$prev_wheelhouse_input"
    if [[ ! -f "$WHEELHOUSE_PATH" ]]; then
      echo "Error: wheelhouse build completed but '$WHEELHOUSE_PATH' is missing." >&2
      exit 1
    fi
    echo "Build-wheel-only mode complete: $WHEELHOUSE_PATH"
    return
  fi

  ensure_kaggle_upload_prereqs
  resolve_initialized_artifacts
  verify_dataset_structure

  local state_exists=0
  if [[ -f "$STATE_FILE" ]]; then
    state_exists=1
  fi

  declare -A DATASET_REF
  declare -A DATASET_EXISTS
  declare -A LOCAL_CHANGED
  declare -A META_CHANGED
  declare -A FORCE_REASON
  declare -A SHOULD_UPLOAD

  for artifact_id in "${ARTIFACT_IDS[@]}"; do
    source_path="$(artifact_source_path "$artifact_id")"
    metadata_file="$(artifact_metadata_file "$artifact_id")"
    item_name="$(artifact_item_name "$artifact_id")"

    if [[ ! -e "$source_path" ]]; then
      echo "Error: missing artifact path '$source_path'" >&2
      exit 1
    fi

    current_fp="$(fingerprint_path "$source_path")"
    CURRENT_FP["$artifact_id"]="$current_fp"
    previous_fp="$(read_state_value "$(state_key_artifact_fp "$artifact_id")")"

    current_meta_fp="$(fingerprint_path "$metadata_file")"
    CURRENT_META_FP["$artifact_id"]="$current_meta_fp"
    previous_meta_fp="$(read_state_value "$(state_key_metadata_fp "$artifact_id")")"

    local_changed=0
    if [[ "$current_fp" != "$previous_fp" ]]; then
      local_changed=1
    fi

    meta_changed=0
    if [[ "$current_meta_fp" != "$previous_meta_fp" ]]; then
      meta_changed=1
    fi

    dataset_ref="$(extract_dataset_ref "$metadata_file")"
    if [[ -z "$dataset_ref" ]]; then
      echo "Error: '$metadata_file' missing 'id' field." >&2
      exit 1
    fi
    DATASET_REF["$artifact_id"]="$dataset_ref"

    dataset_exists=0
    if "${KAGGLE_CMD[@]}" datasets files -d "$dataset_ref" >/dev/null 2>&1; then
      dataset_exists=1
    fi
    DATASET_EXISTS["$artifact_id"]="$dataset_exists"

    force_reason=""
    if [[ "$FORCE_UPLOAD" == "1" ]]; then
      force_reason="KAGGLE_FORCE_UPLOAD=1"
    elif [[ "$dataset_exists" -eq 1 ]]; then
      remote_missing="$(remote_missing_expected_artifacts "$dataset_ref" "$artifact_id" "$item_name")"
      if [[ -n "$remote_missing" ]]; then
        force_reason="remote dataset missing expected artifacts: $remote_missing"
      fi
    fi

    # First run safety: if local state does not exist, baseline existing datasets.
    # This avoids uploading everything immediately when remote and local are already in sync.
    if [[ "$state_exists" -eq 0 ]] && [[ "$dataset_exists" -eq 1 ]] && [[ -z "$force_reason" ]] && [[ "$FORCE_UPLOAD" != "1" ]]; then
      local_changed=0
      meta_changed=0
    fi

    should_upload=0
    if [[ "$dataset_exists" -eq 0 ]] || [[ "$local_changed" -eq 1 ]] || [[ "$meta_changed" -eq 1 ]] || [[ -n "$force_reason" ]]; then
      should_upload=1
    fi

    LOCAL_CHANGED["$artifact_id"]="$local_changed"
    META_CHANGED["$artifact_id"]="$meta_changed"
    FORCE_REASON["$artifact_id"]="$force_reason"
    SHOULD_UPLOAD["$artifact_id"]="$should_upload"
  done

  local any_upload=0
  for artifact_id in "${ARTIFACT_IDS[@]}"; do
    if [[ "${SHOULD_UPLOAD[$artifact_id]}" -eq 1 ]]; then
      any_upload=1
      upload_single_artifact \
        "$artifact_id" \
        "${DATASET_REF[$artifact_id]}" \
        "${DATASET_EXISTS[$artifact_id]}" \
        "${LOCAL_CHANGED[$artifact_id]}" \
        "${META_CHANGED[$artifact_id]}" \
        "${FORCE_REASON[$artifact_id]}"
    fi
  done

  write_state_file

  if [[ "$any_upload" -eq 0 ]]; then
    if [[ "$state_exists" -eq 0 ]]; then
      echo "Initialized local upload state from existing Kaggle datasets. Run again to upload future changes only."
    else
      echo "No artifact/metadata changes detected across all artifact datasets. Nothing to upload."
    fi
    return
  fi

  echo "Done uploading changed artifact datasets."
}
