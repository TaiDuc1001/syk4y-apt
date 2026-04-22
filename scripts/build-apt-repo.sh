#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Build a static APT repository directory from .deb files.

Usage:
  scripts/build-apt-repo.sh --output-dir DIR --deb FILE [--deb FILE ...] [options]

Options:
  --output-dir DIR      Output directory for APT repo (required)
  --deb FILE            .deb file to include (repeatable; required)
  --origin TEXT         Release Origin (default: syk4y)
  --label TEXT          Release Label (default: syk4y)
  --suite NAME          Suite/Codename (default: stable)
  --component NAME      Component (default: main)
  -h, --help            Show this help
USAGE
}

OUTPUT_DIR=""
ORIGIN="syk4y"
LABEL="syk4y"
SUITE="stable"
COMPONENT="main"
DEBS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --deb)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      DEBS+=("$2")
      shift 2
      ;;
    --origin)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      ORIGIN="$2"
      shift 2
      ;;
    --label)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      LABEL="$2"
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
    --component)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      COMPONENT="$2"
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

if [[ -z "$OUTPUT_DIR" ]]; then
  echo "Error: --output-dir is required." >&2
  exit 2
fi
if [[ "${#DEBS[@]}" -eq 0 ]]; then
  echo "Error: at least one --deb is required." >&2
  exit 2
fi

for cmd in dpkg-scanpackages apt-ftparchive gzip; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Error: $cmd is required." >&2
    exit 1
  fi
done

for deb in "${DEBS[@]}"; do
  if [[ ! -f "$deb" ]]; then
    echo "Error: missing .deb file '$deb'." >&2
    exit 1
  fi
done

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_ABS="$OUTPUT_DIR"
if [[ "$OUT_ABS" != /* ]]; then
  OUT_ABS="$ROOT_DIR/$OUT_ABS"
fi

rm -rf "$OUT_ABS"
mkdir -p "$OUT_ABS"

POOL_DIR="$OUT_ABS/pool/$COMPONENT/s/syk4y"
DIST_DIR="$OUT_ABS/dists/$SUITE/$COMPONENT/binary-all"
mkdir -p "$POOL_DIR" "$DIST_DIR"

for deb in "${DEBS[@]}"; do
  cp "$deb" "$POOL_DIR/"
done

(
  cd "$OUT_ABS"
  dpkg-scanpackages --multiversion "pool" > "dists/$SUITE/$COMPONENT/binary-all/Packages"
)
gzip -n -9 -c "$DIST_DIR/Packages" > "$DIST_DIR/Packages.gz"

apt-ftparchive \
  -o "APT::FTPArchive::Release::Origin=$ORIGIN" \
  -o "APT::FTPArchive::Release::Label=$LABEL" \
  -o "APT::FTPArchive::Release::Suite=$SUITE" \
  -o "APT::FTPArchive::Release::Codename=$SUITE" \
  -o "APT::FTPArchive::Release::Components=$COMPONENT" \
  -o "APT::FTPArchive::Release::Architectures=all" \
  release "$OUT_ABS/dists/$SUITE" > "$OUT_ABS/dists/$SUITE/Release"

echo "Built APT repository at: $OUT_ABS"
