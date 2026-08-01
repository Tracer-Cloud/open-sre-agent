"""Persisted remote-sync setup (config.yml section)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from config.constants import paths as paths_mod
from config.constants.filestorage import (
    BLOB_READ_WRITE_TOKEN_ENV,
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PREFIX_ENV,
    REMOTE_SYNC_PROFILE_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
    REMOTE_SYNC_REGION_ENV,
)
from platform.filestorage.config import RemoteSyncConfig, load_remote_sync_config
from platform.filestorage.errors import RemoteSyncConfigError
from platform.filestorage.messages import format_setup_lines
from platform.filestorage.providers import credential_hint_for_provider
from platform.filestorage.setup import RemoteSyncSetupRequest, save_remote_sync_settings

# Every name the loader consults. Environment beats stored config, so any one of
# these left set reaches the assertions instead of the fixture's value.
_REMOTE_SYNC_ENV_NAMES = (
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_PREFIX_ENV,
    REMOTE_SYNC_REGION_ENV,
    REMOTE_SYNC_PROFILE_ENV,
    BLOB_READ_WRITE_TOKEN_ENV,
)


@pytest.fixture(autouse=True)
def _clear_remote_sync_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these tests off the developer's own remote-sync settings.

    ``bootstrap_opensre_env_once`` loads the repo ``.env``, so a machine that
    has ever run remote-sync setup exports a real bucket, prefix, region and
    profile into every test process. Clearing only the on/off switch left the
    rest of them winning over the values under test.
    """
    for name in _REMOTE_SYNC_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_save_writes_remote_sync_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)

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
