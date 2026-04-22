#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Build a .deb package for syk4y.

Usage:
  scripts/build-deb.sh --version VERSION [--output-dir DIR]

Options:
  --version VERSION   Debian package version (required)
  --output-dir DIR    Output directory (default: dist/deb)
  -h, --help          Show this help
USAGE
}

VERSION=""
OUTPUT_DIR="dist/deb"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
      VERSION="$2"
      shift 2
      ;;
    --output-dir)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        exit 2
      fi
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

if [[ -z "$VERSION" ]]; then
  echo "Error: --version is required." >&2
  exit 2
fi

if [[ ! "$VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$ ]]; then
  echo "Error: invalid Debian version '$VERSION'." >&2
  exit 2
fi

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REQUIRED_SCRIPTS=(
  syk4y
  syk4y-init
  syk4y-gen
  syk4y-kaggle
  syk4y-doctor
)

for f in "${REQUIRED_SCRIPTS[@]}"; do
  if [[ ! -f "$ROOT_DIR/$f" ]]; then
    echo "Error: missing required script '$f'." >&2
    exit 1
  fi
done

mkdir -p "$ROOT_DIR/$OUTPUT_DIR"
STAGE_DIR="$(mktemp -d /tmp/syk4y-deb-stage.XXXXXX)"
cleanup() {
  rm -rf "$STAGE_DIR"
}
trap cleanup EXIT

PKG_NAME="syk4y"
PKG_ARCH="all"
PKG_DIR="$STAGE_DIR/${PKG_NAME}_${VERSION}_${PKG_ARCH}"

mkdir -p "$PKG_DIR/DEBIAN" "$PKG_DIR/usr/lib/syk4y" "$PKG_DIR/usr/bin"

for f in "${REQUIRED_SCRIPTS[@]}"; do
  install -m 0755 "$ROOT_DIR/$f" "$PKG_DIR/usr/lib/syk4y/$f"
done

make_wrapper() {
  local name="$1"
  local target="$2"
  cat > "$PKG_DIR/usr/bin/$name" <<WRAP
#!/usr/bin/env bash
set -euo pipefail
exec /usr/lib/syk4y/$target "\$@"
WRAP
  chmod 0755 "$PKG_DIR/usr/bin/$name"
}

make_wrapper "syk4y" "syk4y"
make_wrapper "syk4y-init" "syk4y-init"
make_wrapper "syk4y-gen" "syk4y-gen"
make_wrapper "syk4y-kaggle" "syk4y-kaggle"
make_wrapper "syk4y-doctor" "syk4y-doctor"
make_wrapper "make-gen-full-repo.sh" "syk4y-gen"

INSTALLED_SIZE="$(du -sk "$PKG_DIR/usr" | awk '{print $1}')"

cat > "$PKG_DIR/DEBIAN/control" <<CONTROL
Package: $PKG_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $PKG_ARCH
Maintainer: TaiDuc1001 <taiduc1001@users.noreply.github.com>
Depends: bash, git
Installed-Size: $INSTALLED_SIZE
Description: syk4y Kaggle artifact automation CLI
 Shell-based utility to scaffold Kaggle upload datasets, build wheelhouse
 artifacts, and upload only changed artifacts.
CONTROL

OUT_DEB="$ROOT_DIR/$OUTPUT_DIR/${PKG_NAME}_${VERSION}_${PKG_ARCH}.deb"
rm -f "$OUT_DEB"
dpkg-deb --build --root-owner-group "$PKG_DIR" "$OUT_DEB" >/dev/null

echo "Built package: $OUT_DEB"
