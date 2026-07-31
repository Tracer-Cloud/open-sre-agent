"""``remote-sync setup``: persists non-secret settings; hints ambient credentials."""

from __future__ import annotations

from typing import Any

import pytest

from config.local_settings import LocalSettingsError
from platform.filestorage import setup as setup_mod
from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.errors import RemoteSyncConfigError
from platform.filestorage.messages import format_setup_lines
from platform.filestorage.providers.registry import credential_hint_for_provider
from platform.filestorage.setup import RemoteSyncSetupRequest, save_remote_sync_settings


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Intercept the config.yml write; nothing touches disk in these tests."""
    writes: dict[str, Any] = {}

    def _capture(section: str, values: dict[str, Any]) -> None:
        writes["section"] = section
        writes["values"] = values

    monkeypatch.setattr(setup_mod, "update_section", _capture)
    return writes


def test_save_supplies_exactly_the_six_non_secret_values(captured: dict[str, Any]) -> None:
    config = save_remote_sync_settings(
        RemoteSyncSetupRequest(bucket=" My-Bucket ", provider=" GCS ")
    )

    assert captured["section"] == "remote_sync"
    assert captured["values"] == {
        "enabled": True,
        "provider": "gcs",
        "bucket": "My-Bucket",
        "prefix": "opensre",
        "region": "",
        "profile": "",
    }
    assert config == RemoteSyncConfig(bucket="My-Bucket", provider="gcs")


def test_save_defaults_provider_and_prefix(captured: dict[str, Any]) -> None:
    save_remote_sync_settings(RemoteSyncSetupRequest(bucket="b", provider="  ", prefix=" "))
    assert captured["values"]["provider"] == "aws"
    assert captured["values"]["prefix"] == "opensre"


def test_save_without_bucket_fails_closed() -> None:
    with pytest.raises(RemoteSyncConfigError, match="bucket"):
        save_remote_sync_settings(RemoteSyncSetupRequest(bucket="   "))


def test_save_unknown_provider_lists_known_ones() -> None:
    with pytest.raises(RemoteSyncConfigError, match="unknown remote-sync provider") as caught:
        save_remote_sync_settings(RemoteSyncSetupRequest(bucket="b", provider="gogle"))
    assert "aws" in str(caught.value)
    assert "gcs" in str(caught.value)


def test_save_maps_settings_file_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_section: str, _values: dict[str, Any]) -> None:
        raise LocalSettingsError("config.yml is unreadable")

    monkeypatch.setattr(setup_mod, "update_section", _boom)
    with pytest.raises(RemoteSyncConfigError, match="unreadable"):
        save_remote_sync_settings(RemoteSyncSetupRequest(bucket="b"))


def test_gcs_hint_points_at_application_default_credentials() -> None:
    hint = credential_hint_for_provider("gcs")
    assert "gcloud auth application-default login" in hint


def test_aws_hint_points_at_ambient_profile_or_sso() -> None:
    hint = credential_hint_for_provider("aws")
    assert "sso" in hint.lower() or "profile" in hint.lower()


def test_community_provider_gets_generic_hint() -> None:
    assert credential_hint_for_provider("some-community-backend") == (
        "Credentials come from the provider's usual ambient configuration."
    )


def test_format_setup_lines_shows_written_values_and_next_steps() -> None:
    lines = format_setup_lines(
        RemoteSyncConfig(bucket="b", provider="gcs", prefix="opensre"), "HINT-LINE"
    )
    assert any("provider   gcs" in line for line in lines)
    assert any("bucket     b" in line for line in lines)
    assert lines[-2] == "HINT-LINE"
    assert "status" in lines[-1]
    assert "sync" in lines[-1]
    # Optional fields stay out when unset.
    assert not any("region" in line or "profile" in line for line in lines)


def test_format_setup_lines_shows_region_and_profile_when_set() -> None:
    lines = format_setup_lines(
        RemoteSyncConfig(bucket="b", provider="aws", region="eu-west-1", profile="work"),
        "HINT-LINE",
    )
    assert any("region     eu-west-1" in line for line in lines)
    assert any("profile    work" in line for line in lines)


def test_format_setup_lines_reports_off_when_disabled() -> None:
    lines = format_setup_lines(
        RemoteSyncConfig(bucket="b", provider="gcs"), "HINT-LINE", enabled=False
    )
    assert lines[0].startswith("Remote sync is off.")
    assert "Remote sync is on." not in lines[0]
    assert "OPENSRE_REMOTE_SYNC=1" in lines[-1]
    assert "mirror now" not in lines[-1]
