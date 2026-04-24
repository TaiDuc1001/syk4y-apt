#!/usr/bin/env bash

# Shared Kaggle credential helpers for syk4y shell subcommands.

syk4y_read_kaggle_field() {
  local pybin="$1"
  local field="$2"

  "$pybin" - "$field" <<'PY'
import json
import sys
from pathlib import Path

field = sys.argv[1]
cfg = Path.home() / '.kaggle' / 'kaggle.json'
if not cfg.exists():
    raise SystemExit(0)

try:
    data = json.loads(cfg.read_text(encoding='utf-8'))
except Exception:
    raise SystemExit(0)

value = data.get(field)
if isinstance(value, str) and value.strip():
    print(value.strip())
PY
}

syk4y_resolve_kaggle_username() {
  local pybin="$1"
  local strict_mode="${2:-0}"
  local detected=""

  if [[ -n "${KAGGLE_USERNAME:-}" ]]; then
    printf '%s\n' "$KAGGLE_USERNAME"
    return 0
  fi

  detected="$(syk4y_read_kaggle_field "$pybin" username || true)"
  if [[ -n "$detected" ]]; then
    printf '%s\n' "$detected"
    return 0
  fi

  if [[ "$strict_mode" == "1" ]]; then
    echo "Error: could not resolve Kaggle username. Set KAGGLE_USERNAME or ~/.kaggle/kaggle.json." >&2
    exit 1
  fi

  echo "Warning: Kaggle username not found. Using placeholder 'your-kaggle-username'." >&2
  echo "Set KAGGLE_USERNAME (or run 'syk4y kaggle login') to generate real dataset ids." >&2
  printf '%s\n' "your-kaggle-username"
}

syk4y_has_kaggle_credentials() {
  local pybin="$1"

  if [[ -n "${KAGGLE_USERNAME:-}" ]] && [[ -n "${KAGGLE_KEY:-}" ]]; then
    return 0
  fi

  "$pybin" - <<'PY'
import json
from pathlib import Path

cfg = Path.home() / '.kaggle' / 'kaggle.json'
if not cfg.exists():
    raise SystemExit(1)
try:
    data = json.loads(cfg.read_text(encoding='utf-8'))
except Exception:
    raise SystemExit(1)

username = data.get('username', '')
key = data.get('key', '')
if isinstance(username, str) and username.strip() and isinstance(key, str) and key.strip():
    raise SystemExit(0)
raise SystemExit(1)
PY
}
