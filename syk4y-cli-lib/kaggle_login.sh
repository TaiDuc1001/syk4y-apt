# Shared implementation for `syk4y kaggle login`.

login_usage() {
  cat "$SCRIPT_DIR/templates/kaggle-login-usage.txt"
}

kaggle_login() {
  local username="${KAGGLE_USERNAME:-}"
  local key="${KAGGLE_KEY:-}"
  local force=0
  local non_interactive=0
  local kaggle_dir="$HOME/.kaggle"
  local kaggle_json="$kaggle_dir/kaggle.json"
  local has_explicit_creds=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --username)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for $1" >&2
          exit 2
        fi
        username="$2"
        shift 2
        ;;
      --key)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for $1" >&2
          exit 2
        fi
        key="$2"
        shift 2
        ;;
      --force)
        force=1
        shift
        ;;
      --non-interactive)
        non_interactive=1
        shift
        ;;
      -h|--help)
        login_usage
        exit 0
        ;;
      *)
        echo "Unknown login option: $1" >&2
        login_usage >&2
        exit 2
        ;;
    esac
  done

  if [[ -n "$username" || -n "$key" ]]; then
    has_explicit_creds=1
  fi

  if [[ "$force" -eq 0 ]] && [[ "$has_explicit_creds" -eq 0 ]] && [[ -f "$kaggle_json" ]]; then
    local existing_status existing_username parse_out
    parse_out="$(
      "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_login_json_cli.py" status "$kaggle_json"
    )"
    existing_status="${parse_out%%$'\t'*}"
    existing_username="${parse_out#*$'\t'}"

    if [[ "$existing_status" == "OK" ]]; then
      echo "Kaggle credentials already configured."
      echo "  file: $kaggle_json"
      echo "  username: $existing_username"
      if command -v kaggle >/dev/null 2>&1; then
        if kaggle datasets list -s test -p 1 >/dev/null 2>&1; then
          echo "  auth check: OK"
        else
          echo "  auth check: failed (username/key may be invalid)."
        fi
      else
        echo "  auth check: skipped (kaggle CLI not found)"
      fi
      echo "Use --force or pass --username/--key to overwrite credentials."
      return 0
    fi

    echo "Existing $kaggle_json is invalid. Reconfiguring..."
  fi

  if [[ -z "$username" ]] || [[ -z "$key" ]]; then
    if [[ "$non_interactive" -eq 1 ]]; then
      echo "Error: missing Kaggle credentials. Provide --username/--key or KAGGLE_USERNAME/KAGGLE_KEY." >&2
      exit 1
    fi

    if [[ -z "$username" ]]; then
      read -r -p "Kaggle username: " username
    fi
    if [[ -z "$key" ]]; then
      read -r -s -p "Kaggle API key: " key
      echo
    fi
  fi

  if [[ -z "$username" ]] || [[ -z "$key" ]]; then
    echo "Error: username/key cannot be empty." >&2
    exit 1
  fi

  mkdir -p "$kaggle_dir"
  chmod 700 "$kaggle_dir"

  "$PYTHON_BIN" "$SCRIPT_DIR/syk4y-lib/kaggle_login_json_cli.py" write "$kaggle_json" "$username" "$key"

  chmod 600 "$kaggle_json"

  # Best-effort auth check if kaggle CLI is available.
  if command -v kaggle >/dev/null 2>&1; then
    if kaggle datasets list -s test -p 1 >/dev/null 2>&1; then
      echo "Kaggle credentials saved and verified: $kaggle_json"
    else
      echo "Kaggle credentials saved at $kaggle_json (verification command failed)." >&2
      echo "Check username/key and try: kaggle datasets list -s test -p 1" >&2
    fi
  else
    echo "Kaggle credentials saved at $kaggle_json"
    echo "Install kaggle CLI to verify: kaggle datasets list -s test -p 1"
  fi
}
