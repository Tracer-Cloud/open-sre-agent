"""Tests for the `opensre messaging` CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli.commands.messaging import messaging
from app.integrations.messaging_security import verify_pairing_code


@pytest.fixture()
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the integration store to a temp directory."""
    store_path = tmp_path / "integrations.json"
    store_path.write_text(json.dumps({"version": 2, "integrations": []}))
    monkeypatch.setattr("app.integrations.store.STORE_PATH", store_path)
    return store_path


class TestMessagingPairCommand:
    def test_pair_generates_code(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["pair", "--platform", "telegram"])
        assert result.exit_code == 0
        assert "Pairing code generated" in result.output

    def test_pair_stores_hash_in_integration(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["pair", "--platform", "telegram"])
        assert result.exit_code == 0

        # Read back the store and verify the hash was persisted
        data = json.loads(_isolated_store.read_text())
        integrations = data["integrations"]
        assert len(integrations) == 1
        record = integrations[0]
        assert record["service"] == "telegram"
        creds = record["instances"][0]["credentials"]
        policy_data = creds["identity_policy"]
        assert policy_data["pairing_secret_hash"] is not None
        assert policy_data["inbound_enabled"] is True
        assert policy_data["require_dm_pairing"] is True

    def test_pair_code_is_verifiable(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["pair", "--platform", "discord"])
        assert result.exit_code == 0

        # Extract the code from output (it's between bold markers)
        lines = result.output.strip().split("\n")
        code_line = [line for line in lines if len(line.strip()) == 6 and line.strip().isalnum()]
        assert len(code_line) == 1
        code = code_line[0].strip()

        # Verify the stored hash matches
        data = json.loads(_isolated_store.read_text())
        creds = data["integrations"][0]["instances"][0]["credentials"]
        stored_hash = creds["identity_policy"]["pairing_secret_hash"]
        assert verify_pairing_code(code, stored_hash)

    def test_pair_requires_platform(self) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["pair"])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()


class TestMessagingAllowCommand:
    def test_allow_adds_user(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["allow", "--platform", "telegram", "--user-id", "12345"])
        assert result.exit_code == 0
        assert "Added user 12345" in result.output

        data = json.loads(_isolated_store.read_text())
        creds = data["integrations"][0]["instances"][0]["credentials"]
        assert "12345" in creds["identity_policy"]["allowed_user_ids"]

    def test_allow_duplicate_user_warns(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        runner.invoke(messaging, ["allow", "--platform", "slack", "--user-id", "U001"])
        result = runner.invoke(messaging, ["allow", "--platform", "slack", "--user-id", "U001"])
        assert result.exit_code == 0
        assert "already in the allowed list" in result.output


class TestMessagingRevokeCommand:
    def test_revoke_removes_user(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        # First add a user
        runner.invoke(messaging, ["allow", "--platform", "telegram", "--user-id", "99"])
        # Then revoke
        result = runner.invoke(messaging, ["revoke", "--platform", "telegram", "--user-id", "99"])
        assert result.exit_code == 0
        assert "Removed user 99" in result.output

        data = json.loads(_isolated_store.read_text())
        creds = data["integrations"][0]["instances"][0]["credentials"]
        assert "99" not in creds["identity_policy"]["allowed_user_ids"]

    def test_revoke_nonexistent_user_warns(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["revoke", "--platform", "discord", "--user-id", "ghost"])
        assert result.exit_code == 0
        assert "not in the allowed list" in result.output


class TestMessagingStatusCommand:
    def test_status_no_integration(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(messaging, ["status", "--platform", "telegram"])
        assert result.exit_code == 0
        assert "No telegram integration configured" in result.output

    def test_status_with_configured_integration(self, _isolated_store: Path) -> None:
        runner = CliRunner()
        # Set up a user first
        runner.invoke(messaging, ["allow", "--platform", "telegram", "--user-id", "user1"])
        result = runner.invoke(messaging, ["status", "--platform", "telegram"])
        assert result.exit_code == 0
        assert "Inbound enabled" in result.output
        assert "user1" in result.output
