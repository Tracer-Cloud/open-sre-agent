"""Path → pytest target mapping for branch-scoped test runs (CI.md §2).

This module is the single source of truth for ``make test-scope``. Edit rules
here only — do not duplicate the mapping table in CI.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Distinct app areas in one diff that trigger escalation to ``make test-cov``.
ESCALATION_AREA_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class PathRule:
    """Map changed paths under ``path_prefix`` to pytest targets."""

    path_prefix: str
    test_targets: tuple[str, ...]
    always_escalate: bool = False


# Matched in list order — more specific prefixes must appear before parents.
RULES: tuple[PathRule, ...] = (
    # Shared core (always escalate)
    PathRule("core/runtime/", ("tests/core/runtime/",)),
    PathRule("core/domain/", (), always_escalate=True),
    PathRule("core/orchestration/", (), always_escalate=True),
    PathRule("utils/", (), always_escalate=True),
    # Specific sub-packages before their parent
    PathRule("integrations/llm_cli/", ("tests/integrations/llm_cli/",)),
    PathRule("integrations/opensre/", ("tests/integrations/opensre/",)),
    PathRule("integrations/hermes/", ("tests/hermes/",)),
    PathRule("integrations/", ("tests/integrations/",)),
    PathRule("tools/fleet_monitoring/", ("tests/agent/", "tests/fleet_monitoring/")),
    PathRule("cli/", ("tests/cli/",)),
    PathRule("tools/watch_dog/", ("tests/watch_dog/",)),
    PathRule("tools/", ("tests/tools/",)),
    PathRule("services/", ("tests/services/", "tests/tools/")),
    PathRule("platform/analytics/", ("tests/analytics/",)),
    PathRule("platform/guardrails/", ("tests/test_guardrails/",)),
    PathRule("platform/masking/", ("tests/masking/",)),
    PathRule("deployment/entrypoints/", ("tests/entrypoints/",)),
    PathRule("deployment/remote/", ("tests/remote/",)),
    PathRule("platform/sandbox/", ("tests/sandbox/",)),
    PathRule("deployment/", ("tests/deployment/",)),
    PathRule("core/orchestration/node/publish_findings/", ("tests/delivery/",)),
    PathRule("platform/auth/", ("tests/platform/auth/",)),
    PathRule("config/webapp.py", ("tests/test_webapp.py",)),
    # Repo-wide config
    PathRule("pyproject.toml", (), always_escalate=True),
    PathRule("uv.lock", (), always_escalate=True),
    PathRule("pytest.ini", (), always_escalate=True),
    PathRule("Makefile", (), always_escalate=True),
    PathRule("infra/ci/", ("tests/infra_ci/",)),
)


def _matches(path: str, prefix: str) -> bool:
    return path.startswith(prefix) or path == prefix.rstrip("/")


def _area_key(prefix: str) -> str:
    parts = prefix.split("/")
    if len(parts) > 1 and parts[0] == "app":
        return parts[1]
    if parts[0] == "deployment":
        return "deployment"
    return prefix


def classify(changed: list[str]) -> tuple[bool, list[str], list[str]]:
    """Return ``(should_escalate, test_targets, matched_areas)``."""
    escalate = False
    targets: list[str] = []
    areas: list[str] = []

    for path in changed:
        matched = False
        for rule in RULES:
            if not _matches(path, rule.path_prefix):
                continue
            matched = True
            if rule.always_escalate:
                escalate = True
            else:
                area = _area_key(rule.path_prefix)
                if area not in areas:
                    areas.append(area)
                for target in rule.test_targets:
                    if target not in targets:
                        targets.append(target)
            break

        if not matched:
            if path.startswith("tests/"):
                if path not in targets:
                    targets.append(path)
            elif path.startswith("app/"):
                escalate = True

    if len(areas) >= ESCALATION_AREA_THRESHOLD:
        escalate = True

    existing = [t for t in targets if Path(t).exists()]
    dropped = [t for t in targets if t not in existing]
    if dropped:
        print(f"  (skipping non-existent targets: {', '.join(dropped)})", flush=True)
    return escalate, existing, areas
