"""Streaming redaction wrapper around :class:`GuardrailEngine`.

Used by the local-agents fleet view (``/agents trace <pid>``) to redact
secrets from agent stdout before it reaches the user's terminal scrollback.

The shipped engine assumes a single complete string (LLM input or output),
so calling it on raw stdout chunks would miss any secret whose bytes
straddle two reads. This module buffers chunks until a safe boundary
(newline or ``max_chunk_len`` fallback), runs the engine on the bounded
window, and returns the redacted version.

The engine raises :class:`GuardrailBlockedError` on BLOCK matches, which is
correct for an LLM input pipeline but wrong for a stdout tap where raising
would silently truncate the agent's output. The streaming wrapper promotes
BLOCK to REDACT for this code path so the stream stays open and every
detected secret is replaced equivalently.

A new ``agent_secret_detected`` analytics event fires once per flushed
chunk that contained at least one match.
"""

from __future__ import annotations

from collections.abc import Callable

from app.analytics.cli import capture_agent_secret_detected
from app.guardrails.audit import AuditLogger
from app.guardrails.engine import GuardrailEngine, ScanMatch
from app.guardrails.rules import GuardrailAction

_DEFAULT_MAX_CHUNK_LEN = 4096


class GuardrailStream:
    """Buffer agent stdout into safe windows and redact via :class:`GuardrailEngine`.

    Flushes whenever the buffer contains a newline (the natural boundary for
    most CLI output) and force-flushes once the buffer reaches
    ``max_chunk_len`` so a runaway no-newline stream cannot grow without
    bound. Detected secrets are replaced with the rule's configured
    ``replacement`` (default ``[REDACTED:<rule_name>]``) and logged via the
    optional audit logger so the original is quarantined. AUDIT-action
    matches are logged but pass through unchanged, mirroring
    :meth:`GuardrailEngine.apply`.
    """

    def __init__(
        self,
        engine: GuardrailEngine,
        *,
        max_chunk_len: int = _DEFAULT_MAX_CHUNK_LEN,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._engine = engine
        self._max_chunk_len = max_chunk_len
        self._audit = audit_logger
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        """Append ``chunk`` to the buffer and return any safe-bounded redacted output.

        Returns the empty string while the buffer has no newline and is
        below ``max_chunk_len``.
        """
        self._buffer += chunk
        flushable, self._buffer = _split_at_boundary(self._buffer, self._max_chunk_len)
        if not flushable:
            return ""
        return self._scan_and_redact(flushable)

    def flush(self) -> str:
        """Emit any buffered tail. Call on stream close so a trailing line without
        a final newline is not silently dropped."""
        if not self._buffer:
            return ""
        text, self._buffer = self._buffer, ""
        return self._scan_and_redact(text)

    def _scan_and_redact(self, text: str) -> str:
        if not self._engine.is_active:
            return text

        result = self._engine.scan(text)
        if not result.matches:
            return text

        if self._audit is not None:
            for m in result.matches:
                self._audit.log(
                    rule_name=m.rule_name,
                    action=m.action.value,
                    matched_text_preview=m.matched_text,
                )

        rule_names = tuple(sorted({m.rule_name for m in result.matches}))
        capture_agent_secret_detected(
            rule_names=rule_names,
            count=len(result.matches),
            blocked=result.blocked,
        )
        return _redact_intervals(text, result.matches, self._engine._get_replacement)


def _split_at_boundary(buffer: str, max_chunk_len: int) -> tuple[str, str]:
    """Return ``(flushable, residual)``.

    Splits at the last newline so a secret that straddles two ``feed`` calls
    stays buffered as one piece for the next scan. Force-flushes once the
    buffer reaches ``max_chunk_len`` without seeing a newline so a runaway
    no-newline stream cannot grow without bound.
    """
    last_nl = buffer.rfind("\n")
    if last_nl >= 0:
        return buffer[: last_nl + 1], buffer[last_nl + 1 :]
    if len(buffer) >= max_chunk_len:
        return buffer, ""
    return "", buffer


def _redact_intervals(
    text: str,
    matches: tuple[ScanMatch, ...],
    get_replacement: Callable[[str], str],
) -> str:
    """Replace match ranges with their per-rule replacement, merging overlapping spans.

    AUDIT-action matches are filtered out here so the streaming path mirrors
    :meth:`GuardrailEngine._redact`: AUDIT means "log only", not "replace".
    REDACT and BLOCK matches are processed; BLOCK is included because the
    streaming wrapper promotes BLOCK to REDACT to keep the stream open.

    The widest source match wins on rule-name selection, matching engine
    behavior so output looks consistent across the LLM and agent-stdout
    code paths. ``get_replacement`` is :meth:`GuardrailEngine._get_replacement`
    so custom ``rule.replacement`` values are honored.
    """
    redactable = [m for m in matches if m.action != GuardrailAction.AUDIT]
    if not redactable:
        return text

    sorted_matches = sorted(redactable, key=lambda m: (m.start, -m.end))
    # Each merged span tracks (start, end, rule_name, source_width).
    # source_width is the width of the contributing match that owns the
    # rule_name, so that chained-overlap merges keep picking the widest
    # individual match's name rather than the latest one's.
    merged: list[tuple[int, int, str, int]] = []
    for m in sorted_matches:
        source_width = m.end - m.start
        if merged and m.start < merged[-1][1]:
            prev_start, prev_end, prev_name, prev_width = merged[-1]
            new_end = max(prev_end, m.end)
            if source_width > prev_width:
                merged[-1] = (prev_start, new_end, m.rule_name, source_width)
            else:
                merged[-1] = (prev_start, new_end, prev_name, prev_width)
        else:
            merged.append((m.start, m.end, m.rule_name, source_width))

    out = text
    for start, end, name, _ in reversed(merged):
        out = out[:start] + get_replacement(name) + out[end:]
    return out
