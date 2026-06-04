"""Retry helper for transient LLM provider errors.

Centralises two concerns that previously lived in two places (and were
ad-hoc in others):

  1. Recognizing a "rate limit" error across providers — OpenAI, Anthropic,
     and the various wrappers opensre's clients add on top all surface
     429s with different exception classes but consistent message text.
  2. Retrying with exponential backoff when the recognizer says yes.

Used by:
  - ``tests/benchmarks/cloudopsbench/predictor.py`` — wraps its one-shot
    LLM call so the structured-output emitter doesn't silently degrade to
    None on a transient 429.
  - ``app/services/agent_llm_client.py`` — uses :func:`is_rate_limit_error`
    inside its existing retry loop so the investigation loop survives a
    transient 429 the same way it survives a 500.

Why a helper instead of catching the SDK's typed exceptions everywhere:
opensre wraps provider exceptions into ``RuntimeError`` at boundaries
(see e.g. ``AnthropicAgentClient.invoke`` raising
``RuntimeError("Anthropic rate limit exceeded: ...")``). Downstream code
(the predictor, future cross-provider tooling) only sees the wrapped
text. Matching by text is the only common denominator without taking a
hard import dependency on every provider's exception module.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_INITIAL_BACKOFF_SEC = 2.0

# Hard cap on any single sleep — bounds pathological ``Retry-After`` values
# (e.g. a misconfigured provider returning 3600s would otherwise hang the
# investigation loop). 30s is a balance: long enough to honor genuine
# multi-second TPM resets, short enough that operator interruption is fast.
RETRY_AFTER_MAX_SEC = 30.0

# Body-text pattern OpenAI uses: ``"Please try again in 94ms"`` or
# ``"try again in 36s"``. Anthropic does not include a body hint; relies on
# the HTTP ``retry-after`` header instead.
_BODY_RETRY_HINT_RE = re.compile(r"try again in (\d+(?:\.\d+)?)\s*(ms|s)\b", re.IGNORECASE)

# Substrings present in the error text of OpenAI's RateLimitError, Anthropic's
# RateLimitError, the RuntimeError wrappers opensre's clients raise on top of
# those, and the structured `code: "rate_limit_exceeded"` payload. Lower-cased
# at compare time so casing differences across providers do not matter.
_RATE_LIMIT_HINTS: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "429",
    "tokens per min",
    "tpm",
)

# Substrings that indicate provider-side billing / quota exhaustion. UNLIKE
# rate limits, these are NOT transient — retrying won't help until the
# operator tops up balance or raises the spending cap. Kept disjoint from
# the rate-limit hints above so ``is_rate_limit_error`` and
# ``is_credit_exhausted_error`` never both match the same string.
#   - OpenAI:    error code ``insufficient_quota``, body text
#                "You exceeded your current quota"
#   - Anthropic: HTTP 400 with body text
#                "Your credit balance is too low to access the Anthropic API"
#   - OpenAI:    error code ``billing_hard_limit_reached`` (org-level cap)
_CREDIT_EXHAUSTED_HINTS: tuple[str, ...] = (
    "insufficient_quota",
    "billing_hard_limit_reached",
    "exceeded your current quota",
    "credit balance is too low",
    "credit balance too low",
)


class LLMCreditExhaustedError(Exception):
    """Provider-side billing / quota exhaustion — fatal, not retry-recoverable.

    Raised by the LLM clients when the provider returns ``insufficient_quota``,
    ``billing_hard_limit_reached``, ``"credit balance too low"``, or
    equivalent. UNLIKE transient rate-limit errors, retries don't help —
    the operator must top up balance or raise the spending cap.

    Bench runner halts the entire run on first occurrence (continuing burns
    wall-clock on a dead account and produces no useful data). Production
    agent surfaces it as a credential / billing error.

    Intentionally NOT a subclass of ``RuntimeError`` so the existing
    catch-all-RuntimeError paths don't accidentally swallow it. Always
    propagate to the operator.
    """


def is_rate_limit_error(exc: BaseException) -> bool:
    """Return True if ``exc`` looks like a transient rate-limit error.

    Provider-agnostic: matches the message text of OpenAI's RateLimitError,
    Anthropic's RateLimitError, opensre's ``RuntimeError("... rate limit
    exceeded: ...")`` wrappers, and 429-shaped errors generally.

    Returns False for non-transient billing/quota errors even though they
    sometimes surface as 429 — those have their own recognizer
    (:func:`is_credit_exhausted_error`) and a separate fatal path.
    """
    text = str(exc).lower()
    if is_credit_exhausted_error(exc):
        # OpenAI's insufficient_quota lands as HTTP 429 with "rate limit"
        # in the surrounding message text. Don't classify it as transient;
        # retries cannot fix a missing balance.
        return False
    return any(hint in text for hint in _RATE_LIMIT_HINTS)


def is_credit_exhausted_error(exc: BaseException) -> bool:
    """Return True if ``exc`` indicates provider billing / quota exhaustion.

    Provider-agnostic text matcher for the non-retryable billing cases:
      - OpenAI 429 with ``code: insufficient_quota`` /
        ``code: billing_hard_limit_reached``
      - Anthropic 400 with body
        ``"Your credit balance is too low to access the Anthropic API"``

    Distinct from :func:`is_rate_limit_error` (which is transient/TPM and
    retry-recoverable). Callers SHOULD NOT retry when this returns True —
    raise :class:`LLMCreditExhaustedError` and let it propagate.
    """
    text = str(exc).lower()
    return any(hint in text for hint in _CREDIT_EXHAUSTED_HINTS)


def extract_retry_after_seconds(exc: BaseException) -> float | None:
    """Return the provider-suggested retry delay in seconds, or ``None``.

    Looks in two places, in priority order:

      1. The HTTP ``retry-after`` header on the underlying response object.
         Both Anthropic and OpenAI SDK errors expose ``err.response.headers``.
         RFC 7231 allows the value to be either ``"<integer seconds>"`` or
         an HTTP-date; we honor the integer form and skip dates (rare in
         practice and not worth the parsing complexity).
      2. OpenAI's body-text hint: ``"Please try again in 94ms"``. The
         regex tolerates either ``ms`` or ``s`` units.

    The result is capped at :data:`RETRY_AFTER_MAX_SEC` to bound pathological
    cases (a misconfigured proxy returning ``retry-after: 3600`` should not
    hang the agent loop for an hour).
    """
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        if headers is not None:
            # Both Anthropic and OpenAI SDKs use ``httpx.Headers`` for the
            # underlying response, which is case-insensitive by spec
            # (RFC 7230 §3.2 — header names are case-insensitive). We rely
            # on that, so "Retry-After" and "retry-after" both resolve.
            # If a future SDK ever ships plain-dict headers, this lookup
            # would silently miss capitalized spellings.
            retry_after = headers.get("retry-after") if hasattr(headers, "get") else None
            if retry_after is not None:
                try:
                    seconds = float(retry_after)
                    if seconds >= 0:
                        return min(seconds, RETRY_AFTER_MAX_SEC)
                except (ValueError, TypeError):
                    pass  # HTTP-date form; fall through to body parsing.

    match = _BODY_RETRY_HINT_RE.search(str(exc))
    if match:
        value = float(match.group(1))
        if match.group(2).lower() == "ms":
            value /= 1000
        return min(value, RETRY_AFTER_MAX_SEC)

    return None


def retry_on_rate_limit[T](
    fn: Callable[[], T],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_backoff_sec: float = DEFAULT_INITIAL_BACKOFF_SEC,
    label: str = "llm",
) -> T:
    """Invoke ``fn``, retrying with jittered exponential backoff on rate-limit errors.

    Returns ``fn()``'s result on success.

    Re-raises the original exception when:
      - the exception is not a rate-limit error (no retry — a 400 won't
        get better by waiting), or
      - ``max_attempts`` retries have exhausted.

    Backoff uses **full jitter** (``sleep ~ Uniform(0, backoff)``) rather than
    deterministic ``time.sleep(backoff)``. With multiple concurrent workers
    (e.g. the bench runner's ``workers: 4``), a deterministic backoff would
    have all rate-limited clients wake up at the same instant and retry in
    lockstep, immediately re-hitting the TPM bucket. Full jitter is the
    pattern AWS recommends and matches what the Anthropic + OpenAI SDKs do
    internally.

    ``label`` is the short tag used in log messages so callers (predictor,
    agent loop, ...) can be told apart in tail-grep.
    """
    backoff = initial_backoff_sec
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:
            if not is_rate_limit_error(exc):
                raise
            if attempt == max_attempts - 1:
                logger.warning(
                    "[%s] rate-limited after %d attempts, giving up: %s",
                    label,
                    max_attempts,
                    exc,
                )
                raise
            # Full jitter — uniform in [0, backoff). Never blocks for the
            # full nominal backoff window; the upper bound still doubles
            # each attempt to provide the exponential growth.
            sleep_sec = random.uniform(0.0, backoff)  # noqa: S311 — backoff jitter, not crypto
            logger.warning(
                "[%s] rate-limited, retrying in %.2fs (jitter from [0, %.1f]s) (attempt %d/%d)",
                label,
                sleep_sec,
                backoff,
                attempt + 1,
                max_attempts,
            )
            time.sleep(sleep_sec)
            backoff *= 2
    # Unreachable: either we returned in the try, or every attempt re-raised.
    # mypy needs the explicit return statement; pragma: no cover keeps line
    # coverage honest.
    raise RuntimeError("retry_on_rate_limit exhausted without raise")  # pragma: no cover
