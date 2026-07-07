"""Structured-output wrapper shared by hosted and CLI-backed LLM clients."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class StructuredOutputClient:
    """Wrap any LLM client with ``invoke`` for Pydantic JSON parsing."""

    def __init__(self, base: Any, model: type[BaseModel]) -> None:
        self._base = base
        self._model = model

    def with_config(self, **_kwargs: Any) -> StructuredOutputClient:
        return self

    def invoke(self, prompt: str) -> Any:
        schema = self._model.model_json_schema()
        schema_json = json.dumps(schema, indent=2)
        wrapped_prompt = (
            f"{prompt}\n\nReturn ONLY valid JSON that matches this schema:\n{schema_json}\n"
        )
        response = self._base.invoke(wrapped_prompt)
        payload = extract_json_payload(response.content)
        try:
            return self._model.model_validate(payload)
        except ValidationError:
            if isinstance(payload, list) and "actions" in self._model.model_fields:
                fallback = {"actions": payload, "rationale": "LLM returned actions only."}
                return self._model.model_validate(fallback)
            raise


def safe_json_loads(payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return json.loads(payload, strict=False)


def extract_json_payload(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()
    else:
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if fence_match:
            candidate = fence_match.group(1).strip()
            try:
                return safe_json_loads(candidate)
            except json.JSONDecodeError:
                pass

    try:
        return safe_json_loads(cleaned)
    except json.JSONDecodeError:
        logger.debug("Direct JSON parse failed, trying regex extraction")

    obj_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if obj_match:
        try:
            return safe_json_loads(obj_match.group(0))
        except json.JSONDecodeError:
            logger.debug("Object regex JSON parse failed, trying array extraction")

    list_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if list_match:
        try:
            return safe_json_loads(list_match.group(0))
        except json.JSONDecodeError:
            logger.debug("Array regex JSON parse also failed")

    raise ValueError("LLM did not return valid JSON payload")
