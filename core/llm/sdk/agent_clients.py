"""SDK-backed tool-calling LLM clients for the investigation agent ReAct loop.

Supports Anthropic (native + Bedrock), OpenAI-compatible, and subprocess CLI providers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable
from typing import Any

from core.llm.llm_retry import (
    maybe_raise_credit_exhausted,
    rate_limit_sleep_seconds,
)
from core.llm.openai_chat_completions import (
    _RETRY_INITIAL_BACKOFF_SEC,
    _RETRY_MAX_ATTEMPTS,
    AGENT_CLIENT_TIMEOUT_SEC,
)
from core.llm.openai_chat_completions import (
    build_assistant_message as build_openai_compat_assistant_message,
)
from core.llm.openai_chat_completions import (
    build_tool_result_messages as build_openai_compat_tool_result_messages,
)
from core.llm.tool_schema_normalize import build_openai_tool_specs
from core.llm.types import AgentLLMResponse, ToolCall
from core.llm.usage import emit_provider_usage

logger = logging.getLogger(__name__)


def _anthropic_tool_schema(tool: Any) -> dict[str, Any]:
    return {
        "type": "custom",
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.public_input_schema,
    }


class AnthropicAgentClient:
    """Anthropic client with native tool-calling for the agent loop."""

    provider_name = "Anthropic"
    auth_error_hint = "Check ANTHROPIC_API_KEY."

    def __init__(
        self,
        model: str,
        max_tokens: int = 4096,
        *,
        client: Any | None = None,
        credential_resolver: Callable[[str], str] | None = None,
    ) -> None:
        if client is None:
            from anthropic import Anthropic

            from config.llm_credentials import resolve_llm_api_key

            resolver = credential_resolver or resolve_llm_api_key
            api_key = resolver("ANTHROPIC_API_KEY")
            self._client = Anthropic(api_key=api_key, timeout=AGENT_CLIENT_TIMEOUT_SEC)
        else:
            self._client = client
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model_id(self) -> str | None:
        return self._model

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [_anthropic_tool_schema(t) for t in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        from anthropic import (
            AuthenticationError,
            BadRequestError,
            InternalServerError,
            NotFoundError,
            PermissionDeniedError,
            RateLimitError,
        )

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        backoff = _RETRY_INITIAL_BACKOFF_SEC
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                response = self._client.messages.create(**kwargs)
                break
            except AuthenticationError as err:
                raise RuntimeError(self._authentication_error_message()) from err
            except NotFoundError as err:
                raise RuntimeError(self._model_not_found_error_message()) from err
            except PermissionDeniedError as err:
                raise RuntimeError(self._permission_denied_error_message()) from err
            except BadRequestError as err:
                # Anthropic surfaces "credit balance too low" as HTTP 400;
                # distinguish credit exhaustion (fatal) from real schema
                # errors before wrapping into a generic RuntimeError.
                maybe_raise_credit_exhausted(self.provider_name, err)
                raise RuntimeError(self._bad_request_error_message(err)) from err
            except RateLimitError as err:
                # OpenAI's insufficient_quota lands here too, dressed as 429
                # with "rate limit" text. Distinguish billing exhaustion
                # (fatal — no retry will help) from transient TPM throttling.
                maybe_raise_credit_exhausted(self.provider_name, err)
                # Transient by definition — back off and retry instead of
                # failing the whole case. Honor ``Retry-After`` when the
                # provider sends one (much tighter than our jittered
                # default, which can be 10x longer than necessary).
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"{self.provider_name} rate limit exceeded after "
                        f"{_RETRY_MAX_ATTEMPTS} attempts: {err}"
                    ) from err
                time.sleep(rate_limit_sleep_seconds(err, backoff))
                backoff *= 2
            except InternalServerError as err:
                body = getattr(err, "body", {}) or {}
                if (
                    isinstance(body, dict)
                    and isinstance(body.get("data"), dict)
                    and "model" in body["data"]
                ):
                    raise RuntimeError(
                        f"{self.provider_name} model '{self._model}' is not configured or billing is not enabled: "
                        f"{body.get('message', str(err))}"
                    ) from err
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"{self.provider_name} API failed after {_RETRY_MAX_ATTEMPTS} attempts: {err}"
                    ) from err
                time.sleep(backoff)
                backoff *= 2
            except TypeError as err:
                # Anthropic SDK raises TypeError from _validate_headers when the API key is
                # missing or malformed — retrying won't fix a credential problem.
                if "could not resolve authentication" in str(err).lower():
                    raise RuntimeError(self._authentication_error_message()) from err
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"{self.provider_name} API failed after {_RETRY_MAX_ATTEMPTS} attempts: {err}"
                    ) from err
                time.sleep(backoff)
                backoff *= 2
            except Exception as err:
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"{self.provider_name} API failed after {_RETRY_MAX_ATTEMPTS} attempts: {err}"
                    ) from err
                time.sleep(backoff)
                backoff *= 2
        else:
            raise RuntimeError(f"{self.provider_name} invocation failed") from last_err

        content_blocks = getattr(response, "content", None)
        if not isinstance(content_blocks, list):
            logger.warning(
                "AnthropicAgentClient.invoke: unexpected response type %s — expected Message with .content list",
                type(response).__name__,
            )
            raise RuntimeError(
                f"{self.provider_name} API returned an unexpected response: {type(response).__name__}"
            )

        emit_provider_usage(
            self._model,
            getattr(response, "usage", None),
            input_key="input_tokens",
            output_key="output_tokens",
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in content_blocks:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)
            elif block_type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input)))

        return AgentLLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=str(getattr(response, "stop_reason", "end_turn")),
            raw_content=content_blocks,
        )

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        """Build the Anthropic tool_result user message for one round of tool calls."""
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(result, default=str),
                }
                for tc, result in zip(tool_calls, results)
            ],
        }

    @staticmethod
    def build_assistant_message(raw_content: Any) -> dict[str, Any]:
        """Build the assistant message preserving full Anthropic content blocks."""
        return {"role": "assistant", "content": raw_content}

    def _authentication_error_message(self) -> str:
        return f"{self.provider_name} authentication failed. {self.auth_error_hint}"

    def _model_not_found_error_message(self) -> str:
        return f"{self.provider_name} model '{self._model}' not found."

    def _permission_denied_error_message(self) -> str:
        return f"{self.provider_name} API access denied. Check your API key permissions."

    def _bad_request_error_message(self, err: Any) -> str:
        return f"{self.provider_name} request rejected (HTTP 400): {err.message}"


class BedrockAgentClient(AnthropicAgentClient):
    """Bedrock-backed client using AnthropicBedrock SDK."""

    provider_name = "Bedrock"
    auth_error_hint = (
        "Check AWS credentials (for example AWS_PROFILE, AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, "
        "or instance role) and AWS_REGION/AWS_DEFAULT_REGION."
    )

    def __init__(self, model: str, max_tokens: int = 4096) -> None:
        from anthropic import AnthropicBedrock

        region = (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip()
        if not region:
            raise RuntimeError("Bedrock requires AWS_REGION or AWS_DEFAULT_REGION to be set.")

        bedrock_client = AnthropicBedrock(
            aws_region=region,
            timeout=AGENT_CLIENT_TIMEOUT_SEC,
        )
        super().__init__(model=model, max_tokens=max_tokens, client=bedrock_client)

    def _permission_denied_error_message(self) -> str:
        return (
            f"Bedrock model '{self._model}' is not available for your account. "
            "Check Bedrock model access in the configured AWS region, AWS Marketplace "
            "subscription/payment setup, and IAM permissions including "
            "aws-marketplace:ViewSubscriptions and aws-marketplace:Subscribe."
        )

    def _bad_request_error_message(self, err: Any) -> str:
        err_str = str(err)
        if "on-demand throughput" in err_str or "inference profile" in err_str.lower():
            return (
                f"Bedrock model '{self._model}' requires a cross-region inference profile. "
                f"Try prefixing with 'us.' (e.g. 'us.{self._model}') and update "
                "BEDROCK_REASONING_MODEL or BEDROCK_TOOLCALL_MODEL."
            )
        return f"{self.provider_name} request rejected (HTTP 400): {err.message}"


class BedrockConverseAgentClient:
    """Bedrock investigation client using the boto3 Converse API (non-Anthropic models)."""

    provider_name = "Bedrock"

    def __init__(self, model: str, max_tokens: int = 4096) -> None:
        import boto3

        from core.llm.sdk.bedrock_converse import require_aws_region

        self._model = model
        self._max_tokens = max_tokens
        region = require_aws_region()
        self._boto3_client = boto3.client("bedrock-runtime", region_name=region)

    @property
    def model_id(self) -> str | None:
        return self._model

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        from core.llm.sdk.bedrock_converse import build_converse_tool_specs

        return build_converse_tool_specs(tools)

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        import botocore.exceptions

        from core.llm.sdk.bedrock_converse import (
            is_non_retryable_bedrock_code,
            map_bedrock_client_error,
            parse_converse_output,
            to_converse_messages,
        )
        from platform.guardrails.apply import apply_guardrails_to_converse_payload
        from platform.guardrails.engine import GuardrailBlockedError

        converse_messages = to_converse_messages(messages)
        converse_messages, system = apply_guardrails_to_converse_payload(
            messages=converse_messages,
            system=system,
        )

        kwargs: dict[str, Any] = {
            "modelId": self._model,
            "inferenceConfig": {"maxTokens": self._max_tokens},
            "messages": converse_messages,
        }
        if system:
            kwargs["system"] = [{"text": system}]
        if tools:
            kwargs["toolConfig"] = {"tools": tools}

        backoff = _RETRY_INITIAL_BACKOFF_SEC
        response: dict[str, Any] | None = None
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                response = self._boto3_client.converse(**kwargs)
                break
            except GuardrailBlockedError:
                raise
            except botocore.exceptions.ClientError as err:
                code = err.response.get("Error", {}).get("Code", "")
                if is_non_retryable_bedrock_code(code):
                    raise map_bedrock_client_error(self._model, err) from err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise map_bedrock_client_error(self._model, err) from err
                last_err = err
                time.sleep(backoff)
                backoff *= 2
            except Exception as err:
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(f"Bedrock API request failed: {err}") from err
                last_err = err
                time.sleep(backoff)
                backoff *= 2
        else:
            raise RuntimeError("Bedrock invocation failed without a concrete error") from last_err

        if response is None:
            raise RuntimeError("Bedrock invocation failed without a response") from last_err

        emit_provider_usage(
            self._model,
            response.get("usage"),
            input_key="inputTokens",
            output_key="outputTokens",
        )

        content, raw_tool_calls, stop_reason, raw_message = parse_converse_output(response)
        tool_calls = [
            ToolCall(id=tool_id, name=name, input=inputs)
            for tool_id, name, inputs in raw_tool_calls
        ]
        return AgentLLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw_content=raw_message,
        )

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        from core.llm.sdk.bedrock_converse import build_tool_result_message as _build

        return _build(tool_calls, results)

    @staticmethod
    def build_assistant_message(raw_content: Any) -> dict[str, Any]:
        """Preserve the full Converse assistant ``output.message`` for the next turn."""
        if not isinstance(raw_content, dict):
            return {"role": "assistant", "content": []}
        return raw_content


_OPENAI_O_SERIES_RE = re.compile(r"(?:^|[^A-Za-z0-9])o\d", re.IGNORECASE)
_OPENAI_GPT5_RE = re.compile(r"(?:^|[^A-Za-z0-9])gpt-5", re.IGNORECASE)


def _supports_openai_parallel_tool_calls_param(api_key_env: str) -> bool:
    """Return whether this client targets OpenAI's native chat-completions API."""
    return api_key_env == "OPENAI_API_KEY"


def _openai_max_token_kwarg(model: str) -> str:
    # OpenAI o-series (o1, o3, o4-mini, …) and gpt-5 series reject max_tokens.
    # O-series: matches a bare ``o<digit>`` token at the start of the name or
    # following a non-alphanumeric separator, so vendor-prefixed routes
    # (``openai/o4-mini``, ``azure/o3``) and custom deployments are detected.
    # gpt-5: matches ``gpt-5`` at the start or after a separator, covering
    # gpt-5, gpt-5o, gpt-5o-mini, and future gpt-5* variants.
    if _OPENAI_O_SERIES_RE.search(model) or _OPENAI_GPT5_RE.search(model):
        return "max_completion_tokens"
    return "max_tokens"


class OpenAIAgentClient:
    """OpenAI-compatible client with tool-calling for the agent loop."""

    def __init__(
        self,
        model: str,
        max_tokens: int = 4096,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        api_key_default: str = "",
        credential_resolver: Callable[[str], str] | None = None,
    ) -> None:
        from openai import OpenAI

        from config.llm_credentials import resolve_llm_api_key

        resolver = credential_resolver or resolve_llm_api_key
        api_key = resolver(api_key_env) or api_key_default
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=AGENT_CLIENT_TIMEOUT_SEC)
        self._model = model
        self._max_tokens = max_tokens
        self._api_key_env = api_key_env

    @property
    def model_id(self) -> str | None:
        return self._model

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return build_openai_tool_specs(tools)

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        from openai import (
            AuthenticationError,
            BadRequestError,
            NotFoundError,
            PermissionDeniedError,
            RateLimitError,
        )

        msgs = list(messages)
        if system:
            msgs = [{"role": "system", "content": system}] + msgs

        kwargs: dict[str, Any] = {
            "model": self._model,
            _openai_max_token_kwarg(self._model): self._max_tokens,
            "messages": msgs,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
            if _supports_openai_parallel_tool_calls_param(
                str(getattr(self, "_api_key_env", "OPENAI_API_KEY"))
            ):
                kwargs["parallel_tool_calls"] = True

        backoff = _RETRY_INITIAL_BACKOFF_SEC
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                response = self._client.chat.completions.create(**kwargs)
                break
            except AuthenticationError as err:
                raise RuntimeError("OpenAI authentication failed.") from err
            except NotFoundError as err:
                raise RuntimeError(f"OpenAI model '{self._model}' not found.") from err
            except BadRequestError as err:
                # Some providers (or proxies) surface insufficient_quota as
                # 400 — distinguish before the generic wrap.
                maybe_raise_credit_exhausted("OpenAI", err)
                raise RuntimeError(f"OpenAI request rejected: {err}") from err
            except RateLimitError as err:
                # OpenAI returns insufficient_quota as HTTP 429 with body
                # text "You exceeded your current quota". Halt rather than
                # burning retries on a dead account.
                maybe_raise_credit_exhausted("OpenAI", err)
                # Transient by definition — back off and retry instead of
                # failing the whole cell. OpenAI's body usually carries a
                # tight hint like ``"Please try again in 94ms"``; honor it
                # when present instead of waiting our deterministic backoff
                # (matters most on tight tiers like gpt-4o's 30k TPM).
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"OpenAI rate limit exceeded after {_RETRY_MAX_ATTEMPTS} attempts: {err}"
                    ) from err
                time.sleep(rate_limit_sleep_seconds(err, backoff))
                backoff *= 2
            except PermissionDeniedError as err:
                raise RuntimeError(f"OpenAI request forbidden: {err}") from err
            except Exception as err:
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(f"OpenAI API failed: {err}") from err
                time.sleep(backoff)
                backoff *= 2
        else:
            raise RuntimeError("OpenAI invocation failed") from last_err

        if not hasattr(response, "choices") or not response.choices:
            raise RuntimeError(
                f"OpenAI API returned an unexpected response: {type(response).__name__}"
            )
        emit_provider_usage(
            self._model,
            getattr(response, "usage", None),
            input_key="prompt_tokens",
            output_key="completion_tokens",
        )
        choice = response.choices[0]
        msg = choice.message
        content = msg.content or ""
        stop_reason = choice.finish_reason or "stop"

        tool_calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            try:
                input_dict = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                input_dict = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=input_dict))

        return AgentLLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            # Preserve the raw API message so provider-specific fields (e.g. Gemini
            # thought_signature in tool_calls) survive into the next conversation turn.
            # exclude_none=True strips null fields (refusal, audio, function_call …)
            # that strict OpenAI-compatible endpoints may reject on replay.
            raw_content=msg.model_dump(exclude_none=True),
        )

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        raise NotImplementedError("OpenAI tool results must be appended as separate messages")

    build_tool_result_messages = staticmethod(build_openai_compat_tool_result_messages)
    build_assistant_message = staticmethod(build_openai_compat_assistant_message)


class CLIBackedAgentClient:
    """Tool-calling wrapper for subprocess CLI providers (codex, claude-code, etc.).

    CLI adapters don't expose a native tool-calling API. This client implements
    the investigation agent's ReAct interface by embedding tool schemas in the
    prompt as JSON and parsing the model's text response for tool call JSON.
    Each invoke flattens the full conversation history into a single stdin prompt.
    """

    _TOOL_CALL_INSTRUCTION = (
        "You are an SRE investigation agent. Use the available tools to investigate "
        "the alert. On each turn respond with EITHER:\n"
        '  (a) A JSON object: {"tool_calls": [{"id": "<unique_id>", "name": "<tool>",'
        ' "input": {<args>}}]}\n'
        "  (b) A plain-text final answer when investigation is complete.\n"
        "Respond with JSON only when calling tools; respond with plain text only for the final answer."
    )

    def __init__(self, adapter: Any, *, model: str | None = None) -> None:
        from integrations.llm_cli.runner import CLIBackedLLMClient

        self._adapter = adapter
        self._model = model
        # Reuse one subprocess client so the 45s probe cache in CLIBackedLLMClient
        # applies across ReAct iterations instead of re-probing every invoke.
        self._cli_client = CLIBackedLLMClient(adapter, model=self._model)

    @property
    def model_id(self) -> str | None:
        return self._model

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        # Return the same dicts — used only to pass back into invoke() below.
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        from integrations.llm_cli.text import flatten_messages_to_prompt

        tool_block = ""
        if tools:
            tool_lines = json.dumps(tools, indent=2)
            tool_block = f"\n\nAvailable tools (JSON schema):\n{tool_lines}\n"

        system_block = f"System: {system}\n" if system else ""
        instruction = self._TOOL_CALL_INSTRUCTION + tool_block
        prompt = f"{system_block}{instruction}\n\n{flatten_messages_to_prompt(messages)}"

        response = self._cli_client.invoke(prompt)
        text = response.content.strip()

        # Try to parse a JSON tool call response.
        tool_calls: list[ToolCall] = []
        parsed_json = _try_parse_tool_call_json(text)
        if parsed_json is not None:
            raw_calls = parsed_json.get("tool_calls")
            if isinstance(raw_calls, list):
                for i, tc in enumerate(raw_calls):
                    if not isinstance(tc, dict):
                        continue
                    name = tc.get("name")
                    if not isinstance(name, str) or not name.strip():
                        continue
                    raw_input = tc.get("input")
                    input_payload = raw_input if isinstance(raw_input, dict) else {}
                    tool_calls.append(
                        ToolCall(
                            id=str(tc.get("id") or f"call_{i}"),
                            name=name.strip(),
                            input=input_payload,
                        )
                    )
            content = "" if tool_calls else text
        else:
            content = text

        return AgentLLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            raw_content=None,  # None so _build_assistant_msg falls through to build_assistant_message
        )

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        parts = [
            f"Tool result for {tc.name} (id={tc.id}): {json.dumps(result, default=str)}"
            for tc, result in zip(tool_calls, results)
        ]
        return {"role": "user", "content": "\n".join(parts)}

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        """Match OpenAIAgentClient signature; embed tool JSON in content for CLI history."""
        if tool_calls:
            payload = {
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "input": tc.input} for tc in tool_calls
                ]
            }
            tool_json = json.dumps(payload)
            if content.strip():
                return {"role": "assistant", "content": f"{content.strip()}\n\n{tool_json}"}
            return {"role": "assistant", "content": tool_json}
        return {"role": "assistant", "content": content}


def _try_parse_tool_call_json(text: str) -> dict[str, Any] | None:
    """Return parsed JSON dict if *text* contains a tool_calls JSON object.

    Uses :meth:`json.JSONDecoder.raw_decode` so a single JSON value is parsed
    without a greedy ``{...}`` span swallowing trailing brace-containing prose and
    breaking :func:`json.loads`.
    """
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    candidate = (fence.group(1).strip() if fence else cleaned).strip()
    if not candidate:
        return None

    decoder = json.JSONDecoder()

    def _decode_at(idx: int) -> dict[str, Any] | None:
        try:
            payload, _end = decoder.raw_decode(candidate, idx)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict) and "tool_calls" in payload:
            return payload
        return None

    # Fast path: candidate is pure JSON (the preferred model behavior).
    parsed = _decode_at(0)
    if parsed is not None:
        return parsed

    # Recovery path: some CLI models prepend unfenced prose before the JSON
    # object. Scan object starts and accept the first dict that contains
    # "tool_calls". Skip index 0 because the fast path already attempted it.
    for match in re.finditer(r"\{", candidate):
        if match.start() == 0:
            continue
        parsed = _decode_at(match.start())
        if parsed is not None:
            return parsed

    return None
