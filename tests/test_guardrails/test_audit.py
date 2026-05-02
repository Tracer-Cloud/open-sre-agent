from __future__ import annotations

import json
import re
import stat
import sys
from pathlib import Path

import pytest

from app.guardrails.audit import AuditLogger, _fingerprint

# Hex-only sanity (12 chars of SHA-256, post-fix).
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{12}$")


class TestAuditLogger:
    def test_creates_file_on_first_log(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log(rule_name="test", action="redact", matched_text="secret")
        assert log_path.exists()

    def test_appends_jsonl_entries(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log(rule_name="r1", action="redact", matched_text="a")
        logger.log(rule_name="r2", action="block", matched_text="b")

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["rule_name"] == "r1"
        assert json.loads(lines[1])["rule_name"] == "r2"

    def test_entry_has_expected_fields(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log(rule_name="cc", action="redact", matched_text="4111", context="llm_invoke")

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["rule_name"] == "cc"
        assert entry["action"] == "redact"
        assert entry["context"] == "llm_invoke"
        assert "timestamp" in entry
        # Post-fix: literal text replaced by fingerprint + length.
        assert _FINGERPRINT_RE.match(entry["match_fingerprint"])
        assert entry["match_length"] == 4
        assert "matched_text_preview" not in entry
        assert "matched_text" not in entry

    def test_fingerprint_length_independent_of_input_size(self, tmp_path: Path) -> None:
        """Fingerprint is always 12 hex chars regardless of input size — the
        old ``[:40]`` truncation hack is replaced by a fixed-length hash."""
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        long_text = "x" * 1000
        logger.log(rule_name="test", action="audit", matched_text=long_text)

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert len(entry["match_fingerprint"]) == 12
        assert _FINGERPRINT_RE.match(entry["match_fingerprint"])
        assert entry["match_length"] == 1000

    def test_read_entries_returns_most_recent(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        for i in range(10):
            logger.log(rule_name=f"r{i}", action="audit", matched_text=f"m{i}")

        entries = logger.read_entries(limit=3)
        assert len(entries) == 3
        assert entries[0]["rule_name"] == "r7"
        assert entries[2]["rule_name"] == "r9"

    def test_read_entries_empty_file(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        assert logger.read_entries() == []

    def test_read_entries_nonexistent_file(self, tmp_path: Path) -> None:
        logger = AuditLogger(path=tmp_path / "missing.jsonl")
        assert logger.read_entries() == []

    def test_handles_write_failure_gracefully(self, tmp_path: Path) -> None:
        log_path = tmp_path / "readonly" / "audit.jsonl"
        # Make parent read-only
        (tmp_path / "readonly").mkdir()
        (tmp_path / "readonly").chmod(0o444)
        logger = AuditLogger(path=log_path)
        # Should not raise
        logger.log(rule_name="test", action="redact", matched_text="x")
        # Restore permissions for cleanup
        (tmp_path / "readonly").chmod(0o755)

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        log_path = tmp_path / "deep" / "nested" / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log(rule_name="test", action="audit", matched_text="data")
        assert log_path.exists()


# ---------------------------------------------------------------------------
# Security regressions for issue #1197 — the audit log MUST NOT persist the
# matched text in cleartext, MUST be readable only by the owning user, and
# MUST produce stable fingerprints suitable for deduplication.
# ---------------------------------------------------------------------------


_KNOWN_SAMPLE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"  # AWS public sample key.
_KNOWN_SAMPLE_GH_PAT = "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class TestNoSecretLeakageInAudit:
    @pytest.mark.parametrize(
        "secret",
        [
            _KNOWN_SAMPLE_AWS_KEY,
            _KNOWN_SAMPLE_GH_PAT,
            "4111-1111-1111-1111",  # Test credit-card number
            "super_secret_internal_token_value_with_dashes",
            # 40-char hex API key (the legacy ``[:40]`` truncation window).
            "abcdef0123456789abcdef0123456789abcdef01",
        ],
    )
    def test_secret_never_appears_in_cleartext(self, secret: str, tmp_path: Path) -> None:
        """The exact secret bytes the rule matched must not appear anywhere
        in the on-disk JSONL — neither in a single field nor smeared across
        fields by accident."""
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log(rule_name="test_rule", action="redact", matched_text=secret)

        contents = log_path.read_text(encoding="utf-8")
        assert secret not in contents, f"secret {secret!r} leaked into audit log:\n{contents}"
        # Also assert no >=8-char prefix of the secret appears — guards
        # against accidental partial leaks if the implementation ever does
        # something clever like "first N chars + fingerprint".
        prefix = secret[:8]
        assert prefix not in contents, (
            f"secret prefix {prefix!r} leaked into audit log:\n{contents}"
        )


class TestFingerprintProperties:
    def test_fingerprint_is_deterministic(self) -> None:
        """Same input → same fingerprint, every time. This is what makes the
        audit log useful for deduplication: SREs see N entries with the same
        fingerprint and know they came from the same secret."""
        assert _fingerprint("AKIA...") == _fingerprint("AKIA...")

    def test_distinct_inputs_produce_distinct_fingerprints(self) -> None:
        """SHA-256 collision-resistance applies to the 12-hex prefix in
        practice — for the kinds of strings guardrails fire on, no two
        distinct values will ever match. We test a representative
        cross-section."""
        seen: set[str] = set()
        samples = [
            _KNOWN_SAMPLE_AWS_KEY,
            _KNOWN_SAMPLE_GH_PAT,
            "AKIA" + "X" * 16,
            "ASIA" + "X" * 16,
            "secret_token",
            "super_secret_token_value",
            "",
            "x",
            "X" * 1000,
        ]
        for s in samples:
            fp = _fingerprint(s)
            assert fp not in seen, f"unexpected collision on {s!r} → {fp}"
            seen.add(fp)

    def test_fingerprint_format_is_lowercase_hex(self) -> None:
        """Pin the format — anything else would break log-aggregation
        tooling and complicate the deduplication invariant."""
        assert _FINGERPRINT_RE.match(_fingerprint("anything"))


class TestAuditLogPermissions:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits N/A on Windows")
    def test_log_file_is_owner_only(self, tmp_path: Path) -> None:
        """The audit log must be ``0o600`` so other local users cannot
        read its contents (rule names + timestamps are themselves a leak
        about what the user was handling, even if the matched bytes are
        fingerprinted)."""
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log(rule_name="r", action="audit", matched_text="x")

        mode = stat.S_IMODE(log_path.stat().st_mode)
        assert mode == 0o600, f"audit log perms = {oct(mode)}, expected 0o600"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits N/A on Windows")
    def test_log_dir_is_owner_only_after_log(self, tmp_path: Path) -> None:
        """The parent dir must be ``0o700`` for the same reason — even
        the existence of an audit log is information-disclosure."""
        log_dir = tmp_path / "opensre"
        log_path = log_dir / "audit.jsonl"
        logger = AuditLogger(path=log_path)
        logger.log(rule_name="r", action="audit", matched_text="x")

        mode = stat.S_IMODE(log_dir.stat().st_mode)
        assert mode == 0o700, f"audit dir perms = {oct(mode)}, expected 0o700"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits N/A on Windows")
    def test_log_dir_perms_self_heal_on_existing_loose_dir(self, tmp_path: Path) -> None:
        """If the parent dir already exists with too-loose perms (e.g. from
        a pre-fix install), the next log call must tighten it. Otherwise
        the security fix would only kick in for users who delete their
        existing ``~/.opensre`` directory."""
        log_dir = tmp_path / "opensre"
        log_dir.mkdir()
        log_dir.chmod(0o755)  # World-readable, simulating pre-fix state.
        log_path = log_dir / "audit.jsonl"

        AuditLogger(path=log_path).log(rule_name="r", action="audit", matched_text="x")

        mode = stat.S_IMODE(log_dir.stat().st_mode)
        assert mode == 0o700, f"audit dir perms = {oct(mode)}, expected 0o700 (self-heal failed)"
