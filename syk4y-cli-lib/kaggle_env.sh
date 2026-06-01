#!/usr/bin/env bash

# Shared Kaggle credential helpers for syk4y shell subcommands.

syk4y_read_kaggle_field() {
  local pybin="$1"
  local field="$2"

  "$pybin" - "$field" <<'PY'
import json
import os
import sys
from pathlib import Path

field = sys.argv[1]

def _wsl_windows_path(value):
    if not value or len(value) < 3 or value[1:3] != ':\\':
        return None
    drive = value[0].lower()
    rest = value[3:].replace('\\', '/')
    return Path('/mnt') / drive / rest

def _is_wsl():
    if os.environ.get('WSL_DISTRO_NAME'):
        return True
    try:
        return 'microsoft' in Path('/proc/version').read_text(encoding='utf-8', errors='ignore').lower()
    except Exception:
        return False

def _candidate_paths():
    seen = set()

    def add(path):
        if path is None:
            return
        try:
            resolved = Path(path).expanduser()
        except Exception:
            return
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        yield resolved

    config_dir = os.environ.get('KAGGLE_CONFIG_DIR')
    if config_dir:
        yield from add(Path(config_dir) / 'kaggle.json')

    yield from add(Path.home() / '.kaggle' / 'kaggle.json')

    if not _is_wsl():
        return

    userprofile = _wsl_windows_path(os.environ.get('USERPROFILE', ''))
    if userprofile is not None:
        yield from add(userprofile / '.kaggle' / 'kaggle.json')

    parts = Path.cwd().parts
    if len(parts) >= 5 and parts[1] == 'mnt' and parts[3] == 'Users':
        yield from add(Path('/', *parts[1:5]) / '.kaggle' / 'kaggle.json')

    for users_root in sorted(Path('/mnt').glob('[a-zA-Z]/Users')):
        try:
            user_dirs = sorted(users_root.iterdir(), key=lambda p: p.name.lower())
        except Exception:
            continue
        for user_dir in user_dirs:
            yield from add(user_dir / '.kaggle' / 'kaggle.json')

for cfg in _candidate_paths():
    if not cfg.exists():
        continue
    try:
        data = json.loads(cfg.read_text(encoding='utf-8'))
    except Exception:
        continue
    value = data.get(field)
    if isinstance(value, str) and value.strip():
        print(value.strip())
        raise SystemExit(0)

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

  local username key
  username="$(syk4y_read_kaggle_field "$pybin" username || true)"
  key="$(syk4y_read_kaggle_field "$pybin" key || true)"
  [[ -n "$username" && -n "$key" ]]
}

syk4y_export_kaggle_credentials() {
  local pybin="$1"
  local username key

  username="${KAGGLE_USERNAME:-}"
  key="${KAGGLE_KEY:-}"

  if [[ -z "$username" ]]; then
    username="$(syk4y_read_kaggle_field "$pybin" username || true)"
  fi
  if [[ -z "$key" ]]; then
    key="$(syk4y_read_kaggle_field "$pybin" key || true)"
  fi

  if [[ -n "$username" ]]; then
    export KAGGLE_USERNAME="$username"
  fi
  if [[ -n "$key" ]]; then
    export KAGGLE_KEY="$key"
  fi
}
