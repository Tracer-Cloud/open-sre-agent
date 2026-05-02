"""JSONL audit logger for guardrail events.

Records that a guardrail rule matched a piece of text, **without** persisting
the matched text itself. The rule already redacted the secret out of the
LLM-bound payload; the audit log must not leak it back onto disk.

Each entry stores:

* ``timestamp`` — ISO-8601 UTC timestamp.
* ``rule_name`` — the rule that fired.
* ``action`` — ``redact`` / ``block`` / ``audit``.
* ``match_fingerprint`` — first 12 hex characters of the SHA-256 hash of
  the matched text. Deterministic (identical secrets produce identical
  fingerprints, so an SRE can identify systemic leaks) but irreversible
  (the original text cannot be recovered from the audit log).
* ``match_length`` — character length of the matched text. Useful for
  triage ("a 40-char match in the ``aws_secret_key`` rule is probably a
  real key") without leaking the bytes.
* ``context`` — caller-supplied tag (e.g. ``llm_invoke``, ``chat_node``).

The audit file is written with ``0o600`` and lives under a ``0o700``
directory, both owner-only, so that a non-root local user on the same
machine cannot read the audit trail and reconstruct what was redacted.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_AUDIT_PATH = Path.home() / ".opensre" / "guardrail_audit.jsonl"

_FINGERPRINT_LEN = 12  # 12 hex chars × 4 bits = 48 bits of collision space.

# Owner-only filesystem permissions for the audit trail. The audit log can
# contain provider names, rule names, and timestamps that — even without the
# matched text — describe what kinds of secrets a user has been handling.
# Restricting access to the owning user is defence-in-depth on top of
# fingerprinting the match itself.
_AUDIT_FILE_MODE = 0o600
_AUDIT_DIR_MODE = 0o700


def _fingerprint(text: str) -> str:
    """Return a 12-hex-char SHA-256 prefix of ``text``.

    SHA-256 is used (rather than e.g. blake2b-128 or HMAC) for stdlib-only
    portability and for the well-known property that distinct inputs almost
    never collide in 48 bits of output. The fingerprint is **not** keyed —
    an attacker who knows a candidate secret can confirm a fingerprint
    match, but cannot recover an unknown secret from the log alone.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]


class AuditLogger:
    """Append-only JSONL audit log for guardrail matches."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_AUDIT_PATH

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
        fingerprinted with SHA-256 before persistence — the literal value
        is **never** written to the audit log.
        """
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "rule_name": rule_name,
            "action": action,
            "match_fingerprint": _fingerprint(matched_text),
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
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            with contextlib.suppress(OSError):
                os.chmod(self._path, _AUDIT_FILE_MODE)
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
