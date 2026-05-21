"""Damerau-Levenshtein typo correction for intent matching vocabulary."""

from __future__ import annotations

import re

# Canonical vocabulary used for typo correction before intent matching.
# Keep this bounded to known intent/command keywords to avoid broad rewrites.
_INTENT_CANONICAL_TOKENS: tuple[str, ...] = (
    "about",
    "agent",
    "agents",
    "alert",
    "alerts",
    "all",
    "and",
    "anthropic",
    "api",
    "audit",
    "benchmark",
    "cancel",
    "change",
    "check",
    "claude-code",
    "cli",
    "command",
    "commands",
    "connect",
    "connected",
    "connections",
    "configured",
    "current",
    "datadog",
    "database",
    "db",
    "demo",
    "deploy",
    "deployment",
    "deployments",
    "details",
    "diagnose",
    "doctor",
    "execute",
    "exit",
    "fire",
    "find",
    "forget",
    "gemini",
    "gemini-cli",
    "get",
    "guardrail",
    "guardrails",
    "health",
    "help",
    "integrations",
    "investigate",
    "kill",
    "launch",
    "list",
    "llm",
    "local",
    "logs",
    "model",
    "nvidia",
    "ollama",
    "onboard",
    "openai",
    "opensre",
    "openrouter",
    "provider",
    "quit",
    "rds",
    "register",
    "remote",
    "run",
    "sample",
    "send",
    "select",
    "service",
    "services",
    "set",
    "setup",
    "show",
    "simple",
    "start",
    "status",
    "stop",
    "switch",
    "synthetic",
    "task",
    "tasks",
    "terminate",
    "test",
    "tests",
    "trigger",
    "uninstall",
    "update",
    "use",
    "version",
    "what",
    "which",
)
_INTENT_CANONICAL_TOKEN_SET: frozenset[str] = frozenset(_INTENT_CANONICAL_TOKENS)
_INTENT_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_TYPO_MAX_DISTANCE = 2


def _damerau_levenshtein_distance(a: str, b: str) -> int:
    """Compute Damerau-Levenshtein distance (insert/delete/substitute/transpose)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    rows = len(a) + 1
    cols = len(b) + 1
    dp = [[0] * cols for _ in range(rows)]

    for i in range(rows):
        dp[i][0] = i
    for j in range(cols):
        dp[0][j] = j

    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,  # deletion
                dp[i][j - 1] + 1,  # insertion
                dp[i - 1][j - 1] + cost,  # substitution
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                dp[i][j] = min(dp[i][j], dp[i - 2][j - 2] + 1)  # transposition
    return dp[-1][-1]


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Return [start, end) spans enclosed by backticks or quotes."""
    spans: list[tuple[int, int]] = []
    active_quote: str | None = None
    start = -1
    escape = False
    for idx, ch in enumerate(text):
        if active_quote is None:
            if ch in {"`", "'", '"'}:
                active_quote = ch
                start = idx
            continue
        if active_quote != "`" and ch == "\\" and not escape:
            escape = True
            continue
        if ch == active_quote and not escape:
            spans.append((start, idx + 1))
            active_quote = None
            start = -1
        escape = False
    if active_quote is not None and start >= 0:
        spans.append((start, len(text)))
    return spans


def _in_protected_span(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start >= span_start and end <= span_end for span_start, span_end in spans)


def _best_token_correction(token: str) -> str:
    lower = token.lower()
    if lower in _INTENT_CANONICAL_TOKEN_SET or len(lower) < 3:
        return lower

    best: str | None = None
    best_distance = _TYPO_MAX_DISTANCE + 1
    for candidate in _INTENT_CANONICAL_TOKENS:
        # Keep matching scoped: avoid cross-shape rewrites such as underscore/hyphen form drift.
        if ("_" in lower) != ("_" in candidate):
            continue
        if ("-" in lower) != ("-" in candidate):
            continue
        distance = _damerau_levenshtein_distance(lower, candidate)
        if distance > _TYPO_MAX_DISTANCE:
            continue
        if distance < best_distance:
            best = candidate
            best_distance = distance
            continue
        if distance == best_distance and best is not None:
            # Deterministic tie-break: prefer closest length, then lexical order.
            current_len_delta = abs(len(candidate) - len(lower))
            best_len_delta = abs(len(best) - len(lower))
            if current_len_delta < best_len_delta or (
                current_len_delta == best_len_delta and candidate < best
            ):
                best = candidate
    return best or lower


def normalize_intent_text(text: str) -> str:
    """Return typo-corrected, lower-cased text for intent matching.

    Correction is intentionally bounded to a canonical intent vocabulary so we
    can be aggressive without rewriting arbitrary user content.
    """
    if not text:
        return text

    spans = _protected_spans(text)
    out: list[str] = []
    cursor = 0
    for match in _INTENT_TOKEN_RE.finditer(text):
        start, end = match.span()
        out.append(text[cursor:start])
        token = match.group(0)
        if _in_protected_span(start, end, spans):
            out.append(token)
        else:
            out.append(_best_token_correction(token))
        cursor = end
    out.append(text[cursor:])
    return "".join(out).lower()


def is_single_edit_typo(a: str, b: str) -> bool:
    """Return True when *a* and *b* are within one Damerau–Levenshtein edit of each other."""
    return _damerau_levenshtein_distance(a, b) <= 1
