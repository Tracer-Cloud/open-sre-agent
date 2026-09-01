from __future__ import annotations

import datetime as _dt
import importlib.metadata

from config import version as version_module
from config.runtime_metadata import build_info


def _raise_package_not_found(_: str) -> str:
    raise importlib.metadata.PackageNotFoundError("opensre")


def _no_git(monkeypatch) -> None:
    """Force the git-metadata lookup to find nothing (stripped checkout)."""
    monkeypatch.setattr(build_info, "find_git_layout", lambda: None)


def _git_head_at(monkeypatch, sha: str) -> None:
    """Pin git HEAD to *sha* committed on a fixed date, so the build is stable."""
    monkeypatch.setattr(build_info, "find_git_layout", lambda: object())
    monkeypatch.setattr(build_info, "read_git_head_sha", lambda _layout: sha)
    monkeypatch.setattr(
        build_info,
        "read_git_head_commit_date",
        lambda _layout: _dt.datetime(2026, 8, 27, tzinfo=_dt.UTC),
    )


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


def test_dev_checkout_expands_to_the_dated_main_build_shape(monkeypatch) -> None:
    # A dev checkout expands the base to the release shape ``0.1.Y.M.D+main.<sha>``.
    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "_pyproject_version", lambda: "0.1")
    _git_head_at(monkeypatch, "85fd865")

    assert version_module.get_opensre_version() == "0.1.2026.8.27+main.85fd865"


def test_dev_build_version_is_deterministic_for_a_fixed_commit(monkeypatch) -> None:
    # The date is the commit's, not wall-clock, so an unchanged commit yields
    # the same string on any day — no telemetry/release-id churn.
    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "_pyproject_version", lambda: "0.1")
    _git_head_at(monkeypatch, "85fd865")

    first = version_module.get_opensre_version()
    second = version_module.get_opensre_version()

    assert first == second == "0.1.2026.8.27+main.85fd865"


def test_dev_build_falls_back_to_sha_only_when_commit_date_is_unreadable(monkeypatch) -> None:
    # A packed / unreadable commit object drops the date but keeps the sha, so
    # the identity is still reproducible (never a wall-clock-derived date).
    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "_pyproject_version", lambda: "0.1")
    monkeypatch.setattr(build_info, "find_git_layout", lambda: object())
    monkeypatch.setattr(build_info, "read_git_head_sha", lambda _layout: "85fd865")
    monkeypatch.setattr(build_info, "read_git_head_commit_date", lambda _layout: None)

    assert version_module.get_opensre_version() == "0.1+main.85fd865"
