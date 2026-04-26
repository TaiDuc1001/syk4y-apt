#!/usr/bin/env bash

# Shared .gitignore helpers for syk4y shell subcommands.

syk4y_ensure_kaggle_upload_gitignore() {
  local repo_root="$1"
  local gitignore_path="$repo_root/.gitignore"
  local entry="kaggle_upload/"
  local line=""

  if [[ -f "$gitignore_path" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      if [[ "$line" == "$entry" ]] || [[ "$line" == "kaggle_upload" ]]; then
        return 0
      fi
    done < "$gitignore_path"
  fi

  if [[ -f "$gitignore_path" ]] && [[ -s "$gitignore_path" ]]; then
    if [[ "$(tail -c 1 "$gitignore_path" 2>/dev/null || true)" != $'\n' ]]; then
      printf '\n' >> "$gitignore_path"
    fi
  fi

  printf '%s\n' "$entry" >> "$gitignore_path"
  echo "Added to .gitignore: $entry"
}
