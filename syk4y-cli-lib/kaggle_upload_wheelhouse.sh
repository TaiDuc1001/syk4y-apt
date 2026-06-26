# Wheelhouse build pipeline for `syk4y kaggle upload`.

build_wheelhouse_if_needed() {
  local prev_input_hash="$1"
 
  # Check if we need to build via Docker for a different architecture
  if [[ -n "${WHEEL_ARCH:-}" ]] && [[ "$WHEEL_ARCH" != "native" ]]; then
    local host_arch target_arch
    host_arch="$(uname -m)"
    
    normalize_arch() {
      local a="${1,,}"
      if [[ "$a" == "amd64" || "$a" == "x86_64" || "$a" == "i686" ]]; then
        echo "x86_64"
      elif [[ "$a" == "arm64" || "$a" == "aarch64" ]]; then
        echo "aarch64"
      else
        echo "$a"
      fi
    }

    local host_norm target_norm
    host_norm="$(normalize_arch "$host_arch")"
    target_norm="$(normalize_arch "$WHEEL_ARCH")"

    if [[ "$host_norm" != "$target_norm" ]]; then
      local docker_platform
      if [[ "$target_norm" == "x86_64" ]]; then
        docker_platform="linux/amd64"
      elif [[ "$target_norm" == "aarch64" ]]; then
        docker_platform="linux/arm64"
      else
        echo "Error: unsupported architecture '$WHEEL_ARCH'" >&2
        exit 1
      fi

      echo "Cross-architecture build detected (host: $host_norm, target: $target_norm)."
      echo "Delegating wheelhouse build to Docker container ($docker_platform)..."

      if ! command -v docker >/dev/null 2>&1; then
        echo "Error: docker command not found but is required for cross-architecture build." >&2
        exit 1
      fi

      if ! docker image inspect python:3.10-slim >/dev/null 2>&1; then
        echo "Error: Docker image python:3.10-slim is not available locally." >&2
        echo "Please run: docker pull --platform $docker_platform python:3.10-slim" >&2
        exit 1
      fi

      # Run the build inside docker.
      # We mount REPO_ROOT to /workspace. We mount /tmp to /tmp to share temp files/outputs.
      # We pass WHEEL_ARCH=native to prevent recursive docker calls inside the container.
      local container_upload_root="$UPLOAD_ROOT"
      if [[ "$container_upload_root" == "$REPO_ROOT"* ]]; then
        container_upload_root="/workspace${container_upload_root#$REPO_ROOT}"
      fi

      local docker_err
      docker_err="$(mktemp "/tmp/docker-build-error.XXXXXX.log")"
      if ! docker run --rm \
        --platform "$docker_platform" \
        -v "$REPO_ROOT:/workspace" \
        -v "$SCRIPT_DIR:/syk4y-toolkit" \
        -v /tmp:/tmp \
        -w /workspace \
        -e PYTHON_BIN=python3 \
        -e WHEELHOUSE_PYTHON=python3 \
        -e WHEEL_ARCH=native \
        -e WHEEL_JOBS="$WHEEL_JOBS" \
        -e WHEELHOUSE_ZIP_MODE="$WHEELHOUSE_ZIP_MODE" \
        -e WHEEL_FAIL_ON_MISSING="$WHEEL_FAIL_ON_MISSING" \
        python:3.10-slim \
        bash -c "pip install uv && /syk4y-toolkit/syk4y-kaggle upload --repo-root /workspace --upload-dir $container_upload_root --build-wheel-only" 2>"$docker_err"; then
        
        local err_msg
        err_msg="$(cat "$docker_err")"
        echo "$err_msg" >&2
        rm -f "$docker_err"
        
        if [[ "$err_msg" == *"exec format error"* ]]; then
          local install_arch="$target_norm"
          if [[ "$install_arch" == "x86_64" ]]; then install_arch="amd64"; fi
          if [[ "$install_arch" == "aarch64" ]]; then install_arch="arm64"; fi

          echo "" >&2
          echo "======================================================================" >&2
          echo "Error: QEMU user-space emulation for $target_norm is not configured on your host." >&2
          echo "To run $docker_platform containers on your $host_norm machine, please register QEMU:" >&2
          echo "  docker run --privileged --rm tonistiigi/binfmt --install $install_arch" >&2
          echo "Or install native package (Debian/Ubuntu):" >&2
          echo "  sudo apt-get update && sudo apt-get install -y qemu-user-static binfmt-support" >&2
          echo "======================================================================" >&2
        fi
        exit 1
      fi
      rm -f "$docker_err"

      return
    fi
  fi

  if ! [[ "$WHEEL_JOBS" =~ ^[0-9]+$ ]] || [[ "$WHEEL_JOBS" -lt 1 ]]; then
    echo "WHEEL_JOBS must be a positive integer. Got: $WHEEL_JOBS" >&2
    exit 1
  fi
  if [[ "$WHEEL_FAIL_ON_MISSING" != "0" ]] && [[ "$WHEEL_FAIL_ON_MISSING" != "1" ]]; then
    echo "WHEEL_FAIL_ON_MISSING must be 0 or 1. Got: $WHEEL_FAIL_ON_MISSING" >&2
    exit 1
  fi

  local build_dir wheelhouse_tmp_zip req_out req_sanitized_out failed_reqs_file
  local requirements_source uv_lock_path
  local req local_wheel_path
  build_dir="$(mktemp -d "/tmp/wheelhouse-build.XXXXXX")"
  wheelhouse_tmp_zip="$(mktemp "/tmp/wheelhouse-archive.XXXXXX.zip")"
  req_out="$build_dir/_requirements.txt"
  req_sanitized_out="$build_dir/_requirements_sanitized.txt"
  failed_reqs_file="$(mktemp "/tmp/wheelhouse-failed.XXXXXX.txt")"
  uv_lock_path="$REPO_ROOT/uv.lock"
  cleanup_wheel_tmp() {
    rm -rf "$build_dir"
    rm -f "$wheelhouse_tmp_zip"
    rm -f "$failed_reqs_file"
  }
  trap cleanup_wheel_tmp RETURN

  if [[ -f "$uv_lock_path" ]]; then
    if ! command -v uv >/dev/null 2>&1; then
      echo "Error: '$uv_lock_path' exists but uv is not available." >&2
      echo "Install uv or remove the lockfile only if this is not a uv-managed project." >&2
      exit 1
    fi

    requirements_source="uv.lock"
    echo "Building wheelhouse inputs from: $uv_lock_path"
    if ! uv export \
      --project "$REPO_ROOT" \
      --locked \
      --format requirements.txt \
      --no-hashes \
      --no-header \
      --no-annotate \
      --no-editable \
      --no-emit-project \
      --output-file "$req_out"; then
      echo "Error: failed to export an up-to-date '$uv_lock_path'." >&2
      echo "Run 'uv lock' in '$REPO_ROOT' and retry." >&2
      exit 1
    fi
  else
    requirements_source="pip freeze"
    echo "No uv.lock found; building wheelhouse inputs from: $WHEELHOUSE_PYTHON"
    PIP_DISABLE_PIP_VERSION_CHECK=1 "$WHEELHOUSE_PYTHON" -m pip freeze --all --exclude-editable > "$req_out"
    if [[ ! -s "$req_out" ]]; then
      echo "Error: failed to freeze installed packages from $WHEELHOUSE_PYTHON" >&2
      exit 1
    fi
  fi

  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
    sanitize-wheelhouse-requirements \
    "$req_out" \
    "$req_sanitized_out" \
    "$REPO_ROOT"
  if [[ ! -s "$req_sanitized_out" ]]; then
    echo "Error: no valid portable requirements found after sanitization." >&2
    echo "Hint: install required packages into '$WHEELHOUSE_PYTHON' first, then retry." >&2
    exit 1
  fi
  req_out="$req_sanitized_out"

  if [[ ! -s "$req_out" ]]; then
    echo "Error: no dependencies found from $requirements_source." >&2
    exit 1
  fi

  mapfile -t EXTRA_INDEXES < <(
    "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" pyproject-extra-indexes "$REPO_ROOT/pyproject.toml"
  )

  WHEELHOUSE_INPUT_HASH="$({
    "$WHEELHOUSE_PYTHON" -V 2>&1
    printf 'requirements-source=%s\n' "$requirements_source"
    cat "$req_out"
    while IFS= read -r req; do
      [[ "$req" == *.whl ]] || continue
      if [[ "$req" == /* ]]; then
        local_wheel_path="$req"
      else
        local_wheel_path="$REPO_ROOT/$req"
      fi
      if [[ -f "$local_wheel_path" ]]; then
        printf 'local-wheel=%s\n' "$req"
        sha256sum "$local_wheel_path"
      fi
    done < "$req_out"
    printf '%s\n' "${EXTRA_INDEXES[@]}"
    if [[ "$requirements_source" == "uv.lock" ]]; then
      uv --version
      sha256sum "$uv_lock_path"
    fi
  } | sha256sum | awk '{print $1}')"

  if [[ -f "$WHEELHOUSE_PATH" ]] && [[ -n "$prev_input_hash" ]] && [[ "$prev_input_hash" == "$WHEELHOUSE_INPUT_HASH" ]]; then
    echo "wheelhouse.zip is up-to-date (dependency snapshot unchanged)."
    trap - RETURN
    cleanup_wheel_tmp
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

  local -a PIP_REQ_ITEMS
  PIP_REQ_ITEMS=()
  for req in "${REQ_ITEMS[@]}"; do
    local_wheel_path=""
    if [[ "$req" == *.whl ]]; then
      if [[ "$req" == /* ]]; then
        local_wheel_path="$req"
      else
        local_wheel_path="$REPO_ROOT/$req"
      fi
      if [[ -f "$local_wheel_path" ]]; then
        echo "Copying local wheel into wheelhouse: $req"
        cp -f "$local_wheel_path" "$build_dir/"
        continue
      fi
    fi
    PIP_REQ_ITEMS+=("$req")
  done
  REQ_ITEMS=("${PIP_REQ_ITEMS[@]}")

  local -a pip_cmd_base extra_index_args
  pip_cmd_base=("$WHEELHOUSE_PYTHON" -m pip wheel --no-deps --progress-bar off --wheel-dir "$build_dir")
  extra_index_args=()
  for url in "${EXTRA_INDEXES[@]}"; do
    extra_index_args+=(--extra-index-url "$url")
  done

  if [[ "${#REQ_ITEMS[@]}" -eq 0 ]]; then
    echo "All wheelhouse inputs were local wheels."
  elif [[ "$WHEEL_JOBS" -gt 1 ]] && command -v xargs >/dev/null 2>&1; then
    echo "Fetching/building wheels with parallel jobs: $WHEEL_JOBS"
    export FAILED_REQS_FILE="$failed_reqs_file"
    if ! printf '%s\0' "${REQ_ITEMS[@]}" \
      | xargs -0 -P "$WHEEL_JOBS" -I{} bash -lc '
          req="$1"
          repo_root="$2"
          shift 2
          cd "$repo_root"
          if ! env PIP_DISABLE_PIP_VERSION_CHECK=1 "$@" "$req"; then
            printf "%s\n" "$req" >> "$FAILED_REQS_FILE"
          fi
        ' _ "{}" "$REPO_ROOT" "${pip_cmd_base[@]}" "${extra_index_args[@]}"; then
      true
    fi
  else
    echo "Fetching/building wheels with parallel jobs: $WHEEL_JOBS"
    for req in "${REQ_ITEMS[@]}"; do
      if ! (
        cd "$REPO_ROOT"
        env PIP_DISABLE_PIP_VERSION_CHECK=1 "${pip_cmd_base[@]}" "${extra_index_args[@]}" "$req"
      ); then
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
    trap - RETURN
    cleanup_wheel_tmp
    return
  fi

  mv -f "$wheelhouse_tmp_zip" "$WHEELHOUSE_PATH"
  echo "Updated wheelhouse archive: $WHEELHOUSE_PATH"
  trap - RETURN
  cleanup_wheel_tmp
}
