"""Retry helper for transient LLM provider errors.

Centralises two concerns that previously lived in two places (and were
ad-hoc in others):

  1. Recognizing a "rate limit" error across providers — OpenAI, Anthropic,
     and the various wrappers opensre's clients add on top all surface
     429s with different exception classes but consistent message text.
  2. Retrying with exponential backoff when the recognizer says yes.

Why a helper instead of catching the SDK's typed exceptions everywhere:
opensre wraps provider exceptions into ``RuntimeError`` at boundaries
(see e.g. ``AnthropicAgentClient.invoke`` raising
``RuntimeError("Anthropic rate limit exceeded: ...")``). Downstream code
only sees the wrapped text — matching by text is the common denominator
without taking a hard import dependency on every provider's exception
module.

The agent LLM client (:mod:`core.runtime.llm.agent_llm_client`) uses
:func:`is_rate_limit_error` inside its own retry loop so the
investigation survives a transient 429 the same way it survives a 500.
Any code path that performs its own LLM call can wrap it with
:func:`retry_on_rate_limit` to inherit the same backoff + jitter
contract.
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

# Body-text patterns providers embed in the error message:
#   - OpenAI:        ``"Please try again in 94ms"`` / ``"try again in 36s"``
#   - Google/Gemini: ``"Please retry in 5.478s"``
# Anthropic does not include a body hint; it relies on the HTTP ``retry-after``
# header instead.
_BODY_RETRY_HINT_RE = re.compile(
    r"(?:try again in|retry in) (\d+(?:\.\d+)?)\s*(ms|s)\b", re.IGNORECASE
)

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

# Provider-side billing / quota exhaustion is detected in two layers:
#
#   1. Structured error code (PREFERRED, reword-proof). OpenAI exposes a
#      stable enum on its typed exceptions (``APIError.code``) and in the
#      response body (``body.error.code``). Documented at
#      https://platform.openai.com/docs/guides/error-codes — these codes
#      are part of OpenAI's API contract and don't change as message
#      wording is updated.
#   2. Text-substring fallback (REQUIRED for Anthropic, defensive for
#      wrapped RuntimeErrors). Anthropic does NOT expose a credit-specific
#      error type — credit-too-low lands as a generic
#      ``invalid_request_error`` (400) with the wording in the message body
#      only. See https://docs.anthropic.com/en/api/errors. Text matching is
#      the only signal Anthropic gives us; reword-fragility is theirs, not
#      ours, but our tests pin the current wording so CI catches drift.

# Stable structured codes — match these before text. OpenAI uses these
# exact strings on both ``RateLimitError.code`` and ``body.error.code``
# (sometimes also ``body.error.type``).
_CREDIT_EXHAUSTED_CODES: frozenset[str] = frozenset(
    {
        "insufficient_quota",  # OpenAI: out of credit / over plan limit
        "billing_hard_limit_reached",  # OpenAI: org-level monthly cap
    }
)

# Text fallback for providers without a billing-specific error code
# (Anthropic) AND for already-wrapped ``RuntimeError`` messages that
# downstream code might see after agent_llm_client has rewrapped the
# typed exception. Kept disjoint from the rate-limit hints above so
# ``is_rate_limit_error`` and ``is_credit_exhausted_error`` never both
# match the same string.
_CREDIT_EXHAUSTED_HINTS: tuple[str, ...] = (
    "insufficient_quota",  # OpenAI code may appear in stringified error
    "billing_hard_limit_reached",
    "exceeded your current quota",  # OpenAI body message
    "credit balance is too low",  # Anthropic message
    "credit balance too low",  # normalized
)


def _structured_error_code(exc: BaseException) -> str | None:
    """Pull a provider's stable error code from a typed SDK exception.

    Looks in two places, in priority order:

      1. ``exc.code`` — set directly on OpenAI's ``APIError`` subclasses
         (RateLimitError, BadRequestError, etc.).
      2. ``exc.body.error.code`` — set on the response-body dict for
         OpenAI errors when the SDK has parsed the body.

    Returns ``None`` for exceptions without a code attribute (e.g.
    Anthropic SDK errors, generic RuntimeErrors). Callers fall back to
    text matching in that case.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        return code
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error_obj = body.get("error")
        if isinstance(error_obj, dict):
            nested_code = error_obj.get("code")
            if isinstance(nested_code, str):
                return nested_code
    return None


class LLMCreditExhaustedError(Exception):
    """Provider-side billing / quota exhaustion — fatal, not retry-recoverable.

    Raised by the LLM clients when the provider returns ``insufficient_quota``,
    ``billing_hard_limit_reached``, ``"credit balance too low"``, or
    equivalent. UNLIKE transient rate-limit errors, retries don't help —
    the operator must top up balance or raise the spending cap.

    Long-running orchestrators (multi-cell batches, scheduled investigations)
    should halt on first occurrence rather than continuing — every subsequent
    LLM call will fail the same way until the account is topped up.
    Interactive callers should surface the error to the operator with clear
    "fix your billing" guidance.

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

    Detection order:
      1. **Structured error code** (OpenAI). Reword-proof — ``exc.code``
         and ``body.error.code`` are part of OpenAI's API contract.
      2. **Text-substring fallback** (Anthropic + already-wrapped
         RuntimeErrors). Anthropic surfaces "credit balance is too low"
         only in the message body — they don't expose a billing-specific
         error type. Text matching is the only signal we get.

    Distinct from :func:`is_rate_limit_error` (which is transient/TPM and
    retry-recoverable). Callers SHOULD NOT retry when this returns True —
    raise :class:`LLMCreditExhaustedError` and let it propagate.
    """
    structured_code = _structured_error_code(exc)
    if structured_code is not None and structured_code in _CREDIT_EXHAUSTED_CODES:
        return True
    text = str(exc).lower()
    return any(hint in text for hint in _CREDIT_EXHAUSTED_HINTS)


def _structured_retry_delay_seconds(exc: BaseException) -> float | None:
    """Return Google/Gemini's structured retry hint in seconds, or ``None``.

    Gemini does not set an HTTP ``retry-after`` header. It carries the delay in
    a ``RetryInfo`` entry of the error body's ``details`` array, e.g.::

        {"error": {"details": [
            {"@type": ".../google.rpc.RetryInfo", "retryDelay": "5s"},
        ]}}

    The value is a protobuf ``Duration`` string (``"5s"`` / ``"5.478s"``).
    """
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None
    error_obj = body.get("error")
    if not isinstance(error_obj, dict):
        return None
    for detail in error_obj.get("details") or []:
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        if delay:
            match = re.match(r"^(\d+(?:\.\d+)?)\s*s$", str(delay).strip())
            if match:
                return float(match.group(1))
    return None


def extract_retry_after_seconds(exc: BaseException) -> float | None:
    """Return the provider-suggested retry delay in seconds, or ``None``.

    Looks in three places, in priority order:

      1. The HTTP ``retry-after`` header on the underlying response object.
         Both Anthropic and OpenAI SDK errors expose ``err.response.headers``.
         RFC 7231 allows the value to be either ``"<integer seconds>"`` or
         an HTTP-date; we honor the integer form and skip dates (rare in
         practice and not worth the parsing complexity).
      2. Google/Gemini's structured ``error.details[].retryDelay`` body hint
         (Gemini ships no ``retry-after`` header).
      3. The body-text hint providers embed in the message:
         OpenAI's ``"Please try again in 94ms"`` or Gemini's
         ``"Please retry in 5.478s"``. The regex tolerates ``ms`` or ``s``.

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

    structured = _structured_retry_delay_seconds(exc)
    if structured is not None:
        return min(structured, RETRY_AFTER_MAX_SEC)

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
    deterministic ``time.sleep(backoff)``. With multiple concurrent callers, a
    deterministic backoff would have all rate-limited clients wake up at the
    same instant and retry in lockstep, immediately re-hitting the TPM
    bucket. Full jitter is the pattern AWS recommends and matches what the
    Anthropic + OpenAI SDKs do internally.

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
