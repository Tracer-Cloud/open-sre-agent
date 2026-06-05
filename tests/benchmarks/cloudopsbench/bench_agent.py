"""CloudOpsBench-specific investigation agent.

Subclasses :class:`app.agent.investigation.ConnectedInvestigationAgent` to
enforce a minimum-tool-call floor before the agent is allowed to conclude.
Production code is untouched — bench-only termination behavior lives here.

Why we need a floor for the bench
----------------------------------
Production opensre lets the LLM decide when it has enough evidence. That's
the right default for real incidents: latency matters, the LLM is usually
right after a few tool calls, and forcing extra calls wastes tokens.

CloudOpsBench cases are different:
  - The paper's protocol rewards deep multi-source evidence gathering
    (15-20 tool calls typical in winning runs).
  - The June-3 OpenAI bench showed gpt-4o median=7 steps and gpt-5
    median=4 steps — both producing a1=0 despite the agent's structural
    advantage over plain LLM.
  - Tool coverage was 0.20 (gpt-4o) and 0.00 (gpt-5) — agents bailed
    before exercising the tools the paper measures against.

We force the bench agent to gather more evidence before concluding. The
loop's outer cap (``MAX_INVESTIGATION_LOOPS``) still bounds the worst
case, so a stubborn model can't infinite-loop.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from app.agent.investigation import ConnectedInvestigationAgent
from app.tools.registered_tool import RegisteredTool

logger = logging.getLogger(__name__)

# Tools available to the bench agent are exactly those registered by the
# bench-specific package. Production opensre tools (real EKS API calls,
# Hermes log tailing, etc.) would hit live infrastructure that the bench
# task role intentionally cannot reach — burning calls on AccessDenied
# instead of returning deterministic replay data.
#
# Trailing dot is deliberate: it matches anything UNDER the package, not
# the package root itself. The registry only auto-discovers submodules
# (via ``pkgutil.iter_modules``), so a tool whose ``origin_module`` is
# exactly the root is theoretical — but if you register a single-file
# bench tool module directly via :func:`register_external_tool_package`,
# its ``origin_module`` will be the root and it'll be dropped here. Use
# a submodule (e.g. ``tools/k8s/__init__.py``) instead.
_BENCH_TOOL_MODULE_PREFIX = "tests.benchmarks.cloudopsbench.tools."


class BenchInvestigationAgent(ConnectedInvestigationAgent):
    """Bench subclass that requires N tool calls before allowing conclusion.

    Threshold is a class attribute so subclasses or tests can override it
    without rebuilding the agent instance. Default 8 is calibrated for
    CloudOpsBench's median win-trajectory (~15-20 tool calls) while
    leaving headroom: even a perfect 8-call run is within the loop cap.
    """

    MIN_TOOL_CALLS = 8
    ALLOWED_TOOL_MODULE_PREFIXES: ClassVar[tuple[str, ...]] = (_BENCH_TOOL_MODULE_PREFIX,)

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,
        iteration: int,  # noqa: ARG002 — base class signature
    ) -> tuple[bool, str | None]:
        if evidence_count >= self.MIN_TOOL_CALLS:
            return True, None
        return False, (
            f"You've gathered {evidence_count} tool result(s) so far. Before "
            f"concluding, please continue investigating — what dimensions "
            f"of the system haven't you checked yet? Consider tool sources "
            f"you haven't queried, or evidence that would support OR "
            f"contradict your current hypothesis."
        )

    def _filter_tools(
        self,
        tools: list[RegisteredTool],
    ) -> list[RegisteredTool]:
        """Restrict to bench-package tools by origin module.

        Filtering by ``origin_module`` instead of an explicit name list means
        a new bench tool added under ``tests/benchmarks/cloudopsbench/tools/``
        is picked up automatically — no risk of the whitelist drifting out
        of sync with the tool registry.

        Silent-exclusion edge cases to know about (rare today, but possible
        if someone adds a tool in an unconventional way):
          - A tool whose ``origin_module`` is exactly the prefix root (no
            trailing submodule) is dropped — see the comment on
            ``_BENCH_TOOL_MODULE_PREFIX``.
          - A tool whose ``origin_module`` defaults to the empty string
            (e.g. directly-constructed ``RegisteredTool(...)`` without
            ``origin_module=`` set) is also dropped, and logged at
            WARNING so the registry bug surfaces in the run log instead
            of silently shrinking the bench tool set.
        """
        kept: list[RegisteredTool] = []
        dropped: list[str] = []
        for tool in tools:
            if not tool.origin_module:
                logger.warning(
                    "Bench filter dropping tool %r with empty origin_module — "
                    "registry bug: tool was constructed without origin_module=. "
                    "Set it explicitly so the bench can keep it.",
                    tool.name,
                )
                dropped.append(f"{tool.name} (no origin_module)")
                continue
            if tool.origin_module.startswith(self.ALLOWED_TOOL_MODULE_PREFIXES):
                kept.append(tool)
            else:
                dropped.append(f"{tool.name} ({tool.origin_module})")
        if dropped:
            logger.debug("Bench filter dropped %d tool(s): %s", len(dropped), ", ".join(dropped))
        return kept
