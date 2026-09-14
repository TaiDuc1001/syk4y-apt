#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Render the Homebrew formula for a released syk4y source snapshot.

Usage:
  scripts/render-homebrew-formula.sh \
    --version VERSION \
    --source-url URL \
    --sha256 SHA256 \
    --output Formula/syk4y.rb
USAGE
}

VERSION=""
SOURCE_URL=""
SHA256=""
OUTPUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version|--source-url|--sha256|--output)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      case "$1" in
        --version) VERSION="$2" ;;
        --source-url) SOURCE_URL="$2" ;;
        --sha256) SHA256="$2" ;;
        --output) OUTPUT="$2" ;;
      esac
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

[[ "$VERSION" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]] || {
  echo "Version must contain one to three numeric components." >&2
  exit 2
}
[[ "$SOURCE_URL" =~ ^https://github\.com/TaiDuc1001/syk4y-apt/archive/[A-Za-z0-9._/-]+\.tar\.gz$ ]] || {
  echo "Source URL must be a TaiDuc1001/syk4y-apt GitHub archive URL." >&2
  exit 2
}
[[ "$SHA256" =~ ^[0-9a-fA-F]{64}$ ]] || {
  echo "SHA-256 must be exactly 64 hexadecimal characters." >&2
  exit 2
}
[[ -n "$OUTPUT" ]] || {
  echo "--output is required." >&2
  exit 2
}

mkdir -p "$(dirname "$OUTPUT")"
cat > "$OUTPUT" <<FORMULA
class Syk4y < Formula
  desc "Kaggle artifact automation command-line tool"
  homepage "https://github.com/TaiDuc1001/syk4y-apt"
  url "$SOURCE_URL"
  version "$VERSION"
  sha256 "$SHA256"

  depends_on "bash"

  def install
    libexec.install "syk4y", "syk4y-init", "syk4y-gen", "syk4y-kaggle", "syk4y-doctor"
    libexec.install "syk4y-lib", "syk4y-cli-lib", "templates"

    command_targets = {
      "syk4y" => "syk4y",
      "syk4y-init" => "syk4y-init",
      "syk4y-gen" => "syk4y-gen",
      "syk4y-kaggle" => "syk4y-kaggle",
      "syk4y-doctor" => "syk4y-doctor",
      "make-gen-full-repo.sh" => "syk4y-gen",
    }

    command_targets.each do |command_name, target|
      (bin/command_name).write <<~EOS
        #!/bin/sh
        exec "#{Formula["bash"].opt_bin}/bash" "#{libexec}/#{target}" "\$@"
      EOS
    end
  end

  test do
    assert_match "syk4y - Kaggle artifact automation tool", shell_output("#{bin}/syk4y --help")
  end
end
FORMULA
