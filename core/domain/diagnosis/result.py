"""Diagnosis result model and pure parse helpers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from core.domain.diagnosis.alignment import apply_category_alignment_adjustments
from core.domain.types.root_cause_categories import (
    HERMES_ROOT_CAUSE_CATEGORIES,
    VALID_ROOT_CAUSE_CATEGORIES,
    render_prompt_taxonomy,
)

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\s\-/]+")

# Hand-curated adjacent labels emitted by older prompts or parsers. Targets are
# still gated by the caller's allowed taxonomy, so Hermes-only prompts cannot
# normalize onto product-infra categories.
_CATEGORY_ALIASES: dict[str, str] = {
    "code_bug": "code_defect_null_handling",
    "config_error": "configuration_error",
    "configuration": "configuration_error",
    "connection_pool_exhaustion": "connection_exhaustion",
    "cpu_throttling": "pod_cpu_throttled",
    "database": "connection_exhaustion",
    "database_connection_failure": "connection_exhaustion",
    "dns_failure": "dns_resolution_failure",
    "infrastructure": "configuration_error",
    "memory_pressure": "pod_oomkilled",
    "mysql_connection_pool_exhaustion": "connection_pool_leak",
    "network_delay": "network_partition",
    "network_latency_issue": "network_partition",
    "oom_killed": "pod_oomkilled",
    "oomkilled": "pod_oomkilled",
    "performance": "application_tier_load_spike",
    "pod_cpu_overload": "pod_cpu_throttled",
    "pod_oom_killed": "pod_oomkilled",
    "redis_connection_pool_exhaustion": "connection_pool_leak",
}


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
    category_text_mismatch: bool = False
    category_text_mismatch_reason: str | None = None

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


def result_to_state(result: InvestigationResult) -> dict[str, Any]:
    return {
        "root_cause": result.root_cause,
        "root_cause_category": result.root_cause_category,
        "causal_chain": result.causal_chain,
        "validated_claims": result.validated_claims,
        "non_validated_claims": result.non_validated_claims,
        "remediation_steps": result.remediation_steps,
        "validity_score": result.validity_score,
        "investigation_recommendations": result.investigation_recommendations,
        "evidence": result.evidence,
        "evidence_entries": result.evidence_entries,
        "agent_messages": result.agent_messages,
    }


def extract_last_assistant_text(messages: list[dict[str, Any]]) -> str:
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


def taxonomy_categories_for_alert_source(alert_source: str) -> set[str]:
    source = alert_source.strip().lower()
    if source == "hermes":
        return set(HERMES_ROOT_CAUSE_CATEGORIES | {"healthy", "unknown"})
    return set(VALID_ROOT_CAUSE_CATEGORIES - HERMES_ROOT_CAUSE_CATEGORIES)


def root_cause_category_instruction_for_source(alert_source: str) -> str:
    categories = taxonomy_categories_for_alert_source(alert_source)
    taxonomy = render_prompt_taxonomy(categories).strip()
    if alert_source.strip().lower() == "hermes":
        return (
            "Use exactly one category name from the Hermes taxonomy below\n\n"
            "## Hermes root cause category taxonomy (single source of truth)\n"
            f"{taxonomy}"
        )
    return (
        "Use exactly one category name from the root cause taxonomy below\n\n"
        "## Root cause category taxonomy (single source of truth)\n"
        f"{taxonomy}"
    )


def normalize_root_cause_category(raw: str, *, allowed_categories: set[str]) -> str:
    """Map adjacent labels onto a canonical allowed category when possible."""
    cleaned = raw.strip()
    if not cleaned:
        return cleaned

    if cleaned in allowed_categories:
        return cleaned

    normalized = _normalize_token(cleaned)
    if normalized in allowed_categories:
        return normalized

    alias_target = _CATEGORY_ALIASES.get(normalized)
    if alias_target is not None and alias_target in allowed_categories:
        logger.info("Normalized root_cause_category %r -> %r", cleaned, alias_target)
        return alias_target

    return cleaned


def _normalize_token(raw: str) -> str:
    cleaned = raw.strip().lower()
    return _TOKEN_RE.sub("_", cleaned).strip("_")


def build_diagnosis_schema(include_categories: set[str]) -> type[BaseModel]:
    category_taxonomy = render_prompt_taxonomy(include_categories).strip()

    class DiagnosisSchema(BaseModel):
        root_cause: str = Field(description="Concise root cause statement (2-3 sentences max)")
        root_cause_category: str = Field(
            description=(f"Use exactly one category from this taxonomy:\n{category_taxonomy}")
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
        validity_score: float = Field(
            default=0.0, description="0.0–1.0 confidence in the diagnosis"
        )

    return DiagnosisSchema


def claims_to_dicts(claims: list[str], status: str) -> list[dict[str, str]]:
    return [{"claim": c, "validation_status": status} for c in claims if c]


def build_investigation_result(
    *,
    root_cause: str,
    root_cause_category: str,
    causal_chain: list[str],
    validated_claims: list[str],
    non_validated_claims: list[str],
    remediation_steps: list[str],
    validity_score: float,
    alert_source: str = "",
) -> InvestigationResult:
    normalized_category = normalize_root_cause_category(
        root_cause_category,
        allowed_categories=taxonomy_categories_for_alert_source(alert_source),
    )
    score, recommendations, mismatch, reason = apply_category_alignment_adjustments(
        root_cause=root_cause,
        root_cause_category=normalized_category,
        validity_score=validity_score,
        investigation_recommendations=[],
    )
    return InvestigationResult(
        root_cause=root_cause,
        root_cause_category=normalized_category,
        causal_chain=causal_chain,
        validated_claims=claims_to_dicts(validated_claims, "validated"),
        non_validated_claims=claims_to_dicts(non_validated_claims, "not_validated"),
        remediation_steps=remediation_steps,
        validity_score=score,
        investigation_recommendations=recommendations,
        category_text_mismatch=mismatch,
        category_text_mismatch_reason=reason,
    )
