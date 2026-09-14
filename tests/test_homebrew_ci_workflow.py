from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-macos.yml"
README = REPO_ROOT / "README.md"


def test_macos_release_workflow_updates_the_homebrew_formula() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "[skip macos release]" in workflow
    assert "Update Homebrew formula" in workflow
    assert "scripts/render-homebrew-formula.sh" in workflow
    assert "archive/${GITHUB_SHA}.tar.gz" in workflow
    assert "git push origin HEAD:main" in workflow


def test_macos_readme_documents_custom_tap_installation() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "brew tap TaiDuc1001/syk4y-apt https://github.com/TaiDuc1001/syk4y-apt" in readme
    assert "brew trust --formula TaiDuc1001/syk4y-apt/syk4y" in readme
    assert "brew install TaiDuc1001/syk4y-apt/syk4y" in readme
