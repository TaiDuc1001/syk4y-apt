# Runtime and prerequisite helpers for `syk4y kaggle upload`.

resolve_wheelhouse_python() {
  local wheel_py="${WHEELHOUSE_PYTHON:-}"
  local detected=""
  if [[ -z "$wheel_py" ]]; then
    detected="$(syk4y_find_repo_venv_python "$REPO_ROOT" || true)"
    if [[ -n "$detected" ]]; then
      wheel_py="$detected"
    else
      wheel_py="$PYTHON_BIN"
    fi
  fi

  if ! syk4y_is_python_bin_usable "$wheel_py"; then
    echo "Wheelhouse Python interpreter is not usable: $wheel_py" >&2
    syk4y_print_python_install_hint
    exit 1
  fi
  printf '%s\n' "$wheel_py"
}

has_uv_pip_for_python() {
  local py="$1"
  if ! command -v uv >/dev/null 2>&1; then
    return 1
  fi
  uv pip list --python "$py" >/dev/null 2>&1
}

ensure_pip() {
  local py="$1"
  if "$py" -m pip --version >/dev/null 2>&1; then
    return 0
  fi

  if has_uv_pip_for_python "$py"; then
    echo "pip module is missing in $py. Trying bootstrap via uv pip ..."
    if uv pip install --python "$py" pip >/dev/null 2>&1; then
      if "$py" -m pip --version >/dev/null 2>&1; then
        return 0
      fi
    fi
  fi

  echo "pip is missing in $py. Trying ensurepip ..."
  if "$py" -m ensurepip --upgrade >/dev/null 2>&1; then
    if "$py" -m pip --version >/dev/null 2>&1; then
      return 0
    fi
  fi

  echo "Error: pip is unavailable in '$py'." >&2
  if command -v uv >/dev/null 2>&1; then
    echo "Try: uv pip install --python \"$py\" pip" >&2
  fi
  echo "Or recreate a local environment: uv venv \"$REPO_ROOT/.venv\"" >&2
  echo "Last resort (system-wide): sudo apt update && sudo apt install -y python3-pip python3-venv" >&2
  exit 1
}

resolve_kaggle_cmd() {
  if command -v kaggle >/dev/null 2>&1; then
    KAGGLE_CMD=(kaggle)
    return 0
  fi
  if "$PYTHON_BIN" -m kaggle --help >/dev/null 2>&1; then
    KAGGLE_CMD=("$PYTHON_BIN" -m kaggle)
    return 0
  fi
  return 1
}

ensure_kaggle_cli() {
  if resolve_kaggle_cmd; then
    return 0
  fi

  echo "Kaggle CLI is missing. Attempting auto-install for: $PYTHON_BIN"

  if command -v uv >/dev/null 2>&1; then
    if uv pip install --python "$PYTHON_BIN" kaggle >/dev/null 2>&1; then
      if resolve_kaggle_cmd; then
        return 0
      fi
    fi
  fi

  if "$PYTHON_BIN" -m pip install --disable-pip-version-check kaggle >/dev/null 2>&1; then
    if resolve_kaggle_cmd; then
      return 0
    fi
  fi

  if "$PYTHON_BIN" -m pip install --disable-pip-version-check --user kaggle >/dev/null 2>&1; then
    if resolve_kaggle_cmd; then
      return 0
    fi
  fi

  echo "Error: Kaggle CLI is required for upload (either 'kaggle' or '$PYTHON_BIN -m kaggle')." >&2
  echo "Auto-install attempt failed." >&2
  if command -v uv >/dev/null 2>&1; then
    echo "Try manually: uv pip install --python \"$PYTHON_BIN\" kaggle" >&2
  fi
  echo "Or: \"$PYTHON_BIN\" -m pip install --user kaggle" >&2
  exit 1
}

has_kaggle_credentials() {
  syk4y_has_kaggle_credentials "$PYTHON_BIN"
}

ensure_kaggle_upload_prereqs() {
  ensure_kaggle_cli
  if has_kaggle_credentials; then
    return
  fi
  echo "Error: Kaggle credentials are not configured. Upload requires authentication." >&2
  echo "Run: syk4y kaggle login" >&2
  echo "Or set env vars: KAGGLE_USERNAME and KAGGLE_KEY" >&2
  exit 1
}
