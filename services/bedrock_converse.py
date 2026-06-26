"""Shared helpers for the Amazon Bedrock Converse API (tool schemas and messages).

Used by the investigation agent's :class:`~services.agent_llm_client.BedrockConverseAgentClient`
and kept separate from :mod:`services.llm_client` so tool-schema normalization stays in one place.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any

logger = logging.getLogger(__name__)

# Keys that Converse toolSpec.inputSchema.json rejects or cannot resolve.
_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "title",
        "$schema",
        "$defs",
        "definitions",
        "$ref",
        "not",
        "nullable",  # OpenAPI nullable — Converse uses explicit types; anyOf/oneOf are flattened instead
        # Non-Anthropic Bedrock models (Mistral, Llama, etc.) reject additionalProperties even
        # when it carries a valid boolean value such as false.  Tool calling never relies on this
        # constraint, so stripping it is safe and avoids HTTP 400 ValidationException errors.
        "additionalProperties",
    }
)

# Injected when a tool exposes ``type: array`` without ``items``.
_DEFAULT_ARRAY_ITEMS: dict[str, str] = {"type": "string"}


def require_aws_region() -> str:
    """Return configured AWS region or raise with a clear configuration error."""
    region = (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip()
    if not region:
        raise RuntimeError("Bedrock requires AWS_REGION or AWS_DEFAULT_REGION to be set.")
    return region


def new_tool_use_id() -> str:
    """Return a short alphanumeric id suitable for Converse ``toolUseId`` fields."""
    return secrets.token_hex(5)


def _pick_non_null_schema_variant(variants: list[Any]) -> dict[str, Any] | None:
    """Return the first ``anyOf`` / ``oneOf`` branch with a concrete non-null type."""
    for item in variants:
        if not isinstance(item, dict):
            continue
        branch_type = item.get("type")
        if branch_type and branch_type != "null":
            return item
        # Accept an implicit object schema (properties present, no explicit type).
        if "properties" in item:
            return item
    return None


def _merge_all_of_subschemas(variants: list[Any]) -> dict[str, Any]:
    """Merge ``allOf`` branches (e.g. Pydantic constrained fields) into one schema dict."""
    merged: dict[str, Any] = {}
    for item in variants:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key == "properties" and isinstance(value, dict):
                props = merged.setdefault("properties", {})
                if isinstance(props, dict):
                    props.update(value)
                else:
                    merged["properties"] = dict(value)
            elif key == "required" and isinstance(value, list):
                required = merged.setdefault("required", [])
                if isinstance(required, list):
                    for name in value:
                        if name not in required:
                            required.append(name)
            elif key not in merged:
                merged[key] = value
    return merged


def _flatten_composite_keywords(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``allOf`` / ``anyOf`` / ``oneOf`` into explicit ``type`` fields."""
    flattened = dict(schema)
    if "allOf" in flattened:
        variants = flattened.pop("allOf")
        if isinstance(variants, list):
            for key, value in _merge_all_of_subschemas(variants).items():
                if key == "properties" and isinstance(value, dict):
                    existing = flattened.get("properties")
                    if isinstance(existing, dict):
                        existing.update(value)
                    else:
                        flattened["properties"] = dict(value)
                elif key not in flattened:
                    flattened[key] = value
    for composite in ("anyOf", "oneOf"):
        if composite not in flattened:
            continue
        variants = flattened.pop(composite)
        if not isinstance(variants, list):
            continue
        picked = _pick_non_null_schema_variant(variants)
        if picked:
            for key, value in picked.items():
                flattened.setdefault(key, value)
        elif "type" not in flattened:
            flattened["type"] = "string"
    return flattened


def sanitize_converse_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a Converse-compatible copy of *schema* with required ``type`` / ``items`` filled in."""
    cleaned: dict[str, Any] = {}
    for key, value in _flatten_composite_keywords(schema).items():
        if key in _UNSUPPORTED_SCHEMA_KEYS:
            continue
        if isinstance(value, dict):
            cleaned[key] = sanitize_converse_schema(value)
        elif isinstance(value, list):
            cleaned[key] = [
                sanitize_converse_schema(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            cleaned[key] = value

    _ensure_schema_node(cleaned)
    return cleaned


def _coerce_schema_type(node: dict[str, Any]) -> str | None:
    """Return a single Converse-compatible ``type`` string (Bedrock rejects ``type`` arrays)."""
    schema_type = node.get("type")
    if isinstance(schema_type, list):
        for candidate in schema_type:
            if isinstance(candidate, str) and candidate != "null":
                node["type"] = candidate
                return candidate
        node["type"] = "string"
        return "string"
    if isinstance(schema_type, str):
        return schema_type
    return None


def _ensure_schema_node(node: dict[str, Any]) -> None:
    """Mutate *node* so Bedrock's strict JSON Schema validation receives explicit types."""
    if "properties" in node and "type" not in node:
        node["type"] = "object"

    schema_type = _coerce_schema_type(node)
    if schema_type == "object" and "properties" not in node:
        node["properties"] = {}

    if schema_type == "array":
        items = node.get("items")
        if items is None:
            node["items"] = dict(_DEFAULT_ARRAY_ITEMS)
        elif isinstance(items, dict):
            if "type" not in items and "properties" not in items:
                node["items"] = dict(_DEFAULT_ARRAY_ITEMS)
            else:
                _ensure_schema_node(items)
        return

    items = node.get("items")
    if isinstance(items, dict):
        _ensure_schema_node(items)


def normalize_tool_input_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a tool's public input schema for ``toolSpec.inputSchema.json``.

    Converse tool inputs must be JSON objects at the top level. Non-object roots are
    replaced with an empty object schema so validation stays strict but safe.
    """
    cleaned = sanitize_converse_schema(dict(schema or {}))
    if cleaned.get("type") != "object":
        return {"type": "object", "properties": {}}
    if "properties" not in cleaned:
        cleaned["properties"] = {}
    return cleaned


def build_converse_tool_specs(tools: list[Any]) -> list[dict[str, Any]]:
    """Build ``toolConfig.tools`` entries from registered tool objects."""
    specs: list[dict[str, Any]] = []
    for tool in tools:
        specs.append(
            {
                "toolSpec": {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": {"json": normalize_tool_input_schema(tool.public_input_schema)},
                }
            }
        )
    return specs


def to_converse_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert investigation messages to Converse ``messages`` shape."""
    converted: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            converted.append({"role": message["role"], "content": [{"text": content}]})
        else:
            converted.append(message)
    return converted


def build_assistant_tool_use_message(tool_calls: list[Any]) -> dict[str, Any]:
    """Build a Converse assistant message containing ``toolUse`` blocks."""
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                }
            }
            for tc in tool_calls
        ],
    }


def build_tool_result_message(tool_calls: list[Any], results: list[Any]) -> dict[str, Any]:
    """Build the Converse ``toolResult`` user message for one round of tool calls."""
    content: list[dict[str, Any]] = []
    for tc, result in zip(tool_calls, results, strict=True):
        is_error = isinstance(result, dict) and bool(result.get("error"))
        if isinstance(result, dict):
            sanitized = json.loads(json.dumps(result, default=str))
            result_content: list[dict[str, Any]] = [{"json": sanitized}]
        else:
            result_content = [{"text": json.dumps(result, default=str)}]
        tool_result: dict[str, Any] = {
            "toolUseId": tc.id,
            "content": result_content,
        }
        if is_error:
            tool_result["status"] = "error"
        content.append({"toolResult": tool_result})
    return {"role": "user", "content": content}


def parse_converse_output(
    response: dict[str, Any],
) -> tuple[str, list[tuple[str, str, dict[str, Any]]], str, dict[str, Any]]:
    """Parse a Converse API response into text, tool calls, stop reason, and raw message."""
    output_message = response.get("output", {}).get("message", {})
    if not isinstance(output_message, dict):
        output_message = {"role": "assistant", "content": []}

    text_parts: list[str] = []
    tool_calls: list[tuple[str, str, dict[str, Any]]] = []
    for block in output_message.get("content", []):
        if not isinstance(block, dict):
            continue
        if "text" in block:
            text_parts.append(str(block["text"]))
            continue
        tool_use = block.get("toolUse")
        if not isinstance(tool_use, dict):
            continue
        raw_input = tool_use.get("input")
        tool_calls.append(
            (
                str(tool_use["toolUseId"]),
                str(tool_use["name"]),
                raw_input if isinstance(raw_input, dict) else {},
            )
        )

    stop_reason = str(response.get("stopReason", "end_turn"))
    return "".join(text_parts), tool_calls, stop_reason, output_message


def map_bedrock_client_error(model: str, err: Any) -> RuntimeError:
    """Map a ``botocore`` ``ClientError`` to a user-facing ``RuntimeError``."""
    code = err.response.get("Error", {}).get("Code", "")
    message = err.response.get("Error", {}).get("Message", "") or str(err)

    if code == "ValidationException":
        return RuntimeError(f"Bedrock request rejected (HTTP 400): {message}")
    if code == "ResourceNotFoundException":
        return RuntimeError(
            f"Bedrock model '{model}' was not found in the configured region. "
            "Check the model ID, region, or inference profile."
        )
    if code == "ThrottlingException":
        return RuntimeError(
            f"Bedrock rate limit exceeded for model '{model}'. "
            "Reduce request frequency or request a quota increase."
        )
    if code in ("AccessDeniedException", "UnauthorizedException"):
        err_msg_str = str(message)
        if (
            "INVALID_PAYMENT_INSTRUMENT" in err_msg_str
            or "payment instrument" in err_msg_str.lower()
        ):
            aws_message = err_msg_str.strip().rstrip(".")
            detail = f" Cause: {aws_message}." if aws_message else ""
            return RuntimeError(
                f"Access denied for Bedrock model '{model}'.{detail} "
                "A valid AWS payment instrument is required."
            )
        aws_message = err_msg_str.strip().rstrip(".")
        detail = f" Cause: {aws_message}." if aws_message else ""
        return RuntimeError(
            f"Access denied for Bedrock model '{model}'.{detail} "
            "Check Bedrock model access (per-region opt-in), your "
            "AWS Marketplace subscription / payment method, and "
            "IAM permissions."
        )
    return RuntimeError(f"Bedrock API request failed: {message}")


def is_non_retryable_bedrock_code(code: str) -> bool:
    """Return True when retrying the same request will not help."""
    return code in (
        "ValidationException",
        "ResourceNotFoundException",
        "AccessDeniedException",
        "UnauthorizedException",
    )
