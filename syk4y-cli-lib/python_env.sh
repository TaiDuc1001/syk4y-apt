#!/usr/bin/env bash

# Shared Python environment helpers for syk4y shell subcommands.

syk4y_print_python_install_hint() {
  cat >&2 <<'HINT'
No usable Python interpreter was found.
Install one of:
  - Ubuntu/Debian: sudo apt update && sudo apt install -y python3 python3-venv
  - Or create local venv: uv venv .venv
HINT
}

syk4y_is_python_bin_usable() {
  local py="$1"
  if [[ -z "$py" ]]; then
    return 1
  fi
  if [[ "$py" == */* ]]; then
    [[ -x "$py" ]]
    return
  fi
  command -v "$py" >/dev/null 2>&1
}

syk4y_find_repo_venv_python() {
  local repo_root="$1"
  local candidate dir_name dir_lc
  local -a preferred=(.venv venv .env env)

  for dir_name in "${preferred[@]}"; do
    candidate="$repo_root/$dir_name/bin/python"
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  while IFS= read -r dir_name; do
    dir_lc="${dir_name,,}"
    if [[ "$dir_lc" != *venv* ]] && [[ "$dir_lc" != "env" ]] && [[ "$dir_lc" != ".env" ]] && [[ "$dir_lc" != env-* ]] && [[ "$dir_lc" != .env-* ]] && [[ "$dir_lc" != *-env ]]; then
      continue
    fi
    candidate="$repo_root/$dir_name/bin/python"
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done < <(find "$repo_root" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | LC_ALL=C sort -u)

  # Last resort: accept any direct child virtual environment layout,
  # even when directory naming does not contain "venv"/"env".
  while IFS= read -r dir_name; do
    candidate="$repo_root/$dir_name/bin/python"
    if [[ -x "$candidate" ]] && [[ -f "$repo_root/$dir_name/pyvenv.cfg" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done < <(find "$repo_root" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | LC_ALL=C sort -u)

  return 1
}

syk4y_resolve_python_bin() {
  local repo_root="${1:-$(pwd)}"
  local configured="${PYTHON_BIN:-}"
  local detected=""

  if [[ -n "$configured" ]]; then
    if syk4y_is_python_bin_usable "$configured"; then
      printf '%s\n' "$configured"
      return 0
    fi
    return 1
  fi

  detected="$(syk4y_find_repo_venv_python "$repo_root" || true)"
  if [[ -n "$detected" ]]; then
    printf '%s\n' "$detected"
    return 0
  fi

  if command -v uv >/dev/null 2>&1; then
    detected="$(uv --directory "$repo_root" python find 2>/dev/null || true)"
    if syk4y_is_python_bin_usable "$detected"; then
      printf '%s\n' "$detected"
      return 0
    fi
  fi

  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s\n' "python"
    return 0
  fi

  return 1
}

syk4y_resolve_python_bin_or_die() {
  local repo_root="${1:-$(pwd)}"
  local context="${2:-syk4y}"
  local resolved=""

  resolved="$(syk4y_resolve_python_bin "$repo_root" || true)"
  if [[ -n "$resolved" ]]; then
    printf '%s\n' "$resolved"
    return 0
  fi

  if [[ -n "${PYTHON_BIN:-}" ]]; then
    echo "Error: PYTHON_BIN points to unusable interpreter: ${PYTHON_BIN}" >&2
  else
    echo "Error: Python is required for ${context}." >&2
  fi
  syk4y_print_python_install_hint
  exit 1
}
