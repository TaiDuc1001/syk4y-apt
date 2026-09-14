from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-macos.yml"
README = REPO_ROOT / "README.md"


def test_macos_release_workflow_updates_the_homebrew_formula() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Update Homebrew formula" in workflow
    assert "webfactory/ssh-agent@v0.9.1" in workflow
    assert "mihtriii/homebrew-syk4y.git" in workflow
    assert "HOMEBREW_TAP_DEPLOY_KEY" in workflow
    assert "scripts/render-homebrew-formula.sh" in workflow
    assert "archive/${GITHUB_SHA}.tar.gz" in workflow
    assert "push origin HEAD:main" in workflow


def test_macos_readme_documents_canonical_homebrew_tap_installation() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "brew install mihtriii/syk4y/syk4y" in readme
    assert "brew update" in readme
    assert "brew upgrade syk4y" in readme
