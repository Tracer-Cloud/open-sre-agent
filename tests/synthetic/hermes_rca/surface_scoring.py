from __future__ import annotations

import re
from dataclasses import dataclass

VALID_SURFACE_FAMILIES = frozenset(
    {
        "messaging",
        "llm_provider",
        "execution_backend",
        "agent_runtime",
        "orchestration",
        "memory",
        "controls",
    }
)


@dataclass(frozen=True)
class SurfaceScore:
    adapter_family: bool
    analog_identification: bool
    diagnostic_question: bool

    @property
    def passed_dimensions(self) -> int:
        return (
            int(self.adapter_family)
            + int(self.analog_identification)
            + int(self.diagnostic_question)
        )

    @property
    def passed(self) -> bool:
        return self.passed_dimensions >= 2


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def score_adapter_family(output: str, expected_family: str) -> bool:
    normalized = normalize(output)
    expected = expected_family.strip().lower()

    aliases = {
        "llm_provider": ("llm provider", "model provider", "provider surface"),
        "execution_backend": (
            "execution backend",
            "execution surface",
            "runtime backend",
            "executor backend",
        ),
        "messaging": ("messaging", "message adapter", "messaging adapter"),
        "agent_runtime": ("agent runtime", "agent loop", "runtime surface"),
        "orchestration": ("orchestration", "coordinator", "multi-agent"),
        "memory": ("memory", "memory backend", "memory surface"),
        "controls": ("control", "controls", "security control"),
    }

    return any(alias in normalized for alias in aliases.get(expected, (expected,)))


def score_analog_identification(output: str, expected_analog_id: str) -> bool:
    normalized = normalize(output)
    expected = normalize(expected_analog_id)
    compact = expected.replace("-", " ")

    return expected in normalized or compact in normalized


def score_diagnostic_question(output: str) -> bool:
    stripped = output.strip()
    if "?" not in stripped:
        return False

    questions = re.findall(r"[^?]+\?", stripped)

    if not questions:
        return False

    normalized_questions = [normalize(question) for question in questions]
    actionable_terms = (
        "fetch",
        "capture",
        "inspect",
        "verify",
        "check",
        "confirm",
        "provide",
        "compare",
    )
    evidence_terms = (
        "response",
        "request",
        "payload",
        "headers",
        "adapter",
        "catalog",
        "trace",
        "log",
        "body",
        "bytes",
        "runtime",
        "event",
        "state",
    )

    return any(
        any(term in question for term in actionable_terms)
        and any(term in question for term in evidence_terms)
        for question in normalized_questions
    )


def score_surface_response(
    *,
    output: str,
    expected_family: str,
    expected_analog_id: str,
) -> SurfaceScore:
    return SurfaceScore(
        adapter_family=score_adapter_family(output, expected_family),
        analog_identification=score_analog_identification(output, expected_analog_id),
        diagnostic_question=score_diagnostic_question(output),
    )
