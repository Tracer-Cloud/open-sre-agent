"""Prompt construction for root cause diagnosis."""

from __future__ import annotations

from typing import Any

from app.state import InvestigationState
from app.types.root_cause_categories import (
    GENERIC_FALLBACK_CATEGORIES,
    render_prompt_taxonomy,
)

from .constants import ALLOWED_EVIDENCE_SOURCES
from .directives import (
    _build_database_directive,
    _build_failover_directive,
    _build_kubernetes_directive,
    _build_memory_section,
    _build_upstream_directive,
)
from .evidence import build_evidence_sections


def build_diagnosis_prompt(
    state: InvestigationState, evidence: dict[str, Any], memory_context: str = ""
) -> str:
    """
    Build an evidence-based prompt for root cause analysis.

    Args:
        state: Investigation state with problem and hypotheses
        evidence: Collected evidence dictionary
        memory_context: Optional memory context from prior investigations

    Returns:
        Formatted prompt string for LLM
    """
    from app.masking import MaskingContext

    _masking_ctx = MaskingContext.from_state(dict(state))
    problem = _masking_ctx.mask(state.get("problem_md", "") or "")
    hypotheses = [_masking_ctx.mask(h) for h in state.get("hypotheses", [])]

    upstream_directive = _build_upstream_directive(evidence)
    database_directive = _build_database_directive(state, evidence)
    kubernetes_directive = _build_kubernetes_directive(state, evidence)
    failover_directive = _build_failover_directive(evidence)
    memory_section = _build_memory_section(memory_context)

    evidence_text = build_evidence_sections(state, evidence, _masking_ctx)

    ALLOWED_CATEGORIES = render_prompt_taxonomy()
    GROUP_GENERIC_LIST = ", ".join(sorted(GENERIC_FALLBACK_CATEGORIES))

    return f"""You are an experienced SRE performing a root cause analysis (RCA) for a production incident.

OBJECTIVE:
Produce an evidence-grounded RCA. Separate what you can prove from what you infer.
Follow this reasoning sequence:
1. Identify OBSERVED FACTS from the evidence below.
2. Generate candidate hypotheses that could explain those facts.
3. For each hypothesis, check whether the evidence confirms, contradicts, or is silent.
4. Eliminate hypotheses that contradict the evidence.
5. Select the best-supported hypothesis as the root cause. If multiple survive, pick the most parsimonious one and note alternatives.
6. If no hypothesis can be confirmed, state "Most likely ..." with the strongest candidate and explicitly list what evidence is missing.
{upstream_directive}{database_directive}{kubernetes_directive}{failover_directive}{memory_section}
DEFINITIONS:
- VALIDATED_CLAIMS: Directly supported by the evidence shown below (observed facts).
- NON_VALIDATED_CLAIMS: Plausible hypotheses or contributing factors that are NOT directly proven by the evidence.

EVIDENCE RULES:
- Ground every claim in the telemetry, logs, and metrics provided below. You may use domain knowledge to INTERPRET evidence, but do NOT fabricate facts, metrics, or log entries that are not present.
- Do NOT reference source code files or line numbers unless they appear explicitly in the evidence.
- If you lack telemetry to make a definitive ruling, you MUST state what specific evidence is missing (e.g., "No memory metrics available to confirm OOM").
- If GitHub evidence includes file paths, snippets, commits, or content, you may reference them.
- When a validated claim is supported by a specific evidence source, cite it using one of: {", ".join(ALLOWED_EVIDENCE_SOURCES)}.

ANTI-BIAS RULES:
- Do NOT assume an incident category before examining the evidence. Let the evidence determine the category.
- If the provided hypotheses do not match the evidence, discard them and form new ones.
- Consider at least two alternative explanations before settling on a root cause.
- Do NOT anchor on the first plausible explanation. Check for contradicting evidence before committing.

PROBLEM:
{problem}

HYPOTHESES TO CONSIDER (treat as suggestions, not conclusions — discard any that contradict the evidence):
{chr(10).join(f"- {h}" for h in hypotheses[:5]) if hypotheses else "- None provided"}

EVIDENCE:
{evidence_text}

OUTPUT FORMAT (follow exactly — no markdown code blocks, start immediately with ROOT_CAUSE:):

ROOT_CAUSE:
<1-2 sentences stating the root cause. If not provable, say "Most likely: ..." and state what evidence is missing. Never say only "Unable to determine".>

ROOT_CAUSE_CATEGORY:
<exactly one category name from the list below, lowercase, snake_case, no other text>
Pick the NARROWEST category that fits the evidence. The list is grouped by domain; only fall back to a category in [{GROUP_GENERIC_LIST}] when no narrow category in the relevant group applies.

{ALLOWED_CATEGORIES}
(Use "healthy" only when all metrics are within normal bounds, no errors are detected, and the alert is informational or resolved. Use "unknown" only when evidence is genuinely insufficient to commit to any narrow category. For mixed signals, prefer the narrow category that names the failing subsystem.)

VALIDATED_CLAIMS:
- <factual claim> [evidence: <source>]
- <factual claim> [evidence: <source>]

NON_VALIDATED_CLAIMS:
- <plausible inference - state what data would confirm or refute it>
- <plausible inference - state what data would confirm or refute it>

ALTERNATIVE_HYPOTHESES_CONSIDERED:
- <hypothesis considered and why it was eliminated or deprioritized>

CAUSAL_CHAIN:
- <step 1: the trigger or initial fault>
- <step 2: how the fault propagated>
- <step N: the observable symptom or alert>
(Trace from root cause to the alert. Each step should be one sentence backed by evidence where possible.)

REMEDIATION_STEPS:
- <step 1: most urgent concrete action - reference the specific service, pod, or resource identified above>
- <step 2>
- <step 3>
(3-5 steps. Ground each in the evidence. Only suggest steps using tools or resources confirmed in the investigation. No generic advice.)
"""
