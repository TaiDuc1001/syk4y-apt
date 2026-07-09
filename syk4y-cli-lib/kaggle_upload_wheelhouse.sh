# Wheelhouse build pipeline for `syk4y kaggle upload`.

# shellcheck source=/dev/null
source "${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}/syk4y-cli-lib/kaggle_upload_env.sh"

build_wheelhouse_if_needed() {
  local prev_input_hash="$1"
  
  WHEELHOUSE_PYTHON="$(resolve_wheelhouse_python)"
  ensure_pip "$WHEELHOUSE_PYTHON"

  local -a EXTRA_INDEXES
  mapfile -t EXTRA_INDEXES < <(
    "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" pyproject-extra-indexes "$REPO_ROOT/pyproject.toml"
  )
 
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

      # Detect Python version from WHEELHOUSE_PYTHON so pip ABI flags and
      # Docker image match the user's actual environment (e.g. Python 3.11+).
      local pip_py_version pip_py_compact docker_image
      pip_py_version="$("${WHEELHOUSE_PYTHON}" --version 2>&1 | sed -E 's/Python ([0-9]+\.[0-9]+)\..*/\1/' | head -1)"
      [[ -z "$pip_py_version" ]] && pip_py_version="3.10"
      pip_py_compact="${pip_py_version//./}"
      docker_image="python:${pip_py_version}-slim"

      if ! docker image inspect "$docker_image" > /dev/null 2>&1; then
        echo "Docker image $docker_image is not available locally. Attempting to pull..."
        if ! docker pull --platform "$docker_platform" "$docker_image"; then
          echo "Error: Failed to pull Docker image $docker_image." >&2
          echo "Please check your network connection or manually run: docker pull --platform $docker_platform $docker_image" >&2
          exit 1
        fi
      fi

      # 1. Natively export requirements on the host to avoid QEMU uv segfaults
      local host_req_out uv_lock_path temp_dir r_root
      r_root="${REPO_ROOT:-}"
      if [[ -z "$r_root" ]]; then
        r_root="$(pwd)"
      fi
      temp_dir="$r_root/.syk4y-temp"
      mkdir -p "$temp_dir"
      if declare -f syk4y_ensure_temp_dir_gitignore >/dev/null; then
        syk4y_ensure_temp_dir_gitignore "$r_root"
      fi
      host_req_out="$temp_dir/wheelhouse-host-req.txt"
      uv_lock_path="$REPO_ROOT/uv.lock"
      
      if [[ -f "$uv_lock_path" ]]; then
        echo "Exporting requirements on host from uv.lock..."
        if ! uv export \
          --project "$REPO_ROOT" \
          --locked \
          --format requirements.txt \
          --no-hashes \
          --no-header \
          --no-annotate \
          --no-editable \
          --no-emit-project \
          --output-file "$host_req_out"; then
          echo "Error: failed to export '$uv_lock_path' on host." >&2
          rm -rf "$temp_dir"
          exit 1
        fi
      else
        echo "No uv.lock found; freezing packages on host..."
        PIP_DISABLE_PIP_VERSION_CHECK=1 "$WHEELHOUSE_PYTHON" -m pip freeze --all --exclude-editable > "$host_req_out"
      fi

      # 2. Setup build directory and sanitize requirements on host
      local build_dir wheelhouse_tmp_zip req_out req_sanitized_out docker_req_out
      build_dir="$temp_dir/wheelhouse-build"
      mkdir -p "$build_dir"
      wheelhouse_tmp_zip="$temp_dir/wheelhouse-archive.zip"
      req_out="$build_dir/_requirements.txt"
      req_sanitized_out="$build_dir/_requirements_sanitized.txt"
      docker_req_out="$build_dir/_requirements_docker.txt"

      local full_req_backup
      full_req_backup="$temp_dir/wheelhouse-full-req.txt"

      "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
        sanitize-wheelhouse-requirements \
        "$host_req_out" \
        "$req_sanitized_out" \
        "$REPO_ROOT"

      # Backup the full sanitized list NOW before Docker may overwrite
      # _requirements_sanitized.txt when it runs syk4y-kaggle natively inside
      # the container (which only sees its own subset of packages).
      cp -f "$req_sanitized_out" "$full_req_backup"

      mapfile -t REQ_ITEMS < <(
        sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' "$req_sanitized_out" \
          | grep -Ev '^(#|$|-)'
      )

      # 3. Determine target platform details for pip download on host
      local pip_platform pip_impl pip_abi
      pip_impl="cp"
      pip_abi="cp${pip_py_compact}"
      if [[ "$target_norm" == "x86_64" ]]; then
        pip_platform="manylinux2014_x86_64"
      elif [[ "$target_norm" == "aarch64" ]]; then
        pip_platform="manylinux2014_aarch64"
      else
        pip_platform="$target_norm"
      fi

      # 4. Attempt to download pre-built wheels natively on host in parallel
      local -a remaining_reqs
      remaining_reqs=()
      
      local -a PIP_REQ_ITEMS
      PIP_REQ_ITEMS=()
      for req in "${REQ_ITEMS[@]}"; do
        if [[ "$req" == *.whl ]]; then
          local_wheel_path=""
          if [[ "$req" == /* ]]; then
            local_wheel_path="$req"
          else
            local_wheel_path="$REPO_ROOT/$req"
          fi
          if [[ -f "$local_wheel_path" ]]; then
            echo "Copying local wheel: $req"
            cp -f "$local_wheel_path" "$build_dir/"
          fi
          continue
        fi
        PIP_REQ_ITEMS+=("$req")
      done

      if [[ "${#PIP_REQ_ITEMS[@]}" -gt 0 ]]; then
        local host_failed_reqs_file
        host_failed_reqs_file="$(mktemp "$temp_dir/host-failed.XXXXXX.txt")"
        export HOST_FAILED_REQS_FILE="$host_failed_reqs_file"
        
        local -a extra_index_args
        extra_index_args=()
        for url in "${EXTRA_INDEXES[@]}"; do
          extra_index_args+=(--extra-index-url "$url")
        done

        echo "Downloading pre-built wheels natively on host with parallel jobs: $WHEEL_JOBS"
        if ! printf '%s\0' "${PIP_REQ_ITEMS[@]}" \
          | xargs -0 -P "$WHEEL_JOBS" -I{} bash -c '
              req="$1"
              repo_root="$2"
              build_dir="$3"
              pip_platform="$4"
              pip_py_version="$5"
              pip_impl="$6"
              pip_abi="$7"
              shift 7
              
              cd "$repo_root"
              if ! env PIP_DISABLE_PIP_VERSION_CHECK=1 "$@" \
                --only-binary=:all: \
                --platform "$pip_platform" \
                --python-version "$pip_py_version" \
                --implementation "$pip_impl" \
                --abi "$pip_abi" \
                --no-deps \
                -d "$build_dir" \
                "$req" >/dev/null 2>&1; then
                printf "%s\n" "$req" >> "$HOST_FAILED_REQS_FILE"
              else
                echo "  Downloaded: $req (native)"
              fi
            ' _ "{}" "$REPO_ROOT" "$build_dir" "$pip_platform" "$pip_py_version" "$pip_impl" "$pip_abi" "$WHEELHOUSE_PYTHON" -m pip download "${extra_index_args[@]}"; then
          true
        fi
        
        if [[ -f "$host_failed_reqs_file" ]]; then
          mapfile -t remaining_reqs < <(sort -u "$host_failed_reqs_file" | sed '/^$/d')
          rm -f "$host_failed_reqs_file"
        fi
      fi

      # 5. Build remaining wheels inside Docker if any
      if [[ "${#remaining_reqs[@]}" -gt 0 ]]; then
        echo "Building ${#remaining_reqs[@]} remaining package(s) from source inside Docker container ($docker_platform)..."
        # Write remaining requirements to a new file for Docker
        printf '%s\n' "${remaining_reqs[@]}" > "$docker_req_out"

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
          -w /workspace \
          -e PYTHON_BIN=python3 \
          -e WHEELHOUSE_PYTHON=python3 \
          -e WHEEL_ARCH=native \
          -e EXPORTED_REQUIREMENTS_PATH="/workspace/.syk4y-temp/wheelhouse-build/_requirements_docker.txt" \
          -e OVERRIDE_BUILD_DIR="/workspace/.syk4y-temp/wheelhouse-build" \
          -e WHEEL_JOBS="$WHEEL_JOBS" \
          -e WHEELHOUSE_ZIP_MODE="$WHEELHOUSE_ZIP_MODE" \
          -e WHEEL_FAIL_ON_MISSING="$WHEEL_FAIL_ON_MISSING" \
          -e SYK4Y_BASE_DATASET_SLUG="${BASE_DATASET_SLUG:-}" \
          -e CONTAINER_UPLOAD_DIR="$container_upload_root" \
          "$docker_image" \
          bash -c 'pip install --upgrade pip --quiet && exec /syk4y-toolkit/syk4y-kaggle upload --repo-root /workspace --upload-dir "$CONTAINER_UPLOAD_DIR" --build-wheel-only' 2>"$docker_err"; then
          
          local err_msg
          err_msg="$(cat "$docker_err")"
          echo "$err_msg" >&2
          rm -f "$docker_err"
          rm -rf "$temp_dir"
          
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
      else
        echo "All wheels downloaded natively on host. Bypassing Docker container build."
      fi

      # 6. Pack the wheelhouse and cleanup
      echo "Packing wheelhouse.zip"
      # Write the FULL sanitized requirements (all packages, not just Docker's
      # subset) so the archive's _requirements.txt covers the whole wheelhouse.
      cp -f "$full_req_backup" "$build_dir/_requirements.txt"

      # Remove intermediate requirement files — only _requirements.txt is kept.
      rm -f "$req_sanitized_out" "$docker_req_out"

      "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_upload_py_cli.py" \
        pack-wheelhouse-zip \
        "$build_dir" \
        "$wheelhouse_tmp_zip" \
        "$WHEELHOUSE_ZIP_MODE"

      mkdir -p "$WHEELHOUSE_DATASET_DIR"
      mv -f "$wheelhouse_tmp_zip" "$WHEELHOUSE_PATH"
      echo "Updated wheelhouse archive: $WHEELHOUSE_PATH"

      rm -f "$host_req_out" "$full_req_backup"
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
  if [[ -n "${OVERRIDE_BUILD_DIR:-}" ]]; then
    build_dir="$OVERRIDE_BUILD_DIR"
    mkdir -p "$build_dir"
  else
    build_dir="$(mktemp -d "/tmp/wheelhouse-build.XXXXXX")"
  fi
  wheelhouse_tmp_zip="$(mktemp "/tmp/wheelhouse-archive.XXXXXX.zip")"
  req_out="$build_dir/_requirements.txt"
  req_sanitized_out="$build_dir/_requirements_sanitized.txt"
  failed_reqs_file="$(mktemp "/tmp/wheelhouse-failed.XXXXXX.txt")"
  uv_lock_path="$REPO_ROOT/uv.lock"
  cleanup_wheel_tmp() {
    if [[ -z "${OVERRIDE_BUILD_DIR:-}" ]]; then
      rm -rf "$build_dir"
    fi
    rm -f "$wheelhouse_tmp_zip"
    rm -f "$failed_reqs_file"
  }
  trap cleanup_wheel_tmp RETURN

  if [[ -n "${EXPORTED_REQUIREMENTS_PATH:-}" ]] && [[ -f "$EXPORTED_REQUIREMENTS_PATH" ]]; then
    requirements_source="pre-exported requirements"
    echo "Using pre-exported requirements from host: $EXPORTED_REQUIREMENTS_PATH"
    cp -f "$EXPORTED_REQUIREMENTS_PATH" "$req_out"
  elif [[ -f "$uv_lock_path" ]]; then
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

  # EXTRA_INDEXES parsed at the top of the function

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
      | xargs -0 -P "$WHEEL_JOBS" -I{} bash -c '
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
