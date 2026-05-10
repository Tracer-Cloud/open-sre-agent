from __future__ import annotations

import importlib.metadata

from app import version as version_module


def _raise_package_not_found(_: str) -> str:
    raise importlib.metadata.PackageNotFoundError(version_module.PACKAGE_NAME)


def test_get_version_falls_back_to_pyproject_when_package_metadata_is_missing(
    monkeypatch,
    tmp_path,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")

    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "_PYPROJECT_PATH", pyproject)

    assert version_module.get_version() == "9.9.9"


def test_get_version_falls_back_to_default_when_package_metadata_and_pyproject_are_missing(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "_PYPROJECT_PATH", tmp_path / "missing.toml")

    assert version_module.get_version() == version_module.DEFAULT_VERSION
