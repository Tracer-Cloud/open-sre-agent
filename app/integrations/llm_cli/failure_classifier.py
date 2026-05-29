"""Generic failure classifier for LLM CLI and API errors.

Scans stdout/stderr (or an exception message) for known error categories and
returns a short, actionable hint.  The runner calls this after
``adapter.explain_failure()`` so every CLI adapter gets the same enrichment
without each one reimplementing pattern matching.

The same ``classify_llm_failure`` function can be reused from LLM API clients
that catch HTTP 429 / 401 responses.
"""

from __future__ import annotations

import re

# Patterns ordered by specificity — first match wins.
_QUOTA_RE = re.compile(
    r"quota|rate.?limit|429|too many request|insufficient_quota|"
    r"out of credit|billing|usage limit|spending limit|plan limit|"
    r"exceeded.*limit|limit.*exceeded|maximum.*usage|api.*usage",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"unauthorized|401|invalid.?api.?key|api.?key.*invalid|"
    r"authentication.?fail|not authenticated|not logged.?in|"
    r"no credentials|token.*expired|expired.*token|invalid.?token|"
    r"permission denied|access denied|403|forbidden",
    re.IGNORECASE,
)
_CONTEXT_RE = re.compile(
    r"context.?length|context.?window|max.?token|token.?limit|"
    r"too.?long|input.*exceed|prompt.*too.?large|reduce.*context|"
    r"string too long",
    re.IGNORECASE,
)
_NETWORK_RE = re.compile(
    r"network.*error|connection.*refused|dns.*fail|unreachable|"
    r"no route to host|connection reset|ssl.*error|certificate.*error|"
    r"name.*resolution|getaddrinfo",
    re.IGNORECASE,
)


def classify_llm_failure(
    stdout: str,
    stderr: str,
    returncode: int,
) -> str | None:
    """Return a short actionable hint for a known failure category, or None.

    Args:
        stdout: Process stdout, ANSI-stripped (pass empty string for API errors).
        stderr: Process stderr, ANSI-stripped (or the raw exception message).
        returncode: Exit code (use 1 for API / non-subprocess errors).

    Returns:
        A one-sentence user-facing hint, or ``None`` if no pattern matched.
    """
    combined = f"{stdout}\n{stderr}".strip()

    if _QUOTA_RE.search(combined):
        return "quota or rate limit exceeded — check your plan/billing or wait before retrying"
    if _AUTH_RE.search(combined):
        return "authentication failed — verify your API key or re-login with the provider CLI"
    if _CONTEXT_RE.search(combined):
        return "prompt too long — shorten the input or reduce accumulated context (/context to inspect)"
    if _NETWORK_RE.search(combined):
        return "network error — check connectivity and provider status"

    # Fallback: if the only output is a version banner or a very short string
    # with no error keywords, the CLI likely exited silently — the most common
    # causes are exhausted quota or an expired auth session.
    if returncode not in (0, 130) and (
        not combined
        or (
            len(combined) < 120
            and not re.search(r"error|fail|exception|invalid", combined, re.IGNORECASE)
        )
    ):
        return (
            "no error detail from the CLI — most likely quota exhausted or expired auth; "
            "check your plan/credits or re-login"
        )

    return None
