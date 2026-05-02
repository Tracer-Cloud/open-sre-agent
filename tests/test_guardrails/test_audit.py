from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.guardrails.audit import AuditLogger

# Hex-only sanity (12 chars of HMAC-SHA-256, post-fix).
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{12}$")


def _make_logger(tmp_path: Path) -> AuditLogger:
    """Build an ``AuditLogger`` whose audit file *and* HMAC key live under
    ``tmp_path``, so tests are fully isolated from any real
    ``~/.opensre/.audit_key``."""
    return AuditLogger(
        path=tmp_path / "audit.jsonl",
        key_path=tmp_path / ".audit_key",
    )


class TestAuditLogger:
    def test_creates_file_on_first_log(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        _make_logger(tmp_path).log(rule_name="test", action="redact", matched_text="secret")
        assert log_path.exists()

    def test_appends_jsonl_entries(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = _make_logger(tmp_path)
        logger.log(rule_name="r1", action="redact", matched_text="a")
        logger.log(rule_name="r2", action="block", matched_text="b")

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["rule_name"] == "r1"
        assert json.loads(lines[1])["rule_name"] == "r2"

    def test_entry_has_expected_fields(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        _make_logger(tmp_path).log(
            rule_name="cc", action="redact", matched_text="4111", context="llm_invoke"
        )

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
        long_text = "x" * 1000
        _make_logger(tmp_path).log(rule_name="test", action="audit", matched_text=long_text)

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert len(entry["match_fingerprint"]) == 12
        assert _FINGERPRINT_RE.match(entry["match_fingerprint"])
        assert entry["match_length"] == 1000

    def test_read_entries_returns_most_recent(self, tmp_path: Path) -> None:
        logger = _make_logger(tmp_path)
        for i in range(10):
            logger.log(rule_name=f"r{i}", action="audit", matched_text=f"m{i}")

        entries = logger.read_entries(limit=3)
        assert len(entries) == 3
        assert entries[0]["rule_name"] == "r7"
        assert entries[2]["rule_name"] == "r9"

    def test_read_entries_empty_file(self, tmp_path: Path) -> None:
        assert _make_logger(tmp_path).read_entries() == []

    def test_read_entries_nonexistent_file(self, tmp_path: Path) -> None:
        logger = AuditLogger(path=tmp_path / "missing.jsonl", key_path=tmp_path / ".audit_key")
        assert logger.read_entries() == []

    def test_handles_write_failure_gracefully_via_mocked_open(self, tmp_path: Path) -> None:
        """When the underlying ``os.open`` fails (out of file descriptors,
        read-only mount, AppArmor denial, etc.), ``log()`` must not raise
        — it must swallow the exception and continue. Mocking
        ``app.guardrails.audit.os.open`` to throw is the only reliable way
        to exercise this on every platform: in earlier revisions of this
        test we used a read-only parent dir, but the new ``chmod``
        self-heal now promotes that dir back to ``0o700`` and the write
        succeeds — so the test was passing for the wrong reason."""
        logger = _make_logger(tmp_path)

        def _raise_oserror(*_args: object, **_kwargs: object) -> int:
            raise OSError("simulated open failure")

        with patch("app.guardrails.audit.os.open", side_effect=_raise_oserror):
            # Must not raise.
            logger.log(rule_name="test", action="redact", matched_text="x")

        # No partial file created (the raise happens at open time).
        assert not (tmp_path / "audit.jsonl").exists()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        log_path = tmp_path / "deep" / "nested" / "audit.jsonl"
        AuditLogger(path=log_path, key_path=tmp_path / "deep" / "nested" / ".audit_key").log(
            rule_name="test", action="audit", matched_text="data"
        )
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
        _make_logger(tmp_path).log(rule_name="test_rule", action="redact", matched_text=secret)

        contents = log_path.read_text(encoding="utf-8")
        assert secret not in contents, f"secret {secret!r} leaked into audit log:\n{contents}"
        # Also assert no >=8-char prefix of the secret appears — guards
        # against accidental partial leaks if the implementation ever does
        # something clever like "first N chars + fingerprint".
        prefix = secret[:8]
        assert prefix not in contents, (
            f"secret prefix {prefix!r} leaked into audit log:\n{contents}"
        )


class TestHMACKeyedFingerprint:
    """The fingerprint is HMAC-keyed by a per-machine secret in
    ``~/.opensre/.audit_key``, so an attacker who exfiltrates only the JSONL
    cannot recompute fingerprints even for a known candidate secret."""

    def test_fingerprint_is_deterministic_within_a_machine(self, tmp_path: Path) -> None:
        """Same key + same input → same fingerprint, every time. This is
        what makes the audit log useful for deduplication: SREs see N
        entries with the same fingerprint and know they came from the
        same secret."""
        a = _make_logger(tmp_path)
        b = _make_logger(tmp_path)  # Same key file → same key.
        text = "AKIAIOSFODNN7EXAMPLE"
        assert a._fingerprint(text) == b._fingerprint(text)

    def test_distinct_inputs_produce_distinct_fingerprints(self, tmp_path: Path) -> None:
        """HMAC-SHA-256 collision-resistance applies to the 12-hex prefix in
        practice — for the kinds of strings guardrails fire on, no two
        distinct values will ever match."""
        logger = _make_logger(tmp_path)
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
            fp = logger._fingerprint(s)
            assert fp not in seen, f"unexpected collision on {s!r} → {fp}"
            seen.add(fp)

    def test_distinct_machine_keys_produce_distinct_fingerprints(self, tmp_path: Path) -> None:
        """The dedup property is per-machine, not global — fingerprints are
        not portable across machines (each machine has its own random
        ``.audit_key``). This is the security property: an attacker
        cannot pre-compute a rainbow table of "what fingerprint does
        ``AKIA…`` produce" because the answer depends on a key they
        don't have."""
        machine_a = AuditLogger(
            path=tmp_path / "a" / "audit.jsonl",
            key_path=tmp_path / "a" / ".audit_key",
        )
        machine_b = AuditLogger(
            path=tmp_path / "b" / "audit.jsonl",
            key_path=tmp_path / "b" / ".audit_key",
        )
        secret = "AKIAIOSFODNN7EXAMPLE"
        assert machine_a._fingerprint(secret) != machine_b._fingerprint(secret)

    def test_unkeyed_sha256_does_NOT_match_keyed_fingerprint(self, tmp_path: Path) -> None:
        """Confirmation-attack regression: an attacker who only has the
        JSONL and *guesses* the right candidate secret cannot recompute
        the fingerprint with bare ``hashlib.sha256`` — the keyed HMAC
        produces a different output, so unkeyed brute-force fails."""
        import hashlib

        logger = _make_logger(tmp_path)
        secret = "AKIAIOSFODNN7EXAMPLE"
        unkeyed = hashlib.sha256(secret.encode()).hexdigest()[:12]
        assert logger._fingerprint(secret) != unkeyed

    def test_key_file_is_owner_only(self, tmp_path: Path) -> None:
        if sys.platform == "win32":
            pytest.skip("POSIX permission bits N/A on Windows")
        key_path = tmp_path / ".audit_key"
        # First log triggers key generation.
        _make_logger(tmp_path).log(rule_name="r", action="audit", matched_text="x")
        assert key_path.exists()
        mode = stat.S_IMODE(key_path.stat().st_mode)
        assert mode == 0o600, f"audit key perms = {oct(mode)}, expected 0o600"

    def test_key_file_is_persistent_across_logger_instances(self, tmp_path: Path) -> None:
        """Two ``AuditLogger`` instances on the same machine must read the
        same key — generating fresh keys per instance would make all
        fingerprints distinct and break the dedup property."""
        key_path = tmp_path / ".audit_key"
        _make_logger(tmp_path).log(rule_name="r1", action="audit", matched_text="x")
        first_key = key_path.read_bytes()
        _make_logger(tmp_path).log(rule_name="r2", action="audit", matched_text="x")
        second_key = key_path.read_bytes()
        assert first_key == second_key

    def test_truncated_key_file_is_regenerated(self, tmp_path: Path) -> None:
        """A partial / corrupted key file must be replaced rather than
        silently used — using only 5 bytes of HMAC key would cripple
        the security guarantee."""
        key_path = tmp_path / ".audit_key"
        # Write a too-short key to simulate corruption.
        key_path.write_bytes(b"abc")
        _make_logger(tmp_path).log(rule_name="r", action="audit", matched_text="x")
        # Should have been regenerated to the full size.
        assert len(key_path.read_bytes()) == 32


class TestAuditLogPermissions:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits N/A on Windows")
    def test_log_file_is_owner_only(self, tmp_path: Path) -> None:
        """The audit log must be ``0o600`` so other local users cannot
        read its contents (rule names + timestamps are themselves a leak
        about what the user was handling, even if the matched bytes are
        fingerprinted)."""
        log_path = tmp_path / "audit.jsonl"
        _make_logger(tmp_path).log(rule_name="r", action="audit", matched_text="x")

        mode = stat.S_IMODE(log_path.stat().st_mode)
        assert mode == 0o600, f"audit log perms = {oct(mode)}, expected 0o600"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits N/A on Windows")
    def test_log_dir_is_owner_only_after_log(self, tmp_path: Path) -> None:
        """The parent dir must be ``0o700`` for the same reason — even
        the existence of an audit log is information-disclosure."""
        log_dir = tmp_path / "opensre"
        log_path = log_dir / "audit.jsonl"
        AuditLogger(path=log_path, key_path=log_dir / ".audit_key").log(
            rule_name="r", action="audit", matched_text="x"
        )

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

        AuditLogger(path=log_path, key_path=log_dir / ".audit_key").log(
            rule_name="r", action="audit", matched_text="x"
        )

        mode = stat.S_IMODE(log_dir.stat().st_mode)
        assert mode == 0o700, f"audit dir perms = {oct(mode)}, expected 0o700 (self-heal failed)"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits N/A on Windows")
    def test_log_file_is_NEVER_world_readable_at_any_point(self, tmp_path: Path) -> None:
        """TOCTOU regression for #1197 Greptile P1: the *previous* fix
        sequence was ``open(\"a\")`` (creates with umask perms — typically
        ``0o644``) followed by ``os.chmod(0o600)``. Between those two
        calls a local ``inotify`` watcher could read the file with
        looser perms. Confirmed-fixed by patching ``os.fdopen`` to
        capture the file mode *immediately* after creation, before any
        further work runs.

        We can't drive a real inotify watcher inside a unit test, but we
        can hook the ``os.fdopen`` call and stat the file at that point —
        that's the earliest moment a watcher could have observed the
        new file. Mode there must already be ``0o600``."""
        log_path = tmp_path / "audit.jsonl"
        observed_mode: list[int] = []
        real_fdopen = os.fdopen

        def _stat_at_fdopen_time(fd: int, *args: object, **kwargs: object) -> object:
            # Stat the path the test instructed the logger to create.
            observed_mode.append(stat.S_IMODE(log_path.stat().st_mode))
            return real_fdopen(fd, *args, **kwargs)

        with patch("app.guardrails.audit.os.fdopen", side_effect=_stat_at_fdopen_time):
            _make_logger(tmp_path).log(rule_name="r", action="audit", matched_text="x")

        assert observed_mode, "os.fdopen was never called — log() did not run"
        assert observed_mode[0] == 0o600, (
            f"file was world-readable for a moment: mode at fdopen time = "
            f"{oct(observed_mode[0])}; this is the TOCTOU window the previous "
            "fix had — atomic os.open(O_CREAT, 0o600) closes it"
        )
