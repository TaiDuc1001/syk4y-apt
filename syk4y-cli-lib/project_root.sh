#!/usr/bin/env bash
# Shared project-root resolution and path canonicalization helpers for syk4y.

syk4y_resolve_script_dir() {
  local src="${1:-${BASH_SOURCE[1]}}"
  while [[ -h "$src" ]]; do
    local dir
    dir="$(cd -P "$(dirname "$src")" 2>/dev/null && pwd)"
    src="$(readlink "$src")"
    [[ "$src" != /* ]] && src="$dir/$src"
  done
  cd -P "$(dirname "$src")" 2>/dev/null && pwd
}

syk4y_canonicalize_dir() {
  local target="$1"
  if [[ -z "$target" ]] || [[ ! -d "$target" ]]; then
    return 1
  fi
  (cd "$target" 2>/dev/null && pwd -P)
}

# Resolve project root according to standard syk4y resolution rules:
# 1. Explicit --repo-root takes priority.
# 2. Handle execution from within kaggle_upload/ (detect containing project).
# 3. Detect containing project root via project markers or containing Git worktree.
# 4. Fall back to current directory where supported.
# 5. Clear diagnostic when root detection is ambiguous or invalid.
syk4y_resolve_project_root() {
  local explicit_root="${1:-}"
  local start_dir="${2:-$(pwd)}"
  local resolved=""

  # Rule 1: Explicit --repo-root takes priority
  if [[ -n "$explicit_root" ]]; then
    if ! resolved="$(syk4y_canonicalize_dir "$explicit_root")"; then
      echo "Error: invalid --repo-root directory: '$explicit_root'" >&2
      return 1
    fi
    printf '%s\n' "$resolved"
    return 0
  fi

  if ! start_dir="$(syk4y_canonicalize_dir "$start_dir")"; then
    echo "Error: current directory '$start_dir' cannot be accessed or does not exist." >&2
    return 1
  fi

  # Rule 2: Handle execution from within kaggle_upload/
  # Walk upwards to find if we are inside a kaggle_upload directory.
  # If nested (e.g. project/kaggle_upload/kaggle_upload), find the topmost kaggle_upload.
  local current="$start_dir"
  local topmost_kaggle_upload=""

  while [[ -n "$current" && "$current" != "/" ]]; do
    if [[ "$(basename "$current")" == "kaggle_upload" ]]; then
      topmost_kaggle_upload="$current"
    fi
    current="$(dirname "$current")"
  done

  if [[ -n "$topmost_kaggle_upload" ]]; then
    local project_candidate
    project_candidate="$(dirname "$topmost_kaggle_upload")"
    if [[ -d "$project_candidate" ]]; then
      printf '%s\n' "$(syk4y_canonicalize_dir "$project_candidate")"
      return 0
    fi
  fi

  # Rule 3: Detect project root via project markers in start_dir
  if [[ -d "$start_dir/.git" || -f "$start_dir/.git" || \
        -f "$start_dir/pyproject.toml" || -f "$start_dir/setup.py" || \
        -f "$start_dir/uv.lock" || -d "$start_dir/.venv" || \
        -d "$start_dir/venv" || -d "$start_dir/kaggle_upload" ]]; then
    printf '%s\n' "$start_dir"
    return 0
  fi

  # Rule 4: Detect containing Git worktree where appropriate
  local git_root
  git_root="$(git -C "$start_dir" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -n "$git_root" && -d "$git_root" ]]; then
    printf '%s\n' "$(syk4y_canonicalize_dir "$git_root")"
    return 0
  fi

  # Rule 5: Fall back to current directory
  printf '%s\n' "$start_dir"
  return 0
}
