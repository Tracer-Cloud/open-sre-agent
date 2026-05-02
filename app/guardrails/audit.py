"""JSONL audit logger for guardrail events.

Records that a guardrail rule matched a piece of text, **without** persisting
the matched text itself. The rule already redacted the secret out of the
LLM-bound payload; the audit log must not leak it back onto disk.

Each entry stores:

* ``timestamp`` — ISO-8601 UTC timestamp.
* ``rule_name`` — the rule that fired.
* ``action`` — ``redact`` / ``block`` / ``audit``.
* ``match_fingerprint`` — first 12 hex characters of an HMAC-SHA-256 of the
  matched text, keyed by a per-machine secret stored in
  ``~/.opensre/.audit_key`` (``0o600``). Deterministic on the same machine
  so identical secrets produce identical fingerprints (the SRE-dedup
  property), but **opaque** to anyone who exfiltrates only the JSONL —
  without the per-machine key the fingerprint cannot be recomputed even
  for a known candidate secret. This defeats offline confirmation
  attacks against fixed-format credentials (AWS Access Keys, GitHub
  PATs).
* ``match_length`` — character length of the matched text. Useful for
  triage ("a 40-char match in the ``aws_secret_key`` rule is probably a
  real key") without leaking the bytes.
* ``context`` — caller-supplied tag (e.g. ``llm_invoke``, ``chat_node``).

The audit file is written with ``0o600`` and lives under a ``0o700``
directory, both owner-only. The file is **created atomically** via
``os.open(O_CREAT, mode=0o600)`` so there is no umask-controlled window
between creation and ``chmod`` for an ``inotify`` watcher to read it.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_AUDIT_PATH = Path.home() / ".opensre" / "guardrail_audit.jsonl"
_DEFAULT_KEY_PATH = Path.home() / ".opensre" / ".audit_key"

_FINGERPRINT_LEN = 12  # 12 hex chars × 4 bits = 48 bits of collision space.

# Owner-only filesystem permissions for the audit trail. The audit log can
# contain provider names, rule names, and timestamps that — even without the
# matched text — describe what kinds of secrets a user has been handling.
# Restricting access to the owning user is defence-in-depth on top of
# fingerprinting the match itself.
_AUDIT_FILE_MODE = 0o600
_AUDIT_DIR_MODE = 0o700
_KEY_BYTES = 32  # 256 bits is overkill for HMAC-SHA-256 keying; matches the digest size.


def _atomic_open_append(path: Path, mode: int) -> int:
    """Open ``path`` for append, creating it atomically with ``mode``.

    Calling ``open("a")`` on a non-existent file leaves a window between
    file creation (with the process umask) and any subsequent ``chmod``,
    in which a local ``inotify`` watcher can race in and read the file
    while it still has umask-controlled permissions. ``os.open(O_CREAT)``
    closes the window — the file is created with the requested mode in a
    single syscall.

    The kernel still ANDs the requested mode with ``~umask``, but ``0o600``
    has no group/other bits set, so any reasonable umask (``0o022``,
    ``0o077``, ``0o0277``) leaves it unchanged. We do not touch the
    process-wide umask.
    """
    fd = os.open(
        os.fspath(path),
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        mode,
    )
    # If the file pre-existed with looser perms, ``open`` does not retighten
    # them — fix that here so a stale 0o644 file from a pre-fix install
    # self-heals on next write. Best-effort; chmod on Windows / non-owned
    # files swallows.
    with contextlib.suppress(OSError):
        os.chmod(path, mode)
    return fd


class _AuditKey:
    """Per-machine HMAC key persisted at ``~/.opensre/.audit_key``.

    The key is generated with ``secrets.token_bytes(32)`` on first use and
    stored owner-only (``0o600``). Subsequent ``AuditLogger`` instances on
    the same machine read the same key, preserving the dedup property of
    fingerprints. An attacker who exfiltrates ``guardrail_audit.jsonl``
    without also obtaining ``.audit_key`` cannot recompute fingerprints
    even for a known candidate secret.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._key: bytes | None = None

    def get(self) -> bytes:
        if self._key is not None:
            return self._key
        # Try to read an existing key first. If anything goes wrong (file
        # missing, perms wrong, truncated content) generate a fresh key.
        try:
            existing = self._path.read_bytes()
            if len(existing) == _KEY_BYTES:
                self._key = existing
                return self._key
        except OSError:
            pass
        # Generate, persist atomically with 0o600, cache.
        new_key = secrets.token_bytes(_KEY_BYTES)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=_AUDIT_DIR_MODE)
            with contextlib.suppress(OSError):
                os.chmod(self._path.parent, _AUDIT_DIR_MODE)
            fd = _atomic_open_append(self._path, _AUDIT_FILE_MODE)
            try:
                # Write the key bytes via the fd we just opened. Using
                # ``os.write`` rather than fdopen keeps this short and
                # avoids buffering questions for a one-shot write.
                # First truncate in case ``existing`` was partial — without
                # this a 16-byte stale file would corrupt the new 32-byte key.
                os.ftruncate(fd, 0)
                os.write(fd, new_key)
            finally:
                os.close(fd)
        except OSError:
            # If we cannot persist the key, fall back to an in-memory key
            # so at least *this* process's audit entries are HMAC'd. The
            # cost is that fingerprint dedup won't survive a restart.
            logger.warning(
                "Could not persist guardrail audit key to %s; using process-local key",
                self._path,
            )
        self._key = new_key
        return self._key


class AuditLogger:
    """Append-only JSONL audit log for guardrail matches."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        key_path: Path | None = None,
    ) -> None:
        self._path = path or _DEFAULT_AUDIT_PATH
        # Default key co-located with the audit log so a single ``~/.opensre``
        # directory holds the whole subsystem. Test paths can override with
        # ``key_path=tmp_path / ".audit_key"`` for isolation.
        self._key = _AuditKey(key_path or self._default_key_path())

    def _default_key_path(self) -> Path:
        # Co-locate the key file next to the audit file when a custom audit
        # path is given. Falls back to ``~/.opensre/.audit_key`` otherwise.
        if self._path == _DEFAULT_AUDIT_PATH:
            return _DEFAULT_KEY_PATH
        return self._path.parent / ".audit_key"

    def _fingerprint(self, text: str) -> str:
        """Return a 12-hex-char HMAC-SHA-256 prefix keyed by the per-machine
        audit key. See module docstring for the threat-model rationale."""
        digest = hmac.new(self._key.get(), text.encode("utf-8"), "sha256").hexdigest()
        return digest[:_FINGERPRINT_LEN]

    def log(
        self,
        *,
        rule_name: str,
        action: str,
        matched_text: str,
        context: str = "",
    ) -> None:
        """Append one audit entry. Never raises on write failure.

        ``matched_text`` is what the rule scanned-and-matched. It is
        HMAC-fingerprinted before persistence — the literal value is
        **never** written to the audit log.
        """
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "rule_name": rule_name,
            "action": action,
            "match_fingerprint": self._fingerprint(matched_text),
            "match_length": len(matched_text),
            "context": context,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=_AUDIT_DIR_MODE)
            # ``mkdir(mode=...)`` only sets perms when creating; a pre-existing
            # parent dir keeps whatever umask gave it. Force-tighten unconditionally
            # so re-runs with a stale loose-perm dir self-heal. Best-effort: chmod
            # may fail on Windows or non-owned dirs — swallow that, perms there
            # follow ACL semantics anyway.
            with contextlib.suppress(OSError):
                os.chmod(self._path.parent, _AUDIT_DIR_MODE)
            fd = _atomic_open_append(self._path, _AUDIT_FILE_MODE)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            logger.warning("Failed to write guardrail audit log to %s", self._path)

    def read_entries(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Read the most recent audit entries."""
        if not self._path.exists():
            return []
        try:
            lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries
