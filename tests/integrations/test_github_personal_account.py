from __future__ import annotations

import stat
from pathlib import Path

from integrations import store
from integrations.github import configure_personal_github, disconnect_personal_github


def test_personal_github_token_stays_in_owner_only_integration_store(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "integrations.json"
    monkeypatch.setattr(store, "STORE_PATH", path)

    configure_personal_github(access_token="gho_secret", username="octocat")

    integration = store.get_integration("github")
    assert integration is not None
    assert integration["credentials"]["auth_token"] == "gho_secret"
    assert integration["instances"][0]["tags"] == {"auth_source": "opensre_account"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert disconnect_personal_github() is True
    assert store.get_integration("github") is None


def test_personal_logout_preserves_a_later_manual_github_configuration(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(store, "STORE_PATH", tmp_path / "integrations.json")
    store.upsert_integration("github", {"credentials": {"auth_token": "manually_replaced"}})

    assert disconnect_personal_github() is False
    assert store.get_integration("github") is not None
