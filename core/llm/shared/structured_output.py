"""Structured-output wrapper shared by hosted and CLI-backed LLM clients.

Default path: prompt-injected JSON Schema + Pydantic validate.

Provider-native constrained decoding (OpenAI ``parse`` / Anthropic
``output_config``) is **opt-in** via
``OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT=1``. It stays off until a live diagnose
turn has verified the request shape — a failing native call would otherwise
silently fall back and double-invoke on every turn.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from config.constants.llm import OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV
from core.llm.shared.json_extraction import extract_json_payload

logger = logging.getLogger(__name__)

#: ``finish_reason`` values meaning "the model ran out of output budget".
#: OpenAI and LiteLLM report ``length``; the Anthropic SDK reports ``max_tokens``.
_TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens"})


def native_structured_output_enabled() -> bool:
    """True when native structured outputs are explicitly opted in."""
    return os.environ.get(OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV, "").strip() == "1"


def json_schema_for_structured_output(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema suitable for provider structured-output APIs.

    Ensures every object sets ``additionalProperties: false`` (required by
    OpenAI strict mode and Anthropic structured outputs).
    """
    schema = model.model_json_schema()
    return cast(dict[str, Any], _force_additional_properties_false(schema))


def _force_additional_properties_false(node: Any) -> Any:
    if isinstance(node, dict):
        out = {key: _force_additional_properties_false(value) for key, value in node.items()}
        if out.get("type") == "object" or "properties" in out:
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_force_additional_properties_false(item) for item in node]
    return node


class StructuredOutputClient:
    """Wrap any LLM client with ``invoke`` for Pydantic JSON parsing."""

    def __init__(self, base: Any, model: type[BaseModel]) -> None:
        self._base = base
        self._model = model

    def with_config(self, **_kwargs: Any) -> StructuredOutputClient:
        return self

    def invoke(self, prompt: str) -> Any:
        if native_structured_output_enabled():
            native = getattr(self._base, "invoke_structured", None)
            if callable(native):
                started = time.perf_counter()
                try:
                    payload = native(self._model, prompt)
                    if isinstance(payload, BaseModel):
                        return payload
                    if isinstance(payload, dict):
                        return self._model.model_validate(payload)
                    if isinstance(payload, str):
                        return self._model.model_validate(extract_json_payload(payload))
                except Exception as err:
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    # Warning (not info): fallback means a second full LLM call.
                    logger.warning(
                        "Native structured output failed after %.0fms (%s: %s); "
                        "falling back to prompt-injected schema (second LLM invoke). "
                        "Unset %s to disable native attempts.",
                        elapsed_ms,
                        type(err).__name__,
                        err,
                        OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV,
                    )

        return self._invoke_prompt_schema(prompt)

    def _invoke_prompt_schema(self, prompt: str) -> Any:
        schema = json_schema_for_structured_output(self._model)
        schema_json = json.dumps(schema, indent=2)
        wrapped_prompt = (
            f"{prompt}\n\nReturn ONLY valid JSON that matches this schema:\n{schema_json}\n"
        )
        response = self._base.invoke(wrapped_prompt)
        finish_reason = getattr(response, "finish_reason", None)
        if finish_reason in _TRUNCATED_FINISH_REASONS:
            # The single most useful line when a structured stage degrades: it
            # separates "the output-token budget was too small for this schema"
            # from "the model wrote bad JSON", which need opposite fixes.
            logger.warning(
                "%s structured output stopped at the model's output limit "
                "(finish_reason=%s, %d chars) — the payload is incomplete because "
                "the request ran out of output budget, not because the model wrote "
                "bad JSON. Recovery keeps only the fields it finished writing.",
                self._model.__name__,
                finish_reason,
                len(response.content or ""),
            )
        payload = self._extract(response.content, finish_reason)
        try:
            return self._model.model_validate(payload)
        except ValidationError:
            if isinstance(payload, list) and "actions" in self._model.model_fields:
                fallback = {"actions": payload, "rationale": "LLM returned actions only."}
                return self._model.model_validate(fallback)
            raise

    def _extract(self, content: str, finish_reason: Any) -> Any:
        try:
            return extract_json_payload(content)
        except ValueError as err:
            raise ValueError(f"{err} [finish_reason={finish_reason!r}]") from err
