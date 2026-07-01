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


def test_main_build_has_distinct_binary_version() -> None:
    source = _RELEASE_WORKFLOW.read_text()

    assert "version_name: ${{ steps.meta.outputs.version_name }}" in source
    assert 'main_version="0.1.${year}.${month}.${day}+main.${short_sha}"' in source
    assert "version_name=${main_version}" in source


def test_binary_build_syncs_and_smokes_expected_version() -> None:
    source = _RELEASE_WORKFLOW.read_text()

    assert "VERSION_NAME: ${{ needs.prepare.outputs.version_name }}" in source
    assert "Sync binary version" in source
    assert 'sync_release_version.py --version "$VERSION_NAME"' in source
    assert 'sync_release_version.py --tag "$TAG_NAME"' in source
    assert "Binary version mismatch: expected %s but saw %s" in source
    assert "$VERSION_NAME" in source


def test_binary_build_bundles_registry_discovered_tool_modules() -> None:
    source = _RELEASE_WORKFLOW.read_text()

    assert "--collect-submodules tools" in source
    assert "--collect-submodules surfaces.interactive_shell" in source
    assert "--collect-submodules integrations" in source


def test_unix_binary_build_uses_onedir_and_pinned_linux_x64_runner() -> None:
    source = _RELEASE_WORKFLOW.read_text()

    assert "runner: ubuntu-22.04" in source
    assert "target: linux-x64" in source
    assert "pyinstaller_mode: onedir" in source
    assert "--${{ matrix.pyinstaller_mode }}" in source
    assert 'BINARY_PATH="./dist/opensre/${{ matrix.binary_name }}"' in source
