"""Shared failure explanation for all LLM CLI adapters.

Adapters call :func:`explain_cli_failure` from ``explain_failure`` so generic
quota/auth/context/network handling lives in one place. The runner must not
re-classify or override adapter messages.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.llm.failure_classification import (
    classify_cli_failure_category_hint,
    classify_cli_failure_hint,
    is_context_length_overflow,
)

__all__ = [
    "classify_cli_failure_category_hint",
    "classify_cli_failure_hint",
    "explain_cli_failure",
    "is_context_length_overflow",
]


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
