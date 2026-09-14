#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Build a macOS installer package for syk4y.

Usage:
  scripts/build-macos-pkg.sh --version VERSION [--output-dir DIR]

Options:
  --version VERSION   Package version (required)
  --output-dir DIR    Output directory (default: dist/macos)
  -h, --help          Show this help
USAGE
}

VERSION=""
OUTPUT_DIR="dist/macos"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      VERSION="$2"
      shift 2
      ;;
    --output-dir)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      OUTPUT_DIR="$2"
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

[[ -n "$VERSION" ]] || { echo "Error: --version is required." >&2; exit 2; }
[[ "$VERSION" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]] || {
  echo "Error: macOS package version must contain one to three numeric components (for example: 1.2.3)." >&2
  exit 2
}

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

command -v pkgbuild >/dev/null 2>&1 || {
  echo "Error: pkgbuild is required; run this on macOS with Xcode command-line tools." >&2
  exit 1
}

REQUIRED_SCRIPTS=(syk4y syk4y-init syk4y-gen syk4y-kaggle syk4y-doctor)
REQUIRED_DIRS=(syk4y-lib syk4y-cli-lib templates)
for path in "${REQUIRED_SCRIPTS[@]}" "${REQUIRED_DIRS[@]}"; do
  [[ -e "$ROOT_DIR/$path" ]] || { echo "Error: missing required path '$path'." >&2; exit 1; }
done

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/syk4y-pkg-stage.XXXXXX")"
trap 'rm -rf "$STAGE_DIR"' EXIT

PAYLOAD_ROOT="$STAGE_DIR/root"
INSTALL_DIR="/usr/local/lib/syk4y"
BIN_DIR="$PAYLOAD_ROOT/usr/local/bin"
LIB_DIR="$PAYLOAD_ROOT$INSTALL_DIR"
mkdir -p "$BIN_DIR" "$LIB_DIR"

for script in "${REQUIRED_SCRIPTS[@]}"; do
  install -m 0755 "$ROOT_DIR/$script" "$LIB_DIR/$script"
done
for directory in "${REQUIRED_DIRS[@]}"; do
  cp -R "$ROOT_DIR/$directory" "$LIB_DIR/$directory"
done

write_launcher() {
  local command_name="$1"
  local target_name="$2"
  cat > "$BIN_DIR/$command_name" <<LAUNCHER
#!/bin/sh
set -eu

for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
  if [ -x "\$candidate" ] && "\$candidate" -c '[ "\${BASH_VERSINFO[0]}" -ge 4 ]' >/dev/null 2>&1; then
    export PATH="\$(dirname "\$candidate"):\$PATH"
    exec "\$candidate" "$INSTALL_DIR/$target_name" "\$@"
  fi
done

echo "syk4y on macOS requires Bash 4 or newer." >&2
echo "Install it with: brew install bash" >&2
exit 1
LAUNCHER
  chmod 0755 "$BIN_DIR/$command_name"
}

for command_name in "${REQUIRED_SCRIPTS[@]}"; do
  write_launcher "$command_name" "$command_name"
done
write_launcher "make-gen-full-repo.sh" "syk4y-gen"

PACKAGE_PATH="$OUTPUT_DIR/syk4y-$VERSION-macos-universal.pkg"
rm -f "$PACKAGE_PATH"
pkgbuild \
  --root "$PAYLOAD_ROOT" \
  --identifier "com.github.taiduc1001.syk4y" \
  --version "$VERSION" \
  --install-location / \
  "$PACKAGE_PATH" >/dev/null

echo "Built macOS package: $PACKAGE_PATH"
