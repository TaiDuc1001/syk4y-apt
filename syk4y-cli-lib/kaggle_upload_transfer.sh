# Upload transfer and high-level sync flow for `syk4y kaggle upload`.

# shellcheck source=/dev/null
source "${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}/syk4y-cli-lib/fs_utils.sh"

declare -g CURRENT_UPLOAD_LOCK_DIR=""

release_upload_lock() {
  if [[ -n "${CURRENT_UPLOAD_LOCK_DIR:-}" ]]; then
    syk4y_release_lock "$CURRENT_UPLOAD_LOCK_DIR"
    CURRENT_UPLOAD_LOCK_DIR=""
  fi
}

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
  local max_retries=3
  local attempt=1
  local delay=2
  local command_status=0
  local temp_dir="${REPO_ROOT:-$(pwd)}/.syk4y-temp"
  mkdir -p "$temp_dir" 2>/dev/null || temp_dir="${TMPDIR:-/tmp}"
  local output_file
  local -a pipeline_status

  while [[ "$attempt" -le "$max_retries" ]]; do
    output_file="$(syk4y_mktemp "$temp_dir" "kaggle-upload-output")"

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

    # Success
    if [[ "$command_status" -eq 0 ]]; then
      rm -f "$output_file"
      return 0
    fi

    # Permanent error checks: authentication, permissions, validation
    if grep -Eqi '(401|403|unauthorized|forbidden|invalid credentials|kaggle api key not found|bad request|400|directory already exists|already exists)' "$output_file"; then
      echo "Permanent error detected. Not retrying." >&2
      rm -f "$output_file"
      return "$command_status"
    fi

    # Transient error handling: retry with bounded exponential backoff
    if [[ "$attempt" -lt "$max_retries" ]]; then
      echo "Transient upload error encountered (attempt $attempt/$max_retries). Retrying in ${delay}s..." >&2
      sleep "$delay"
      delay=$((delay * 2))
      attempt=$((attempt + 1))
      rm -f "$output_file"
    else
      echo "Upload operation failed after $max_retries attempts." >&2
      rm -f "$output_file"
      return "$command_status"
    fi
  done

  return "$command_status"
}

probe_kaggle_dataset() {
  local dataset_ref="$1"
  local dataset_owner output_file temp_dir
  dataset_owner="${dataset_ref%%/*}"
  temp_dir="${REPO_ROOT:-$(pwd)}/.syk4y-temp"
  mkdir -p "$temp_dir" 2>/dev/null || temp_dir="${TMPDIR:-/tmp}"
  output_file="$(syk4y_mktemp "$temp_dir" "kaggle-dataset-probe")"

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

verify_kaggle_dataset_status() {
  local dataset_ref="$1"
  local max_attempts="${KAGGLE_STATUS_POLL_ATTEMPTS:-5}"
  local delay="${KAGGLE_STATUS_POLL_DELAY:-2}"
  local attempt=1
  local status_output=""

  echo "Verifying remote dataset status on Kaggle for '$dataset_ref'..."
  while [[ "$attempt" -le "$max_attempts" ]]; do
    status_output="$("${KAGGLE_CMD[@]}" datasets status "$dataset_ref" 2>&1 || true)"
    local clean_status
    clean_status="$(printf '%s' "$status_output" | tr '[:upper:]' '[:lower:]')"

    if [[ "$clean_status" == *"ready"* ]]; then
      echo "Dataset creation verified on Kaggle: READY"
      return 0
    elif [[ "$clean_status" == *"error"* || "$clean_status" == *"failed"* || "$clean_status" == *"already exists"* ]]; then
      echo "Error: Kaggle dataset processing failed on server for '$dataset_ref'." >&2
      echo "Server response: $status_output" >&2
      return 1
    elif [[ "$clean_status" == *"pending"* || "$clean_status" == *"processing"* ]]; then
      if [[ "$attempt" -lt "$max_attempts" ]]; then
        sleep "$delay"
      fi
    else
      break
    fi
    attempt=$((attempt + 1))
  done

  if [[ "$clean_status" == *"pending"* || "$clean_status" == *"processing"* ]]; then
    echo "Dataset upload accepted by Kaggle. Remote ingestion is currently PROCESSING."
    return 0
  fi

  return 0
}

compute_artifact_fingerprint() {
  local source_path="$1"
  local item_name="$2"
  if [[ -f "$source_path" ]]; then
    syk4y_sha256 "$source_path"
  elif [[ -d "$source_path" ]]; then
    local cache_dir="${REPO_ROOT:-$(pwd)}/.syk4y-temp/kaggle-zip-cache"
    local cache_file="$cache_dir/${item_name}.zip"
    if [[ -f "$cache_file" ]]; then
      syk4y_sha256 "$cache_file"
    else
      # Fallback to directory mtime/size fingerprint
      find "$source_path" -type f -exec stat -f "%m %z" {} + 2>/dev/null || find "$source_path" -type f -exec stat -c "%Y %s" {} + 2>/dev/null || true
    fi
  fi
}

upload_single_artifact() {
  local artifact_id="$1"
  local dataset_ref="$2"
  local dataset_exists="$3"

  local item_name source_path metadata_file stage_dir upload_status
  item_name="$(artifact_item_name "$artifact_id")"
  source_path="$(artifact_source_path "$artifact_id")"
  metadata_file="$(artifact_metadata_file "$artifact_id")"

  # Validate metadata before attempting upload
  if ! "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" validate-metadata "$metadata_file"; then
    echo "Error: refusing upload due to invalid metadata in '$metadata_file'." >&2
    return 1
  fi

  # Check if artifact is unchanged to avoid unnecessary re-uploads (unless --force)
  local current_fingerprint last_fingerprint
  if [[ "$FORCE_UPLOAD" -eq 0 && "$dataset_exists" -eq 1 ]]; then
    current_fingerprint="$(compute_artifact_fingerprint "$source_path" "$artifact_id" | syk4y_sha256)"
    last_fingerprint="$(read_state_value "${artifact_id}_hash")"
    if [[ -n "$current_fingerprint" && -n "$last_fingerprint" && "$current_fingerprint" == "$last_fingerprint" ]]; then
      echo "Artifact '$artifact_id' is up to date on remote dataset '$dataset_ref'. Skipping re-upload (use --force to override)."
      return 0
    fi
  fi

  # Handle dry-run mode
  if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
    if [[ "$dataset_exists" -eq 1 ]]; then
      echo "[DRY RUN] Would update existing dataset '$dataset_ref' with artifact '$item_name' from '$source_path'"
    else
      echo "[DRY RUN] Would create new dataset '$dataset_ref' with artifact '$item_name' from '$source_path'"
    fi
    return 0
  fi
  
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
  stage_dir="$(syk4y_mktemp_dir "$temp_dir" "kaggle-upload-stage.${artifact_id}")"
  upload_status=0

  local cache_dir="$temp_dir/kaggle-zip-cache"
  mkdir -p "$cache_dir"
  local cache_file="$cache_dir/${artifact_id}.zip"

  cp "$metadata_file" "$stage_dir/dataset-metadata.json" || upload_status=$?
  if [[ "$upload_status" -eq 0 ]]; then
    if [[ -d "$source_path" && "$DIR_MODE" == "zip" ]]; then
      local staged_zip_name="${item_name%.zip}.zip"
      local cache_status
      cache_status="$("$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
        check-zip-cache \
        "$source_path" \
        "$cache_file" 2>/dev/null || echo "invalid")"

      if [[ "$FORCE_UPLOAD" -eq 0 && "$cache_status" == "valid" ]]; then
        echo "Using cached zip for '$artifact_id'"
      else
        if [[ "$cache_status" == "changed" ]]; then
          echo "Source changed: rebuilding $(basename "$cache_file")..."
        else
          echo "Creating zip cache for directory artifact '$artifact_id'..."
        fi
        local force_args=()
        if [[ "$FORCE_UPLOAD" -eq 1 ]]; then
          force_args+=(--force)
        fi
        if ! "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
          pack-artifact-dir-zip \
          "$source_path" \
          "$cache_file" \
          "$ARTIFACT_ZIP_MODE" \
          "${force_args[@]}"; then
          echo "Error: failed to zip directory artifact '$artifact_id'." >&2
          upload_status=1
        fi
      fi

      if [[ "$upload_status" -eq 0 ]]; then
        # Fail-fast pre-upload validation
        echo "Pre-upload validation for '$artifact_id'..."
        if ! "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
          validate-artifact-archive \
          "$cache_file" \
          "$metadata_file" \
          "$source_path"; then
          echo "Error: pre-upload archive validation failed for '$artifact_id'." >&2
          upload_status=1
        fi
      fi

      if [[ "$upload_status" -eq 0 ]]; then
        ln "$cache_file" "$stage_dir/$staged_zip_name" 2>/dev/null || cp "$cache_file" "$stage_dir/$staged_zip_name" || upload_status=$?
      fi
    elif [[ -f "$source_path" && "$source_path" == *.zip ]]; then
      echo "Pre-upload validation for archive '$artifact_id'..."
      if ! "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
        validate-artifact-archive \
        "$source_path" \
        "$metadata_file"; then
        echo "Error: pre-upload archive validation failed for '$artifact_id'." >&2
        upload_status=1
      fi
      if [[ "$upload_status" -eq 0 ]]; then
        ln -sfn "$source_path" "$stage_dir/$item_name" || upload_status=$?
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
    if ! verify_kaggle_dataset_status "$dataset_ref"; then
      echo "Error: Remote Kaggle dataset creation/update failed processing for '$dataset_ref'." >&2
      upload_status=1
    fi
  fi

  if [[ "$upload_status" -eq 0 ]]; then
    echo "Uploaded artifact source path: $source_path"
    # Update uploaded artifact hash in state
    local uploaded_hash
    uploaded_hash="$(compute_artifact_fingerprint "$source_path" "$artifact_id" | syk4y_sha256)"
    if [[ -n "$uploaded_hash" ]]; then
      local state_tmp state_tsv
      state_tmp="$(syk4y_mktemp "$temp_dir" "state-update")"
      state_tsv="$(syk4y_mktemp "$temp_dir" "state-tsv")"
      printf '%s\t%s\n' "${artifact_id}_hash" "$uploaded_hash" > "$state_tsv"
      "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" write-state-file "$state_tmp" "$state_tsv" "$STATE_FILE"
      rm -f "$state_tsv"
      mv -f "$state_tmp" "$STATE_FILE"
    fi
  fi

  syk4y_safe_rm_rf "$stage_dir" "$temp_dir"
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

  # Concurrency lock to prevent concurrent uploads to the same project
  CURRENT_UPLOAD_LOCK_DIR="$UPLOAD_ROOT/.upload.lock"
  if ! syk4y_acquire_lock "$CURRENT_UPLOAD_LOCK_DIR" "upload"; then
    return 1
  fi
  trap release_upload_lock EXIT RETURN INT TERM

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
      return 1
    fi

    sync_dataset_metadata_owner "$metadata_file"

    dataset_ref="$(extract_dataset_ref "$metadata_file")"
    if [[ -z "$dataset_ref" ]]; then
      echo "Error: '$metadata_file' missing 'id' field." >&2
      return 1
    fi
    DATASET_REF["$artifact_id"]="$dataset_ref"

    dataset_exists=0
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
      # In dry run, probe if possible, default to existing if probe succeeds
      if probe_kaggle_dataset "$dataset_ref" 2>/dev/null; then
        dataset_exists=1
      fi
    else
      if probe_kaggle_dataset "$dataset_ref"; then
        dataset_exists=1
      else
        probe_status=$?
        if [[ "$probe_status" -ne 1 ]]; then
          return "$probe_status"
        fi
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

  if [[ "${DRY_RUN:-0}" -eq 0 ]]; then
    write_state_file
    echo "Done uploading artifact datasets."
  else
    echo "Dry-run complete. No datasets were uploaded."
  fi

  release_upload_lock
  trap - EXIT RETURN INT TERM
}
