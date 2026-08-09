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

run_kaggle_upload_checked() {
  local output_file command_status
  local -a pipeline_status
  output_file="$(mktemp "/tmp/kaggle-upload-output.XXXXXX.log")"

  if "${KAGGLE_CMD[@]}" "$@" 2>&1 | tee "$output_file"; then
    pipeline_status=("${PIPESTATUS[@]}")
  else
    pipeline_status=("${PIPESTATUS[@]}")
  fi
  command_status="${pipeline_status[0]}"

  if [[ "$command_status" -eq 0 ]] && \
     grep -Eq 'Dataset (creation|version creation) error:' "$output_file"; then
    echo "Error: Kaggle CLI reported an upload failure despite returning exit code 0." >&2
    command_status=1
  fi

  rm -f "$output_file"
  return "$command_status"
}

probe_kaggle_dataset() {
  local dataset_ref="$1"
  local dataset_owner output_file
  dataset_owner="${dataset_ref%%/*}"
  output_file="$(mktemp "/tmp/kaggle-dataset-probe.XXXXXX.log")"

  if "${KAGGLE_CMD[@]}" datasets files -d "$dataset_ref" >"$output_file" 2>&1; then
    rm -f "$output_file"
    return 0
  fi

  if grep -Eqi \
    '(^|[^0-9])404([^0-9]|$)|not found|does not exist|could not find (the )?dataset|dataset .* unavailable' \
    "$output_file"; then
    rm -f "$output_file"
    return 1
  fi

  # Kaggle's ListDatasetFiles endpoint also returns 403 for a dataset slug
  # that does not exist. Treat that response as "missing" only for the
  # authenticated user's own namespace; a 403 for another owner remains an
  # authorization error.
  if [[ "$dataset_owner" == "$KAGGLE_UPLOAD_USERNAME" ]] && \
     grep -Eqi '(^|[^0-9])403([^0-9]|$)|forbidden' "$output_file"; then
    rm -f "$output_file"
    return 1
  fi

  echo "Error: could not determine whether Kaggle dataset '$dataset_ref' exists." >&2
  sed 's/^/  Kaggle: /' "$output_file" >&2
  rm -f "$output_file"
  return 2
}

upload_single_artifact() {
  local artifact_id="$1"
  local dataset_ref="$2"
  local dataset_exists="$3"

  local item_name source_path metadata_file stage_dir upload_status
  item_name="$(artifact_item_name "$artifact_id")"
  source_path="$(artifact_source_path "$artifact_id")"
  metadata_file="$(artifact_metadata_file "$artifact_id")"
  
  local temp_dir stage_dir r_root
  r_root="${REPO_ROOT:-}"
  if [[ -z "$r_root" ]]; then
    r_root="$(pwd)"
  fi
  temp_dir="$r_root/.syk4y-temp"
  mkdir -p "$temp_dir"
  if declare -f syk4y_ensure_temp_dir_gitignore >/dev/null; then
    syk4y_ensure_temp_dir_gitignore "$r_root"
  fi
  stage_dir="$(mktemp -d "$temp_dir/kaggle-upload-stage.${artifact_id}.XXXXXX")"
  upload_status=0

  local cache_dir="$temp_dir/kaggle-zip-cache"
  mkdir -p "$cache_dir"
  local cache_file="$cache_dir/${artifact_id}.zip"

  cp "$metadata_file" "$stage_dir/dataset-metadata.json" || upload_status=$?
  if [[ "$upload_status" -eq 0 ]]; then
    if [[ -d "$source_path" && "$DIR_MODE" == "zip" ]]; then
      if [[ -f "$cache_file" ]]; then
        echo "Using cached zip for '$artifact_id'"
        ln "$cache_file" "$stage_dir/$item_name.zip" 2>/dev/null || cp "$cache_file" "$stage_dir/$item_name.zip" || upload_status=$?
      else
        echo "Error: ZIP file for artifact '$artifact_id' not found in cache." >&2
        echo "Please run: syk4y kaggle zip" >&2
        upload_status=1
      fi
    else
      ln -sfn "$source_path" "$stage_dir/$item_name" || upload_status=$?
    fi
  fi
  if [[ "$upload_status" -eq 0 ]]; then
    clear_resume_markers "$stage_dir" || upload_status=$?
  fi

  if [[ "$upload_status" -eq 0 ]]; then
    if [[ "$dataset_exists" -eq 1 ]]; then
      echo "Updating '$artifact_id' dataset."
      run_kaggle_upload_checked datasets version -p "$stage_dir" -m "${VERSION_MESSAGE} [$artifact_id]" -r "$DIR_MODE" || upload_status=$?
    else
      echo "Dataset '$dataset_ref' does not exist yet; creating with artifact '$item_name'."
      run_kaggle_upload_checked datasets create -p "$stage_dir" -r "$DIR_MODE" || upload_status=$?
    fi
  fi

  if [[ "$upload_status" -eq 0 ]]; then
    echo "Uploaded artifact source path: $source_path"
  fi

  rm -rf -- "$stage_dir"
  return "$upload_status"
}

sync_dataset_metadata_owner() {
  local metadata_file="$1"
  local old_ref new_ref

  old_ref="$(extract_dataset_ref "$metadata_file")"
  new_ref="$("$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
    rewrite-dataset-owner \
    "$metadata_file" \
    "$KAGGLE_UPLOAD_USERNAME")"

  if [[ -z "$new_ref" ]]; then
    echo "Error: '$metadata_file' missing valid dataset id." >&2
    exit 1
  fi

  if [[ -n "$old_ref" && "$old_ref" != "$new_ref" ]]; then
    echo "Updated dataset metadata owner: $old_ref -> $new_ref"
  fi
}

kaggle_upload_run_flow() {
  local artifact_id source_path metadata_file item_name
  local dataset_ref dataset_exists
  local failed_upload_status probe_status

  cd "$REPO_ROOT"

  PYTHON_BIN="$(syk4y_resolve_python_bin_or_die "$REPO_ROOT" "syk4y")"

  mkdir -p "$UPLOAD_ROOT"

  if [[ "$BUILD_WHEEL_ONLY" -eq 1 ]]; then
    local prev_wheelhouse_input
    prev_wheelhouse_input="$(read_state_value "$WHEELHOUSE_INPUT_KEY")"
    build_wheelhouse_if_needed "$prev_wheelhouse_input"
    if [[ ! -f "$WHEELHOUSE_PATH" ]]; then
      echo "Error: wheelhouse build completed but '$WHEELHOUSE_PATH' is missing." >&2
      exit 1
    fi
    write_state_file
    echo "Build-wheel-only mode complete: $WHEELHOUSE_PATH"
    return
  fi

  ensure_kaggle_upload_prereqs
  KAGGLE_UPLOAD_USERNAME="$(syk4y_resolve_kaggle_username "$PYTHON_BIN" 1)"
  resolve_initialized_artifacts
  verify_dataset_structure

  declare -A DATASET_REF
  declare -A DATASET_EXISTS

  for artifact_id in "${ARTIFACT_IDS[@]}"; do
    source_path="$(artifact_source_path "$artifact_id")"
    metadata_file="$(artifact_metadata_file "$artifact_id")"
    item_name="$(artifact_item_name "$artifact_id")"

    if [[ ! -e "$source_path" ]]; then
      echo "Error: missing artifact path '$source_path'" >&2
      exit 1
    fi

    sync_dataset_metadata_owner "$metadata_file"

    dataset_ref="$(extract_dataset_ref "$metadata_file")"
    if [[ -z "$dataset_ref" ]]; then
      echo "Error: '$metadata_file' missing 'id' field." >&2
      exit 1
    fi
    DATASET_REF["$artifact_id"]="$dataset_ref"

    dataset_exists=0
    if probe_kaggle_dataset "$dataset_ref"; then
      dataset_exists=1
    else
      probe_status=$?
      if [[ "$probe_status" -ne 1 ]]; then
        return "$probe_status"
      fi
    fi
    DATASET_EXISTS["$artifact_id"]="$dataset_exists"
  done

  failed_upload_status=0
  for artifact_id in "${ARTIFACT_IDS[@]}"; do
    if upload_single_artifact \
      "$artifact_id" \
      "${DATASET_REF[$artifact_id]}" \
      "${DATASET_EXISTS[$artifact_id]}"; then
      :
    else
      failed_upload_status=$?
      break
    fi
  done

  if [[ "$failed_upload_status" -ne 0 ]]; then
    echo "Error: artifact dataset upload failed." >&2
    return "$failed_upload_status"
  fi

  write_state_file
  echo "Done uploading artifact datasets."
}

