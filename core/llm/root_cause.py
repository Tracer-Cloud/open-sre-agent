"""Parse a free-text LLM diagnosis into structured root-cause fields.

Legacy fallback used when the diagnose stage's structured output is unavailable;
extracts root cause, category, claims, causal chain, and remediation steps from
the ``ROOT_CAUSE:`` / ``VALIDATED_CLAIMS:`` / … convention in the model's text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.domain.types.root_cause_categories import VALID_ROOT_CAUSE_CATEGORIES


@dataclass(frozen=True)
class RootCauseResult:
    root_cause: str
    root_cause_category: str
    validated_claims: list[str]
    non_validated_claims: list[str]
    causal_chain: list[str]
    remediation_steps: list[str]


def parse_root_cause(response: str) -> RootCauseResult:
    """Parse root cause, category, and claims from LLM response."""
    root_cause = "Unable to determine root cause"
    root_cause_category = "unknown"
    validated_claims: list[str] = []
    non_validated_claims: list[str] = []
    causal_chain: list[str] = []
    remediation_steps: list[str] = []

    if "ROOT_CAUSE_CATEGORY:" in response:
        parts = response.split("ROOT_CAUSE_CATEGORY:", 1)
        if len(parts) > 1:
            after = parts[1]
            for line in after.split("\n"):
                candidate = line.strip().lower()
                if not candidate:
                    continue
                if candidate in VALID_ROOT_CAUSE_CATEGORIES:
                    root_cause_category = candidate
                    break
                for token in re.findall(r"[a-z_][a-z0-9_]*", candidate):
                    if token in VALID_ROOT_CAUSE_CATEGORIES:
                        root_cause_category = token
                        break
                if root_cause_category != "unknown":
                    break

    if "ROOT_CAUSE:" in response:
        parts = response.split("ROOT_CAUSE:", 1)
        if len(parts) > 1:
            after = parts[1]
            for delimiter in (
                "ROOT_CAUSE_CATEGORY:",
                "VALIDATED_CLAIMS:",
                "NON_VALIDATED_CLAIMS:",
                "CAUSAL_CHAIN:",
                "REMEDIATION_STEPS:",
            ):
                if delimiter in after:
                    root_cause = after.split(delimiter, 1)[0].strip()
                    break
            else:
                root_cause = after.strip()

            if "VALIDATED_CLAIMS:" in after:
                validated_section = after.split("VALIDATED_CLAIMS:", 1)[1]
                for delimiter in (
                    "NON_VALIDATED_CLAIMS:",
                    "CAUSAL_CHAIN:",
                    "REMEDIATION_STEPS:",
                ):
                    if delimiter in validated_section:
                        validated_text = validated_section.split(delimiter, 1)[0]
                        break
                else:
                    validated_text = validated_section

                for line in validated_text.strip().split("\n"):
                    line = line.strip().lstrip("*-• ").strip()
                    if (
                        line
                        and not line.startswith("NON_")
                        and not line.startswith("CAUSAL_CHAIN")
                        and not line.startswith("CONFIDENCE")
                        and not line.startswith("ROOT_CAUSE")
                        and not line.startswith("REMEDIATION_STEPS")
                    ):
                        validated_claims.append(line)

            if "NON_VALIDATED_CLAIMS:" in after:
                non_validated_section = after.split("NON_VALIDATED_CLAIMS:", 1)[1]
                for delimiter in (
                    "ALTERNATIVE_HYPOTHESES_CONSIDERED:",
                    "CAUSAL_CHAIN:",
                    "REMEDIATION_STEPS:",
                ):
                    if delimiter in non_validated_section:
                        non_validated_text = non_validated_section.split(delimiter, 1)[0]
                        break
                else:
                    non_validated_text = non_validated_section

                for line in non_validated_text.strip().split("\n"):
                    line = line.strip().lstrip("*-• ").strip()
                    if (
                        line
                        and not line.startswith("CAUSAL_CHAIN")
                        and not line.startswith("ALTERNATIVE")
                        and not line.startswith("REMEDIATION_STEPS")
                    ):
                        non_validated_claims.append(line)

            if "CAUSAL_CHAIN:" in after:
                causal_section = after.split("CAUSAL_CHAIN:", 1)[1]
                if "REMEDIATION_STEPS:" in causal_section:
                    causal_section = causal_section.split("REMEDIATION_STEPS:", 1)[0]
                causal_text = causal_section

                for line in causal_text.strip().split("\n"):
                    line = line.strip().lstrip("*-• ").strip()
                    if line and not line.startswith("ALTERNATIVE"):
                        causal_chain.append(line)

            if "REMEDIATION_STEPS:" in after:
                rem_section = after.split("REMEDIATION_STEPS:", 1)[1]
                for line in rem_section.strip().split("\n"):
                    line = line.strip().lstrip("*-•( ").strip()
                    if not line or line.startswith("("):
                        continue
                    if any(
                        line.startswith(h)
                        for h in (
                            "ROOT_CAUSE",
                            "VALIDATED",
                            "NON_VALIDATED",
                            "CAUSAL",
                            "ALTERNATIVE",
                            "REMEDIATION_STEPS",
                        )
                    ):
                        break
                    remediation_steps.append(line)

    return RootCauseResult(
        root_cause=root_cause,
        root_cause_category=root_cause_category,
        validated_claims=validated_claims,
        non_validated_claims=non_validated_claims,
        causal_chain=causal_chain,
        remediation_steps=remediation_steps,
    )


__all__ = ["RootCauseResult", "parse_root_cause"]
