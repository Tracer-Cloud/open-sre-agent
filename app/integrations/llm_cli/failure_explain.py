"""Shared failure explanation for all LLM CLI adapters.

Adapters call :func:`explain_cli_failure` from ``explain_failure`` so generic
quota/auth/context/network handling lives in one place. The runner must not
re-classify or override adapter messages.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# Patterns ordered by specificity — first match wins.
_QUOTA_RE = re.compile(
    r"quota|rate.?limit|429|too many request|insufficient_quota|"
    r"out of credit|billing|usage limit|spending limit|plan limit|"
    r"exceeded.*limit|limit.*exceeded|maximum.*usage",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"unauthorized|401|invalid.?api.?key|api.?key.*invalid|"
    r"authentication.?fail|not authenticated|not logged.?in|"
    r"no credentials|token.*expired|expired.*token|invalid.?token|"
    r"permission denied|access denied|403|forbidden",
    re.IGNORECASE,
)
_CONTEXT_OVERFLOW_RE = re.compile(
    r"context.?length|context.?window|max(?:imum)?\s+context\s+length|"
    r"max.?token|token.?limit|prompt.*too\s+long|prompt.*too.?large|"
    r"input.*exceed|reduce.*context|string too long",
    re.IGNORECASE,
)


def is_context_length_overflow(message: str) -> bool:
    """Return True when *message* indicates prompt/context/token limit exhaustion.

    Avoids bare ``too long`` so timeout strings like "request took too long"
    are not misclassified as context overflow.
    """
    return _CONTEXT_OVERFLOW_RE.search(message) is not None


_NETWORK_RE = re.compile(
    r"network.*error|connection.*refused|dns.*fail|unreachable|"
    r"no route to host|connection reset|ssl.*error|certificate.*error|"
    r"name.*resolution|getaddrinfo",
    re.IGNORECASE,
)
_ERROR_KEYWORD_RE = re.compile(r"error|fail|exception|invalid", re.IGNORECASE)

_SILENT_FAILURE_HINT = (
    "no error detail from the CLI — most likely quota exhausted or expired auth; "
    "check your plan/credits or re-login"
)


def classify_cli_failure_category_hint(stdout: str, stderr: str, _returncode: int) -> str | None:
    """Return a category hint (quota/auth/context/network) when output matches."""
    combined = f"{stdout}\n{stderr}".strip()

    if _QUOTA_RE.search(combined):
        return "quota or rate limit exceeded — check your plan/billing or wait before retrying"
    if _AUTH_RE.search(combined):
        return "authentication failed — verify your API key or re-login with the provider CLI"
    if is_context_length_overflow(combined):
        return (
            "prompt too long — shorten the input or reduce accumulated context "
            "(/context to inspect)"
        )
    if _NETWORK_RE.search(combined):
        return "network error — check connectivity and provider status"
    return None


def classify_cli_failure_hint(stdout: str, stderr: str, returncode: int) -> str | None:
    """Return a short actionable hint for a known failure category, or None."""
    category = classify_cli_failure_category_hint(stdout, stderr, returncode)
    if category is not None:
        return category

    combined = f"{stdout}\n{stderr}".strip()
    if returncode not in (0, 130) and (
        not combined or (len(combined) < 120 and not _ERROR_KEYWORD_RE.search(combined))
    ):
        return _SILENT_FAILURE_HINT

    return None


def explain_cli_failure(
    *,
    exit_label: str,
    stdout: str,
    stderr: str,
    returncode: int,
    extra_messages: Sequence[str] = (),
    always_include_output_snippet: bool = False,
) -> str:
    """Build a human-readable failure string for a non-zero CLI exit.

    Args:
        exit_label: Command label shown to users (e.g. ``codex exec``).
        stdout: Process stdout (ANSI-stripped).
        stderr: Process stderr (ANSI-stripped).
        returncode: Subprocess exit code.
        extra_messages: Provider-specific messages inserted before generic hints.
        always_include_output_snippet: When True, append stderr/stdout after
            ``extra_messages`` (used by adapters that surface raw CLI output
            alongside tailored guidance).
    """
    err = (stderr or "").strip()
    out = (stdout or "").strip()
    bits: list[str] = [f"{exit_label} exited with code {returncode}"]
    bits.extend(msg for msg in extra_messages if msg)
    has_extra = len(bits) > 1

    if has_extra:
        if always_include_output_snippet:
            if err:
                bits.append(err[:2000])
            elif out:
                bits.append(out[:2000])
        return ". ".join(bits)

    if always_include_output_snippet:
        if err:
            bits.append(err[:2000])
        elif out:
            bits.append(out[:2000])
        else:
            hint = classify_cli_failure_hint(stdout, stderr, returncode)
            if hint:
                bits.append(hint)
        return ". ".join(bits)

    category = classify_cli_failure_category_hint(stdout, stderr, returncode)
    if err:
        bits.append(category if category else err[:2000])
    elif out:
        bits.append(category if category else out[:2000])
    else:
        hint = classify_cli_failure_hint(stdout, stderr, returncode)
        if hint:
            bits.append(hint)

    return ". ".join(bits)
