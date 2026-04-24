#!/usr/bin/env bash

# Shared string helpers for syk4y shell subcommands.

syk4y_slugify() {
  local value="$1"
  value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
  value="$(printf '%s' "$value" | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')"
  if [[ -z "$value" ]]; then
    value="dataset-artifacts"
  fi
  printf '%s\n' "$value"
}
