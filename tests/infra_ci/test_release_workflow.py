"""Contracts for the binary release workflow."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "release.yml"


def test_main_release_uses_main_build_tag_not_nightly() -> None:
    source = _RELEASE_WORKFLOW.read_text()

    assert "tag_name=main-build" in source
    assert "refs/tags/${{ needs.prepare.outputs.tag_name }}" in source
    assert 'gh release view "$tag_name"' in source
    assert "nightly" not in source


def test_main_binary_publish_runs_when_verify_is_skipped_on_push() -> None:
    source = _RELEASE_WORKFLOW.read_text()

    assert (
        "if: always() && (needs.prepare.outputs.channel == 'main' || "
        "needs.verify.result == 'success')"
    ) in source
    assert (
        "if: always() && needs.prepare.outputs.channel == 'main' && "
        "needs.build-binaries.result == 'success'"
    ) in source
