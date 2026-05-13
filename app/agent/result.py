"""Structured investigation result — parsed from the agent's final LLM response."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class _DiagnosisSchema(BaseModel):
    root_cause: str = Field(description="Concise root cause statement (2-3 sentences max)")
    root_cause_category: str = Field(
        description=(
            "One of: database, infrastructure, code_bug, configuration, "
            "network, performance, healthy, unknown"
        )
    )
    causal_chain: list[str] = Field(
        default_factory=list, description="Ordered steps leading to the failure"
    )
    validated_claims: list[str] = Field(
        default_factory=list, description="Claims supported by tool evidence"
    )
    non_validated_claims: list[str] = Field(
        default_factory=list, description="Claims not yet confirmed by evidence"
    )
    remediation_steps: list[str] = Field(
        default_factory=list, description="Concrete remediation actions in order"
    )
    validity_score: float = Field(default=0.0, description="0.0–1.0 confidence in the diagnosis")


@dataclass
class InvestigationResult:
    root_cause: str
    root_cause_category: str
    causal_chain: list[str] = field(default_factory=list)
    validated_claims: list[dict] = field(default_factory=list)
    non_validated_claims: list[dict] = field(default_factory=list)
    remediation_steps: list[str] = field(default_factory=list)
    validity_score: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    evidence_entries: list[dict] = field(default_factory=list)
    agent_messages: list[dict] = field(default_factory=list)
    investigation_recommendations: list[str] = field(default_factory=list)

    @classmethod
    def unknown(cls, alert_name: str = "Unknown alert") -> InvestigationResult:
        return cls(
            root_cause=f"{alert_name}: Unable to determine root cause — insufficient evidence.",
            root_cause_category="unknown",
            validity_score=0.0,
            non_validated_claims=[
                {
                    "claim": "Insufficient evidence available",
                    "validation_status": "not_validated",
                }
            ],
        )

    @classmethod
    def noise(cls) -> InvestigationResult:
        return cls(
            root_cause="Message classified as noise — no investigation needed.",
            root_cause_category="healthy",
            validity_score=1.0,
        )


def parse_diagnosis(
    messages: list[dict[str, Any]],
    evidence: dict[str, Any],
    alert_name: str = "",
) -> InvestigationResult:
    """Parse the agent's final response into a structured InvestigationResult.

    Uses structured output to extract root_cause, claims, remediation, etc.
    Falls back to parse_root_cause() if structured output fails.
    """
    last_text = _extract_last_assistant_text(messages)
    if not last_text:
        return InvestigationResult.unknown(alert_name)

    try:
        return _parse_via_structured_output(last_text, evidence)
    except Exception as err:
        logger.warning("Structured diagnosis parse failed, falling back: %s", err)
        return _parse_via_legacy(last_text, evidence, alert_name)


def _extract_last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                    continue
                if isinstance(block, dict):
                    if block.get("type") == "text" and isinstance(block.get("text"), str):
                        parts.append(block["text"])
                    continue
                block_type = getattr(block, "type", None)
                block_text = getattr(block, "text", None)
                if block_type == "text" and isinstance(block_text, str):
                    parts.append(block_text)
            text = " ".join(p for p in parts if p).strip()
            if text:
                return text
    return ""


def _parse_via_structured_output(last_text: str, evidence: dict[str, Any]) -> InvestigationResult:
    from app.services import get_llm_for_reasoning

    prompt = f"""Extract the structured diagnosis from this investigation conclusion.

Investigation conclusion:
{last_text}

Evidence keys collected: {", ".join(evidence.keys()) if evidence else "none"}
"""
    llm = get_llm_for_reasoning()
    import typing

    schema = typing.cast(
        _DiagnosisSchema,
        llm.with_structured_output(_DiagnosisSchema)
        .with_config(run_name="LLM – Parse diagnosis")
        .invoke(prompt),
    )

    def _to_claim_dicts(claims: list[str], status: str) -> list[dict]:
        return [{"claim": c, "validation_status": status} for c in claims if c]

    return InvestigationResult(
        root_cause=schema.root_cause,
        root_cause_category=schema.root_cause_category,
        causal_chain=schema.causal_chain,
        validated_claims=_to_claim_dicts(schema.validated_claims, "validated"),
        non_validated_claims=_to_claim_dicts(schema.non_validated_claims, "not_validated"),
        remediation_steps=schema.remediation_steps,
        validity_score=schema.validity_score,
    )


def _parse_via_legacy(
    last_text: str, _evidence: dict[str, Any], alert_name: str
) -> InvestigationResult:
    from app.services import parse_root_cause

    try:
        rr = parse_root_cause(last_text)
        return InvestigationResult(
            root_cause=rr.root_cause,
            root_cause_category=rr.root_cause_category,
            causal_chain=rr.causal_chain,
            validated_claims=[
                {"claim": c, "validation_status": "validated"} for c in rr.validated_claims
            ],
            non_validated_claims=[
                {"claim": c, "validation_status": "not_validated"} for c in rr.non_validated_claims
            ],
            remediation_steps=rr.remediation_steps,
            validity_score=0.5,
        )
    except Exception as err:
        logger.warning("Legacy parse_root_cause also failed: %s", err)
        return InvestigationResult.unknown(alert_name)
