#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Sign an APT repository Release file and export public key.

Usage:
  scripts/sign-apt-release.sh --repo-dir DIR --suite NAME --key-id KEY_ID [options]

Options:
  --repo-dir DIR           Root directory of static APT repo (required)
  --suite NAME             Suite/codename (required, e.g. stable)
  --key-id KEY_ID          GPG key ID/fingerprint to sign with (required)
  --public-key-output FILE Output .gpg keyring path (default: <repo-dir>/keys/syk4y-archive-keyring.gpg)
  --public-key-asc FILE    Optional output armored public key path
  -h, --help               Show this help

Environment:
  APT_GPG_PASSPHRASE       Passphrase for signing key (optional)
USAGE
}

REPO_DIR=""
SUITE=""
KEY_ID=""
PUBLIC_KEY_OUTPUT=""
PUBLIC_KEY_ASC=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-dir)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      REPO_DIR="$2"
      shift 2
      ;;
    --suite)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      SUITE="$2"
      shift 2
      ;;
    --key-id)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      KEY_ID="$2"
      shift 2
      ;;
    --public-key-output)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      PUBLIC_KEY_OUTPUT="$2"
      shift 2
      ;;
    --public-key-asc)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      PUBLIC_KEY_ASC="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$REPO_DIR" || -z "$SUITE" || -z "$KEY_ID" ]]; then
  echo "Error: --repo-dir, --suite, and --key-id are required." >&2
  exit 2
fi

for cmd in gpg; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Error: $cmd is required." >&2
    exit 1
  fi
done

if [[ "$REPO_DIR" != /* ]]; then
  REPO_DIR="$(pwd)/$REPO_DIR"
fi

RELEASE_FILE="$REPO_DIR/dists/$SUITE/Release"
if [[ ! -f "$RELEASE_FILE" ]]; then
  echo "Error: missing Release file: $RELEASE_FILE" >&2
  exit 1
fi

if [[ -z "$PUBLIC_KEY_OUTPUT" ]]; then
  PUBLIC_KEY_OUTPUT="$REPO_DIR/keys/syk4y-archive-keyring.gpg"
fi
if [[ "$PUBLIC_KEY_OUTPUT" != /* ]]; then
  PUBLIC_KEY_OUTPUT="$REPO_DIR/$PUBLIC_KEY_OUTPUT"
fi

mkdir -p "$(dirname "$PUBLIC_KEY_OUTPUT")"

passphrase="${APT_GPG_PASSPHRASE:-}"
pass_args=(--pinentry-mode loopback)
if [[ -n "$passphrase" ]]; then
  pass_args+=(--passphrase "$passphrase")
fi

# Generate InRelease and detached signature for apt clients.
gpg --batch --yes \
  "${pass_args[@]}" \
  --default-key "$KEY_ID" \
  --clearsign \
  --output "$REPO_DIR/dists/$SUITE/InRelease" \
  "$RELEASE_FILE"

gpg --batch --yes \
  "${pass_args[@]}" \
  --default-key "$KEY_ID" \
  --armor --detach-sign \
  --output "$REPO_DIR/dists/$SUITE/Release.gpg" \
  "$RELEASE_FILE"

# Export binary keyring for signed-by usage.
gpg --batch --yes --export "$KEY_ID" | gpg --dearmor > "$PUBLIC_KEY_OUTPUT"

if [[ -n "$PUBLIC_KEY_ASC" ]]; then
  if [[ "$PUBLIC_KEY_ASC" != /* ]]; then
    PUBLIC_KEY_ASC="$REPO_DIR/$PUBLIC_KEY_ASC"
  fi
  mkdir -p "$(dirname "$PUBLIC_KEY_ASC")"
  gpg --batch --yes --armor --export "$KEY_ID" > "$PUBLIC_KEY_ASC"
fi

echo "Signed APT metadata for suite '$SUITE'"
echo "Public key exported to: $PUBLIC_KEY_OUTPUT"
