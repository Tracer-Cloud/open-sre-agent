from __future__ import annotations

import importlib.metadata

from config import version as version_module


def _raise_package_not_found(_: str) -> str:
    raise importlib.metadata.PackageNotFoundError("opensre")


def test_get_opensre_version_falls_back_to_pyproject_when_package_metadata_is_missing(
    monkeypatch,
    tmp_path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    version_file = config_dir / "version.py"
    version_file.touch()
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")

    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "__file__", str(version_file))

    assert version_module.get_opensre_version() == "9.9.9"


def test_get_opensre_version_falls_back_to_dev_default_when_metadata_and_pyproject_missing(
    monkeypatch,
    tmp_path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    version_file = config_dir / "version.py"
    version_file.touch()

    monkeypatch.setattr(version_module.importlib.metadata, "version", _raise_package_not_found)
    monkeypatch.setattr(version_module, "__file__", str(version_file))

    assert version_module.get_opensre_version() == "0.1"
