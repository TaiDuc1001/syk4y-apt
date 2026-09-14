from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RENDER_SCRIPT = REPO_ROOT / "scripts" / "render-homebrew-formula.sh"


def test_formula_renderer_emits_a_self_contained_macos_formula(tmp_path: Path) -> None:
    output = tmp_path / "syk4y.rb"
    source_sha = "a" * 40
    asset_sha = "b" * 64

    completed = subprocess.run(
        [
            "bash",
            str(RENDER_SCRIPT),
            "--version",
            "0.0.123",
            "--source-url",
            f"https://github.com/TaiDuc1001/syk4y-apt/archive/{source_sha}.tar.gz",
            "--sha256",
            asset_sha,
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    formula = output.read_text(encoding="utf-8")
    assert "class Syk4y < Formula" in formula
    assert 'depends_on "bash"' in formula
    assert 'version "0.0.123"' in formula
    assert asset_sha in formula
    assert f"archive/{source_sha}.tar.gz" in formula
    assert 'exec "#{Formula["bash"].opt_bin}/bash"' in formula
    assert "syk4y-doctor" in formula
    assert "make-gen-full-repo.sh" in formula
    assert 'assert_match "syk4y - Kaggle artifact automation tool"' in formula


def test_formula_renderer_rejects_non_sha256_checksum(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            "bash",
            str(RENDER_SCRIPT),
            "--version",
            "0.0.1",
            "--source-url",
            "https://github.com/TaiDuc1001/syk4y-apt/archive/abc.tar.gz",
            "--sha256",
            "not-a-checksum",
            "--output",
            str(tmp_path / "syk4y.rb"),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "SHA-256" in completed.stderr
