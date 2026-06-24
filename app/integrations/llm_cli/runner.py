"""Shared subprocess executor for `LLMCLIAdapter` implementations."""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel

from app.integrations.llm_cli.base import CLIProbe, LLMCLIAdapter
from app.integrations.llm_cli.constants import (
    EX_TEMPFAIL as _EX_TEMPFAIL,
)
from app.integrations.llm_cli.constants import (
    PROBE_CACHE_TTL_SEC as _PROBE_CACHE_TTL_SEC,
)
from app.integrations.llm_cli.constants import (
    TEMPFAIL_BACKOFF_SEC as _TEMPFAIL_BACKOFF_SEC,
)
from app.integrations.llm_cli.constants import (
    TEMPFAIL_MAX_RETRIES as _TEMPFAIL_MAX_RETRIES,
)
from app.integrations.llm_cli.errors import (
    CLIAuthenticationRequired,
    CLIInterruptedError,
    CLITimeoutError,
)
from app.integrations.llm_cli.subprocess_env import build_cli_subprocess_env
from app.integrations.llm_cli.text import flatten_messages_to_prompt
from app.llm_reasoning_effort import get_active_reasoning_effort
from app.services.llm_client import LLMResponse

logger = logging.getLogger(__name__)

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_REDACTED_PROMPT_ARG = "<redacted-prompt>"
# Avoid re-running `detect()` (two subprocess probes) on every invoke during long
# investigations. Value is defined in shared constants.
# POSIX EX_TEMPFAIL (75): the subprocess hit a transient error and can be retried.
# kimi uses this when a session dies mid-flight ("To resume this session: kimi -r …").

# Back-compat name for tests and imports that expect this symbol on runner.
_build_subprocess_env = build_cli_subprocess_env


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def _sanitize_argv_for_debug(argv: tuple[str, ...], *, prompt: str) -> list[str]:
    """Redact prompt text from debug argv logs when passed as a CLI argument."""
    if not prompt:
        return list(argv)

    redacted: list[str] = []
    prompt_equals_form = f"--prompt={prompt}"
    for arg in argv:
        if arg == prompt:
            redacted.append(_REDACTED_PROMPT_ARG)
            continue
        if arg == prompt_equals_form:
            redacted.append(f"--prompt={_REDACTED_PROMPT_ARG}")
            continue
        redacted.append(arg)
    return redacted


class CLIBackedLLMClient:
    """Drives any `LLMCLIAdapter` with a single non-interactive subprocess call per invoke."""

    def __init__(
        self,
        adapter: LLMCLIAdapter,
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        model_type: str = "reasoning",
    ) -> None:
        self._adapter = adapter
        self._model = model
        self._max_tokens = max_tokens
        self._model_type = model_type
        self._cached_probe: CLIProbe | None = None
        self._probe_cached_at: float = 0.0
        self._probe_lock = threading.Lock()

    def _probe(self) -> CLIProbe:
        now = time.monotonic()
        if self._cached_probe is not None and (now - self._probe_cached_at) < _PROBE_CACHE_TTL_SEC:
            return self._cached_probe
        with self._probe_lock:
            locked_now = time.monotonic()
            if (
                self._cached_probe is not None
                and (locked_now - self._probe_cached_at) < _PROBE_CACHE_TTL_SEC
            ):
                return self._cached_probe
            probe = self._adapter.detect()
            self._cached_probe = probe
            self._probe_cached_at = locked_now
            return probe

    def with_config(self, **_kwargs: Any) -> CLIBackedLLMClient:
        return self

    def with_structured_output(self, model: type[BaseModel]) -> Any:
        """JSON-schema prompt + parse; same contract as API `StructuredOutputClient`."""
        from app.services.llm_client import StructuredOutputClient

        return StructuredOutputClient(self, model)

    def bind_tools(self, _tools: list[Any]) -> CLIBackedLLMClient:
        return self

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        # max_tokens / model_type are stored for API parity but ignored here:
        # CLI adapters (e.g. codex exec) do not expose a scriptable token limit.
        _ = self._max_tokens
        _ = self._model_type

        from app.guardrails.apply import apply_guardrails_to_text

        flat = flatten_messages_to_prompt(prompt_or_messages)
        flat = apply_guardrails_to_text(flat)

        probe = self._probe()
        if not probe.installed or not probe.bin_path:
            raise RuntimeError(
                f"{self._adapter.name} CLI not found. {self._adapter.install_hint} "
                f"or set {self._adapter.binary_env_key} to the full binary path. "
                f"({probe.detail})"
            )
        if probe.logged_in is False:
            raise CLIAuthenticationRequired(
                provider=self._adapter.name,
                auth_hint=self._adapter.auth_hint,
                detail=probe.detail,
            )
        auth_probe_unclear = probe.logged_in is None

        invocation = self._adapter.build(
            prompt=flat,
            model=self._model,
            workspace="",
            reasoning_effort=get_active_reasoning_effort(),
        )
        merged_env = _build_subprocess_env(invocation.env)
        logger.debug(
            "cli_llm_spawn",
            extra={
                "provider": self._adapter.name,
                "argv": _sanitize_argv_for_debug(invocation.argv, prompt=flat),
            },
        )

        backoff = _TEMPFAIL_BACKOFF_SEC
        for attempt in range(_TEMPFAIL_MAX_RETRIES + 1):
            try:
                proc = subprocess.run(
                    list(invocation.argv),
                    input=invocation.stdin,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=invocation.cwd,
                    env=merged_env,
                    timeout=invocation.timeout_sec,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise CLITimeoutError(
                    f"{self._adapter.name} CLI timed out after {invocation.timeout_sec:.0f}s."
                ) from exc
            except OSError as exc:
                raise RuntimeError(f"Failed to spawn {self._adapter.name} CLI: {exc}") from exc

            if proc.returncode == _EX_TEMPFAIL and attempt < _TEMPFAIL_MAX_RETRIES:
                logger.warning(
                    "cli_llm_tempfail_retry",
                    extra={
                        "provider": self._adapter.name,
                        "attempt": attempt + 1,
                        "backoff_sec": backoff,
                    },
                )
                time.sleep(backoff)
                backoff *= 2
                continue
            break

        out = _strip_ansi(proc.stdout or "")
        err = _strip_ansi(proc.stderr or "")

        if proc.returncode != 0:
            # Exit code 130 = subprocess terminated by SIGINT (Ctrl+C); raise
            # CLIInterruptedError so callers using `try/except Exception` still
            # observe the failure (KeyboardInterrupt inherits from BaseException
            # and would bypass those handlers). Sentry's `ignore_errors` config
            # filters this type so user-initiated cancellations are not reported
            # as bugs.
            if proc.returncode == 130:
                raise CLIInterruptedError(f"{self._adapter.name} CLI subprocess interrupted.")
            # Exit code 75 is EX_TEMPFAIL (sysexits.h) — a transient failure
            # the caller should retry. Raise CLITimeoutError so it is treated as
            # an expected operational failure and not forwarded to Sentry.
            if proc.returncode == _EX_TEMPFAIL:
                hint = (
                    f"{self._adapter.name} reported a temporary failure (exit 75). "
                    "Retry the request or check network connectivity."
                )
                if err:
                    hint = f"{hint} {err[:200]}"
                raise CLITimeoutError(hint)
            base = self._adapter.explain_failure(
                stdout=out, stderr=err, returncode=proc.returncode
            ).strip()
            # When the failure message signals an auth problem raise
            # CLIAuthenticationRequired so callers (reraise_cli_runtime_error,
            # server endpoints) get structured, actionable handling instead of
            # a bare RuntimeError that lands in Sentry as a spurious bug.
            # Patterns cover all current adapters:
            #   kimi        → "not logged in", "api key invalid", "re-authenticate"
            #   cursor      → "not logged in"
            #   opencode    → "authentication failed", "not authenticated"
            #   claude/gemini/codex pass raw stderr which may contain these phrases too
            _base_lower = base.lower()
            if (
                "not logged in" in _base_lower
                or "api key invalid" in _base_lower
                or "re-authenticate" in _base_lower
                or "authentication failed" in _base_lower
                or "not authenticated" in _base_lower
            ):
                raise CLIAuthenticationRequired(
                    provider=self._adapter.name,
                    auth_hint=self._adapter.auth_hint,
                    detail=base,
                )
            if auth_probe_unclear:
                message = (
                    f"{base}\n\n"
                    f"Auth status could not be verified before invocation. "
                    f"{self._adapter.auth_hint} ({probe.detail})"
                )
            else:
                message = base
            raise RuntimeError(message)

        content = self._adapter.parse(stdout=out, stderr=err, returncode=proc.returncode)
        content = _strip_ansi(content).strip()
        if err:
            logger.debug(
                "cli_llm_stderr",
                extra={"provider": self._adapter.name, "stderr": err[:500]},
            )
        logger.debug(
            "cli_llm_invoke",
            extra={"provider": self._adapter.name, "cli_cost_unknown": True},
        )
        return LLMResponse(content=content)

    def invoke_stream(self, prompt_or_messages: Any) -> Iterator[str]:
        """Yield the full response as one chunk; real streaming is a follow-up.

        Subprocess CLI adapters ``subprocess.run`` to completion, so this
        satisfies the protocol contract without faking progressive output.
        """
        yield self.invoke(prompt_or_messages).content
