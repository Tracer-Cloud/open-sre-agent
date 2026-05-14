"""Tests for app.integrations.messaging_security — identity model, pairing, and authorization."""

from __future__ import annotations

import time

from app.integrations.messaging_security import (
    _MAX_PAIRING_ATTEMPTS,
    _PAIRING_CODE_TTL_SECONDS,
    AuthorizationResult,
    MessagingIdentityPolicy,
    MessagingPlatform,
    RejectionBehavior,
    authorize_inbound_message,
    complete_pairing,
    generate_pairing_code,
    hash_pairing_code,
    verify_pairing_code,
)

# ---------------------------------------------------------------------------
# MessagingIdentityPolicy model tests
# ---------------------------------------------------------------------------


class TestMessagingIdentityPolicy:
    def test_default_policy_has_empty_allowlists(self) -> None:
        policy = MessagingIdentityPolicy()
        assert policy.allowed_user_ids == []
        assert policy.allowed_chat_ids == []
        assert policy.require_dm_pairing is True
        assert policy.pairing_secret_hash is None
        assert policy.pairing_created_at is None
        assert policy.pairing_attempts == 0
        assert policy.rejection_behavior == RejectionBehavior.REPLY
        assert policy.inbound_enabled is False

    def test_policy_with_allowed_users(self) -> None:
        policy = MessagingIdentityPolicy(
            allowed_user_ids=["123", "456"],
            inbound_enabled=True,
        )
        assert policy.allowed_user_ids == ["123", "456"]
        assert policy.inbound_enabled is True

    def test_policy_serialization_roundtrip(self) -> None:
        policy = MessagingIdentityPolicy(
            allowed_user_ids=["user1"],
            allowed_chat_ids=["chat1"],
            require_dm_pairing=False,
            rejection_behavior=RejectionBehavior.DROP,
            inbound_enabled=True,
        )
        data = policy.model_dump(mode="json")
        restored = MessagingIdentityPolicy.model_validate(data)
        assert restored.allowed_user_ids == ["user1"]
        assert restored.allowed_chat_ids == ["chat1"]
        assert restored.require_dm_pairing is False
        assert restored.rejection_behavior == RejectionBehavior.DROP
        assert restored.inbound_enabled is True

    def test_policy_back_compat_empty_dict_loads(self) -> None:
        """Existing configs with no identity_policy should load with defaults."""
        policy = MessagingIdentityPolicy.model_validate({})
        assert policy.allowed_user_ids == []
        assert policy.inbound_enabled is False


# ---------------------------------------------------------------------------
# Pairing code tests
# ---------------------------------------------------------------------------


class TestPairingCode:
    def test_generate_pairing_code_length(self) -> None:
        code = generate_pairing_code()
        assert len(code) == 6

    def test_generate_pairing_code_is_alphanumeric_uppercase(self) -> None:
        code = generate_pairing_code()
        assert code.isalnum()
        assert code == code.upper()

    def test_generate_pairing_code_is_random(self) -> None:
        codes = {generate_pairing_code() for _ in range(100)}
        # With 36^6 possible codes, 100 should all be unique
        assert len(codes) == 100

    def test_hash_pairing_code_deterministic(self) -> None:
        code = "ABC123"
        h1 = hash_pairing_code(code)
        h2 = hash_pairing_code(code)
        assert h1 == h2

    def test_hash_pairing_code_case_insensitive(self) -> None:
        assert hash_pairing_code("ABC123") == hash_pairing_code("abc123")

    def test_hash_pairing_code_is_hex_string(self) -> None:
        h = hash_pairing_code("TEST01")
        assert len(h) == 64  # SHA-256 hex
        int(h, 16)  # Should not raise

    def test_verify_pairing_code_correct(self) -> None:
        code = "XYZ789"
        stored = hash_pairing_code(code)
        assert verify_pairing_code(code, stored) is True

    def test_verify_pairing_code_wrong(self) -> None:
        stored = hash_pairing_code("CORRECT")
        assert verify_pairing_code("WRONG1", stored) is False

    def test_verify_pairing_code_case_insensitive(self) -> None:
        code = "ABC123"
        stored = hash_pairing_code(code)
        assert verify_pairing_code("abc123", stored) is True


# ---------------------------------------------------------------------------
# Authorization tests
# ---------------------------------------------------------------------------


class TestAuthorizeInboundMessage:
    def test_inbound_disabled_rejects(self) -> None:
        policy = MessagingIdentityPolicy(inbound_enabled=False)
        result = authorize_inbound_message(policy=policy, user_id="123")
        assert not result.allowed
        assert "not enabled" in result.reason

    def test_pairing_attempt_allowed_when_pending(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code("ABC123"),
        )
        result = authorize_inbound_message(
            policy=policy, user_id="unknown", message_text="/pair ABC123"
        )
        assert result.allowed
        assert result.is_pairing_attempt

    def test_pairing_attempt_rejected_when_no_pending(self) -> None:
        """When no pairing is pending, /pair messages should be rejected."""
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=None,
        )
        result = authorize_inbound_message(
            policy=policy, user_id="unknown", message_text="/pair ABC123"
        )
        assert not result.allowed
        assert "no pairing" in result.reason.lower()

    def test_pairing_attempt_case_insensitive(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code("ABC123"),
        )
        result = authorize_inbound_message(
            policy=policy, user_id="unknown", message_text="/Pair abc123"
        )
        assert result.allowed
        assert result.is_pairing_attempt

    def test_allowed_user_passes(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            allowed_user_ids=["user1", "user2"],
        )
        result = authorize_inbound_message(policy=policy, user_id="user1")
        assert result.allowed

    def test_disallowed_user_rejected(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            allowed_user_ids=["user1"],
        )
        result = authorize_inbound_message(policy=policy, user_id="intruder")
        assert not result.allowed
        assert "not in the allowed" in result.reason

    def test_empty_allowlist_with_pairing_required_rejects(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            require_dm_pairing=True,
            allowed_user_ids=[],
        )
        result = authorize_inbound_message(policy=policy, user_id="anyone")
        assert not result.allowed
        assert "No users have been paired" in result.reason

    def test_empty_allowlist_without_pairing_allows(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            require_dm_pairing=False,
            allowed_user_ids=[],
        )
        result = authorize_inbound_message(policy=policy, user_id="anyone")
        assert result.allowed

    def test_chat_id_restriction(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            allowed_user_ids=["user1"],
            allowed_chat_ids=["chat1"],
        )
        # Allowed chat
        result = authorize_inbound_message(policy=policy, user_id="user1", chat_id="chat1")
        assert result.allowed

        # Disallowed chat
        result = authorize_inbound_message(policy=policy, user_id="user1", chat_id="other_chat")
        assert not result.allowed
        assert "not in the allowed chat" in result.reason

    def test_chat_id_none_blocked_when_allowlist_configured(self) -> None:
        """When allowed_chat_ids is set, None chat_id should be blocked."""
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            allowed_user_ids=["user1"],
            allowed_chat_ids=["chat1"],
        )
        result = authorize_inbound_message(policy=policy, user_id="user1", chat_id=None)
        assert not result.allowed
        assert "not in the allowed chat" in result.reason

    def test_pairing_blocked_from_restricted_chat(self) -> None:
        """Pairing attempts from outside allowed_chat_ids are blocked."""
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            allowed_chat_ids=["chat1"],
            pairing_secret_hash=hash_pairing_code("CODE01"),
        )
        result = authorize_inbound_message(
            policy=policy, user_id="anyone", chat_id="other_chat", message_text="/pair CODE01"
        )
        assert not result.allowed
        assert "not in the allowed chat" in result.reason

    def test_authorization_result_bool(self) -> None:
        allowed = AuthorizationResult(allowed=True, reason="ok")
        denied = AuthorizationResult(allowed=False, reason="no")
        assert bool(allowed) is True
        assert bool(denied) is False


# ---------------------------------------------------------------------------
# Complete pairing tests
# ---------------------------------------------------------------------------


class TestCompletePairing:
    def test_successful_pairing(self) -> None:
        code = "TEST01"
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code(code),
            pairing_created_at=time.time(),
        )
        success, message = complete_pairing(policy=policy, user_id="new_user", code=code)
        assert success is True
        assert "successful" in message.lower()
        assert "new_user" in policy.allowed_user_ids
        assert policy.pairing_secret_hash is None
        assert policy.pairing_attempts == 0

    def test_pairing_wrong_code_increments_attempts(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code("CORRECT"),
            pairing_created_at=time.time(),
        )
        success, message = complete_pairing(policy=policy, user_id="user1", code="WRONG1")
        assert success is False
        assert "invalid" in message.lower()
        assert "user1" not in policy.allowed_user_ids
        # Hash should NOT be cleared on single failure
        assert policy.pairing_secret_hash is not None
        assert policy.pairing_attempts == 1

    def test_pairing_no_pending(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=None,
        )
        success, message = complete_pairing(policy=policy, user_id="user1", code="ANY123")
        assert success is False
        assert "no pairing" in message.lower()

    def test_pairing_does_not_duplicate_user(self) -> None:
        code = "DUP001"
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            allowed_user_ids=["existing_user"],
            pairing_secret_hash=hash_pairing_code(code),
            pairing_created_at=time.time(),
        )
        success, _ = complete_pairing(policy=policy, user_id="existing_user", code=code)
        assert success is True
        assert policy.allowed_user_ids.count("existing_user") == 1

    def test_brute_force_invalidates_code(self) -> None:
        """After MAX_PAIRING_ATTEMPTS failures, the code is invalidated."""
        code = "SECRET"
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code(code),
            pairing_created_at=time.time(),
        )
        # Exhaust all attempts
        for i in range(_MAX_PAIRING_ATTEMPTS):
            success, _ = complete_pairing(policy=policy, user_id="attacker", code=f"WRONG{i}")
            assert success is False

        # Code should now be invalidated
        assert policy.pairing_secret_hash is None
        assert policy.pairing_attempts == 0

    def test_expired_code_rejected(self) -> None:
        """A pairing code that has exceeded its TTL is rejected."""
        code = "EXPIRE"
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code(code),
            pairing_created_at=time.time() - _PAIRING_CODE_TTL_SECONDS - 1,
        )
        success, message = complete_pairing(policy=policy, user_id="user1", code=code)
        assert success is False
        assert "expired" in message.lower()
        assert policy.pairing_secret_hash is None

    def test_missing_pairing_created_at_treated_as_expired(self) -> None:
        """A hash with no timestamp (legacy or corrupted) is treated as expired."""
        code = "LEGACY"
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code(code),
            pairing_created_at=None,
        )
        success, message = complete_pairing(policy=policy, user_id="user1", code=code)
        assert success is False
        assert "expired" in message.lower()
        assert policy.pairing_secret_hash is None


# ---------------------------------------------------------------------------
# Platform enum tests
# ---------------------------------------------------------------------------


class TestMessagingPlatform:
    def test_platform_values(self) -> None:
        assert MessagingPlatform.TELEGRAM.value == "telegram"
        assert MessagingPlatform.SLACK.value == "slack"
        assert MessagingPlatform.DISCORD.value == "discord"
