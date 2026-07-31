"""Persisted remote-sync setup (config.yml section)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from config.constants import paths as paths_mod
from config.constants.filestorage import BLOB_READ_WRITE_TOKEN_ENV, REMOTE_SYNC_ENV
from platform.filestorage.config import RemoteSyncConfig, load_remote_sync_config
from platform.filestorage.errors import RemoteSyncConfigError
from platform.filestorage.messages import format_setup_lines
from platform.filestorage.setup import (
    RemoteSyncSetupRequest,
    credential_hint_for_provider,
    save_remote_sync_settings,
)


def test_save_writes_remote_sync_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)

    config = save_remote_sync_settings(
        RemoteSyncSetupRequest(
            bucket="opensre-remote-sync",
            provider="vercel",
            prefix="opensre",
        )
    )
    assert config.provider == "vercel"
    assert config.bucket == "opensre-remote-sync"

    on_disk = yaml.safe_load((tmp_path / "config.yml").read_text(encoding="utf-8"))
    assert on_disk["remote_sync"]["enabled"] is True
    assert on_disk["remote_sync"]["provider"] == "vercel"
    assert on_disk["remote_sync"]["bucket"] == "opensre-remote-sync"

    loaded = load_remote_sync_config()
    assert loaded is not None
    assert loaded.provider == "vercel"
    assert loaded.bucket == "opensre-remote-sync"


def test_save_requires_bucket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    with pytest.raises(RemoteSyncConfigError, match="bucket"):
        save_remote_sync_settings(RemoteSyncSetupRequest(bucket="  "))


def test_vercel_credential_hint_names_token_env() -> None:
    assert BLOB_READ_WRITE_TOKEN_ENV in credential_hint_for_provider("vercel")
    lines = format_setup_lines(RemoteSyncConfig(bucket="b", provider="vercel", prefix="p"))
    assert any(BLOB_READ_WRITE_TOKEN_ENV in line for line in lines)
