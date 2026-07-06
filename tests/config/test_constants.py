from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import get_store_path


def test_get_store_path_honors_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "custom-dir" / "opensre.json"
    monkeypatch.setenv("OPENSRE_WIZARD_STORE_PATH", str(override))

    assert get_store_path() == override


def test_get_store_path_defaults_away_from_real_home_during_tests(tmp_path: Path) -> None:
    """Regression guard for #3721.

    The root ``tests/conftest.py`` autouse fixture ``_isolate_opensre_home_files``
    sets ``OPENSRE_WIZARD_STORE_PATH`` for every test by default, specifically so
    a test that forgets to monkeypatch ``get_store_path`` can never fall through
    to the developer's real ``~/.opensre/opensre.json``. This test intentionally
    does *not* patch anything itself, to prove that default is in effect.
    """
    resolved = get_store_path()

    assert resolved != Path.home() / ".opensre" / "opensre.json"
    # Same tmp_path instance the autouse fixture pointed OPENSRE_WIZARD_STORE_PATH at.
    assert resolved == tmp_path / "opensre.json"
