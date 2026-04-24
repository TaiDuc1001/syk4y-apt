# Wheelhouse build pipeline for `syk4y kaggle upload`.

build_wheelhouse_if_needed() {
  local prev_input_hash="$1"

  if ! [[ "$WHEEL_JOBS" =~ ^[0-9]+$ ]] || [[ "$WHEEL_JOBS" -lt 1 ]]; then
    echo "WHEEL_JOBS must be a positive integer. Got: $WHEEL_JOBS" >&2
    exit 1
  fi
  if [[ "$WHEEL_FAIL_ON_MISSING" != "0" ]] && [[ "$WHEEL_FAIL_ON_MISSING" != "1" ]]; then
    echo "WHEEL_FAIL_ON_MISSING must be 0 or 1. Got: $WHEEL_FAIL_ON_MISSING" >&2
    exit 1
  fi

  local build_dir wheelhouse_tmp_zip req_out req_sanitized_out failed_reqs_file
  build_dir="$(mktemp -d "/tmp/wheelhouse-build.XXXXXX")"
  wheelhouse_tmp_zip="$(mktemp "/tmp/wheelhouse-archive.XXXXXX.zip")"
  req_out="$build_dir/_requirements.txt"
  req_sanitized_out="$build_dir/_requirements_sanitized.txt"
  failed_reqs_file="$(mktemp "/tmp/wheelhouse-failed.XXXXXX.txt")"
  cleanup_wheel_tmp() {
    rm -rf "$build_dir"
    rm -f "$wheelhouse_tmp_zip"
    rm -f "$failed_reqs_file"
  }
  trap cleanup_wheel_tmp RETURN

  echo "Building wheelhouse inputs from: $WHEELHOUSE_PYTHON"
  PIP_DISABLE_PIP_VERSION_CHECK=1 "$WHEELHOUSE_PYTHON" -m pip freeze --all --exclude-editable > "$req_out"
  if [[ ! -s "$req_out" ]]; then
    echo "Error: failed to freeze installed packages from $WHEELHOUSE_PYTHON" >&2
    exit 1
  fi

  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
    sanitize-wheelhouse-requirements \
    "$req_out" \
    "$req_sanitized_out"
  if [[ ! -s "$req_sanitized_out" ]]; then
    echo "Error: no valid portable requirements found after sanitization." >&2
    echo "Hint: install required packages into '$WHEELHOUSE_PYTHON' first, then retry." >&2
    exit 1
  fi
  req_out="$req_sanitized_out"

  mapfile -t EXTRA_INDEXES < <(
    "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" pyproject-extra-indexes "$REPO_ROOT/pyproject.toml"
  )

  WHEELHOUSE_INPUT_HASH="$({
    "$WHEELHOUSE_PYTHON" -V 2>&1
    cat "$req_out"
    printf '%s\n' "${EXTRA_INDEXES[@]}"
  } | sha256sum | awk '{print $1}')"

  if [[ -f "$WHEELHOUSE_PATH" ]] && [[ -n "$prev_input_hash" ]] && [[ "$prev_input_hash" == "$WHEELHOUSE_INPUT_HASH" ]]; then
    echo "wheelhouse.zip is up-to-date (dependency snapshot unchanged)."
    return
  fi

  mapfile -t REQ_ITEMS < <(
    sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' "$req_out" \
      | grep -Ev '^(#|$|-)'
  )
  if [[ "${#REQ_ITEMS[@]}" -eq 0 ]]; then
    echo "Error: no valid requirements parsed from $req_out" >&2
    exit 1
  fi

  local -a pip_cmd_base extra_index_args
  pip_cmd_base=("$WHEELHOUSE_PYTHON" -m pip wheel --no-deps --progress-bar off --wheel-dir "$build_dir")
  extra_index_args=()
  for url in "${EXTRA_INDEXES[@]}"; do
    extra_index_args+=(--extra-index-url "$url")
  done

  echo "Fetching/building wheels with parallel jobs: $WHEEL_JOBS"
  if [[ "$WHEEL_JOBS" -gt 1 ]] && command -v xargs >/dev/null 2>&1; then
    export FAILED_REQS_FILE="$failed_reqs_file"
    if ! printf '%s\0' "${REQ_ITEMS[@]}" \
      | xargs -0 -P "$WHEEL_JOBS" -I{} bash -lc '
          req="$1"
          shift
          if ! env PIP_DISABLE_PIP_VERSION_CHECK=1 "$@" "$req"; then
            printf "%s\n" "$req" >> "$FAILED_REQS_FILE"
          fi
        ' _ "{}" "${pip_cmd_base[@]}" "${extra_index_args[@]}"; then
      true
    fi
  else
    local req
    for req in "${REQ_ITEMS[@]}"; do
      if ! env PIP_DISABLE_PIP_VERSION_CHECK=1 "${pip_cmd_base[@]}" "${extra_index_args[@]}" "$req"; then
        printf '%s\n' "$req" >> "$failed_reqs_file"
      fi
    done
  fi

  mapfile -t FAILED_REQS < <(sort -u "$failed_reqs_file" | sed '/^$/d')
  if [[ "${#FAILED_REQS[@]}" -gt 0 ]]; then
    echo "Warning: unable to build wheels for ${#FAILED_REQS[@]} requirement(s)."
    local req
    for req in "${FAILED_REQS[@]}"; do
      echo "  - $req"
    done
    if [[ "$WHEEL_FAIL_ON_MISSING" == "1" ]]; then
      echo "Error: WHEEL_FAIL_ON_MISSING=1 and some wheels failed to build." >&2
      exit 1
    fi
  fi

  local wheel_count
  wheel_count="$(find "$build_dir" -maxdepth 1 -type f -name '*.whl' | wc -l | tr -d ' ')"
  if [[ "$wheel_count" -eq 0 ]]; then
    echo "Error: no wheels were built from the current environment requirements." >&2
    exit 1
  fi

  echo "Packing wheelhouse.zip"
  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
    pack-wheelhouse-zip \
    "$build_dir" \
    "$wheelhouse_tmp_zip" \
    "$WHEELHOUSE_ZIP_MODE"

  mkdir -p "$WHEELHOUSE_DATASET_DIR"
  if [[ -f "$WHEELHOUSE_PATH" ]] && cmp -s "$wheelhouse_tmp_zip" "$WHEELHOUSE_PATH"; then
    echo "wheelhouse.zip unchanged."
    return
  fi

  mv -f "$wheelhouse_tmp_zip" "$WHEELHOUSE_PATH"
  echo "Updated wheelhouse archive: $WHEELHOUSE_PATH"
}
