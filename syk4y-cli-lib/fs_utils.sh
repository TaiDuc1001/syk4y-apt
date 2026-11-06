#!/usr/bin/env bash
# Shared filesystem safety, portability, and concurrency utilities for syk4y.

# Safe mktemp helper compatible with both GNU (Linux) and BSD (macOS) mktemp.
# Guarantees template ends with XXXXXX to avoid BSD mkstemp failure.
syk4y_mktemp() {
  local dir="${1:-${TMPDIR:-/tmp}}"
  local prefix="${2:-syk4y}"
  mkdir -p "$dir" 2>/dev/null || true
  mktemp "$dir/${prefix}.XXXXXX"
}

syk4y_mktemp_dir() {
  local dir="${1:-${TMPDIR:-/tmp}}"
  local prefix="${2:-syk4y-dir}"
  mkdir -p "$dir" 2>/dev/null || true
  mktemp -d "$dir/${prefix}.XXXXXX"
}

# Computes SHA256 hash portably across Linux and macOS.
# Usage: syk4y_sha256 [file] (or pipes data via stdin if no file given)
syk4y_sha256() {
  local file="${1:-}"
  if command -v sha256sum >/dev/null 2>&1; then
    if [[ -n "$file" ]]; then
      sha256sum "$file" | awk '{print $1}'
    else
      sha256sum | awk '{print $1}'
    fi
  elif command -v shasum >/dev/null 2>&1; then
    if [[ -n "$file" ]]; then
      shasum -a 256 "$file" | awk '{print $1}'
    else
      shasum -a 256 | awk '{print $1}'
    fi
  else
    local py="${PYTHON_BIN:-python3}"
    if command -v "$py" >/dev/null 2>&1; then
      if [[ -n "$file" ]]; then
        "$py" -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())" "$file"
      else
        "$py" -c "import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"
      fi
    else
      echo "Error: no SHA256 utility found (sha256sum, shasum, or python3 required)." >&2
      return 1
    fi
  fi
}

# Safely remove a directory or file only if it is strictly contained inside allowed_base.
# Prevents accidental deletion of root, home, or unmanaged directories.
syk4y_safe_rm_rf() {
  local target="$1"
  local allowed_base="$2"

  if [[ -z "$target" ]] || [[ -z "$allowed_base" ]]; then
    echo "Error: syk4y_safe_rm_rf called with empty target or allowed_base." >&2
    return 1
  fi

  local target_abs allowed_base_abs
  if ! target_abs="$(cd -P "$(dirname "$target")" 2>/dev/null && pwd)/$(basename "$target")"; then
    return 0  # Does not exist
  fi
  if ! allowed_base_abs="$(cd -P "$allowed_base" 2>/dev/null && pwd)"; then
    echo "Error: allowed base '$allowed_base' does not exist." >&2
    return 1
  fi

  # Guard against root, home, or exact match with allowed_base
  if [[ "$target_abs" == "/" || "$target_abs" == "$HOME" || "$target_abs" == "$allowed_base_abs" ]]; then
    echo "Error: refusing to remove unsafe directory: '$target_abs'" >&2
    return 1
  fi

  # Verify target is strictly inside allowed_base_abs
  if [[ "$target_abs" != "$allowed_base_abs"/* ]]; then
    echo "Error: refusing to remove '$target_abs' outside allowed base '$allowed_base_abs'." >&2
    return 1
  fi

  rm -rf -- "$target_abs"
}

# Concurrency lock helpers
syk4y_acquire_lock() {
  local lock_dir="$1"
  local description="${2:-operation}"
  local pid_file="$lock_dir/pid"

  if mkdir "$lock_dir" 2>/dev/null; then
    printf '%s\n' "$$" > "$pid_file"
    return 0
  fi

  # Lock directory exists; check if owning process is still alive
  if [[ -f "$pid_file" ]]; then
    local lock_pid
    lock_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$lock_pid" ]] && [[ "$lock_pid" =~ ^[0-9]+$ ]]; then
      if kill -0 "$lock_pid" 2>/dev/null; then
        echo "Error: concurrent syk4y $description in progress (PID: $lock_pid)." >&2
        echo "Lock directory: $lock_dir" >&2
        return 1
      else
        # Stale lock from dead process
        echo "Notice: removing stale lock from terminated process PID $lock_pid." >&2
        rm -rf -- "$lock_dir"
        if mkdir "$lock_dir" 2>/dev/null; then
          printf '%s\n' "$$" > "$pid_file"
          return 0
        fi
      fi
    fi
  fi

  echo "Error: failed to acquire lock on '$lock_dir'." >&2
  return 1
}

syk4y_release_lock() {
  local lock_dir="$1"
  if [[ -d "$lock_dir" ]]; then
    rm -rf -- "$lock_dir"
  fi
}
