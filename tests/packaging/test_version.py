from __future__ import annotations

import importlib.metadata
import re

from config import version as version_module
from config.runtime_metadata import build_info


def _raise_package_not_found(_: str) -> str:
    raise importlib.metadata.PackageNotFoundError("opensre")


def _no_git(monkeypatch) -> None:
    """Force the git-metadata lookup to find nothing (stripped checkout)."""
    monkeypatch.setattr(build_info, "find_git_layout", lambda: None)


def test_release_version_string_is_returned_verbatim(monkeypatch) -> None:
    # A full release string (with a ``+`` local segment) is used as-is.
    monkeypatch.setattr(
        version_module.importlib.metadata,
        "version",
        lambda _name: "0.1.2026.8.27+main.85fd865",
    )
    assert version_module.get_opensre_version() == "0.1.2026.8.27+main.85fd865"


def test_pyproject_base_is_returned_when_git_is_unavailable(monkeypatch, tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    version_file = config_dir / "version.py"
    version_file.touch()
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")

    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "__file__", str(version_file))
    _no_git(monkeypatch)

    assert version_module.get_opensre_version() == "9.9.9"


def test_dev_default_when_metadata_pyproject_and_git_all_missing(monkeypatch, tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    version_file = config_dir / "version.py"
    version_file.touch()

    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "__file__", str(version_file))
    _no_git(monkeypatch)

    assert version_module.get_opensre_version() == "0.1"


def test_dev_checkout_expands_base_to_a_specific_build_from_git(monkeypatch) -> None:
    # A dev checkout has only the base version, so it expands to the release shape
    # ``0.1.Y.M.D+<branch>.<sha>`` using git head + branch.
    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "_pyproject_version", lambda: "0.1")
    monkeypatch.setattr(build_info, "find_git_layout", lambda: object())
    monkeypatch.setattr(build_info, "read_git_head_sha", lambda _layout: "85fd865")
    monkeypatch.setattr(build_info, "read_git_head_branch", lambda _layout: "main")

    result = version_module.get_opensre_version()

    assert re.fullmatch(r"0\.1\.\d{4}\.\d{1,2}\.\d{1,2}\+main\.85fd865", result), result


def test_dev_branch_name_is_sanitised_into_a_valid_local_segment(monkeypatch) -> None:
    # Slashes in a branch name become dots so the local segment stays valid.
    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "_pyproject_version", lambda: "0.1")
    monkeypatch.setattr(build_info, "find_git_layout", lambda: object())
    monkeypatch.setattr(build_info, "read_git_head_sha", lambda _layout: "abc1234")
    monkeypatch.setattr(build_info, "read_git_head_branch", lambda _layout: "feat/sign-in-screen")

    result = version_module.get_opensre_version()

    assert result.endswith("+feat.sign.in.screen.abc1234"), result


def test_detached_head_uses_the_sha_alone_without_a_branch(monkeypatch) -> None:
    # No branch to invent on a detached HEAD — the local segment is just the sha.
    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "_pyproject_version", lambda: "0.1")
    monkeypatch.setattr(build_info, "find_git_layout", lambda: object())
    monkeypatch.setattr(build_info, "read_git_head_sha", lambda _layout: "abc1234")
    monkeypatch.setattr(build_info, "read_git_head_branch", lambda _layout: None)

    result = version_module.get_opensre_version()

    assert re.fullmatch(r"0\.1\.\d{4}\.\d{1,2}\.\d{1,2}\+abc1234", result), result
