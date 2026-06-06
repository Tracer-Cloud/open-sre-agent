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
import os
from typing import Any, ClassVar

from app.agent.investigation import ConnectedInvestigationAgent
from app.tools.registered_tool import RegisteredTool

logger = logging.getLogger(__name__)

# Default minimum-tool-call floor for the opensre+llm arm. Overridable via the
# ``BENCH_MIN_TOOL_CALLS`` env var so the floor can be swept across runs WITHOUT
# editing code — each sweep point is a fresh CLI process, so an import-time read
# is sufficient. Tests still override the class attribute directly.
#
# Calibrated to 5 based on the 2026-06-06 floorsweep on 30 gpt-4o cases × 3
# seeds (.bench-results/cloudopsbench_floorsweep_openai/). Floor=5 produced the
# highest single-shot A@1 mean (0.578) and the highest object_a1 (0.811) while
# preserving a `rel` (0.374) much closer to the paper's gpt-4o reference (0.63)
# than floor=8 (rel=0.306). Floor=8 (the prior default) over-explored — agents
# averaged 9 tool calls per case, burning 3-4 calls on tools that didn't change
# the diagnosis. See EXPERIMENTS.md in bench-results-openai/ for the full table.
_DEFAULT_MIN_TOOL_CALLS = 5
_ENV_MIN_TOOL_CALLS = "BENCH_MIN_TOOL_CALLS"


def _resolve_min_tool_calls() -> int:
    """Read the floor from the environment, falling back to the default.

    Invalid or negative values are ignored (with a warning) rather than
    crashing a long bench run; a 0 floor is legal and means "let the LLM
    decide", i.e. the same termination policy as the llm_alone control.
    """
    raw = os.environ.get(_ENV_MIN_TOOL_CALLS)
    if raw is None or raw.strip() == "":
        return _DEFAULT_MIN_TOOL_CALLS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Ignoring non-integer %s=%r; using default floor %d",
            _ENV_MIN_TOOL_CALLS,
            raw,
            _DEFAULT_MIN_TOOL_CALLS,
        )
        return _DEFAULT_MIN_TOOL_CALLS
    if value < 0:
        logger.warning(
            "Ignoring negative %s=%d; using default floor %d",
            _ENV_MIN_TOOL_CALLS,
            value,
            _DEFAULT_MIN_TOOL_CALLS,
        )
        return _DEFAULT_MIN_TOOL_CALLS
    return value


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
    Set ``BENCH_MIN_TOOL_CALLS`` to sweep the floor across runs.
    """

    MIN_TOOL_CALLS = _resolve_min_tool_calls()
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
        return _filter_to_bench_package(tools, self.ALLOWED_TOOL_MODULE_PREFIXES)


def _filter_to_bench_package(
    tools: list[RegisteredTool],
    allowed_prefixes: tuple[str, ...],
) -> list[RegisteredTool]:
    """Shared bench-package tool filter — same policy across all bench agents.

    Both :class:`BenchInvestigationAgent` (the opensre+llm path) and
    :class:`BaselineLLMAloneAgent` (the llm_alone control arm) must see the
    same tool surface; the comparison between modes is only fair when the
    tool inventory is identical. Extracting the filter into a free function
    keeps that contract enforced by reuse rather than by a "remember to keep
    these in sync" comment.
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
        if tool.origin_module.startswith(allowed_prefixes):
            kept.append(tool)
        else:
            dropped.append(f"{tool.name} ({tool.origin_module})")
    if dropped:
        logger.debug("Bench filter dropped %d tool(s): %s", len(dropped), ", ".join(dropped))
    return kept


class BaselineLLMAloneAgent(ConnectedInvestigationAgent):
    """LLM-alone control arm for the bench.

    The audit identified this as the single biggest scientific gap in the
    cycle: without a matched in-harness baseline on the same cases, no
    "opensre helps" claim is attributable. This subclass is that control.

    What it inherits from :class:`ConnectedInvestigationAgent` (production):
      - The ReAct loop, evidence accumulation, context-budget enforcement
      - The default ``_should_accept_conclusion`` hook — accept whatever
        the LLM decides, no minimum-tool-call floor

    What it overrides:
      - ``_filter_tools`` — same bench-package whitelist
        :class:`BenchInvestigationAgent` uses, so the two modes see the
        IDENTICAL tool inventory and the only difference between them is
        the bench-specific termination policy (Lever #1's MIN_TOOL_CALLS=8)

    What this measures: the marginal lift from the bench-specific lever
    (MIN_TOOL_CALLS), not the full opensre-vs-bare-LLM gap. The system
    prompt and ReAct loop are still opensre's. A truly pure baseline
    (minimal SRE prompt, no opensre planning structure) is a follow-up;
    surface this limitation in the report rather than hiding it.
    """

    ALLOWED_TOOL_MODULE_PREFIXES: ClassVar[tuple[str, ...]] = (_BENCH_TOOL_MODULE_PREFIX,)

    def _filter_tools(
        self,
        tools: list[RegisteredTool],
    ) -> list[RegisteredTool]:
        return _filter_to_bench_package(tools, self.ALLOWED_TOOL_MODULE_PREFIXES)


# Minimal SRE-diagnostic system prompt for the pure baseline.
#
# Deliberately concise — no planner instructions, no stage-gate language, no
# anti-hallucination scaffolding, no evidence-budget guidance. The point of
# this control is to measure what a general-purpose LLM does with the same
# tools and zero opensre-specific framing. Anything richer than this prompt
# starts smuggling opensre's structural priors back into the "baseline."
#
# We DO ask for the same output shape (root cause + faulting component)
# because the scorer needs to find those fields; that's a measurement
# protocol requirement, not a reasoning prior.
_PURE_BASELINE_SYSTEM_PROMPT = (
    "You are an SRE diagnosing a Kubernetes incident. An alert has been raised. "
    "Use the available tools to investigate. When you have enough evidence to "
    "name a root cause, state your conclusion in two short fields: "
    "(1) the faulting component (Kubernetes object: deployment, pod, service, "
    "secret, etc.), and (2) the root cause in 1-2 sentences."
)


class PureBaselineAgent(ConnectedInvestigationAgent):
    """Pure LLM-alone baseline — strips opensre's system prompt as well.

    The third arm the audit asked for. Comparison hierarchy:
      - ``opensre+llm``         → opensre prompt + Lever #1 floor (full opensre)
      - ``llm_alone``           → opensre prompt − Lever #1 floor (isolates Lever #1)
      - ``llm_alone_pure`` (this) → minimal prompt − Lever #1 floor (isolates opensre's PROMPT vs raw LLM+tools)

    Reading the contrasts:
      - (opensre+llm) − (llm_alone)         = lift from Lever #1
      - (opensre+llm) − (llm_alone_pure)    = lift from full opensre stack (prompt + Lever #1)
      - (llm_alone)   − (llm_alone_pure)    = lift from opensre's PROMPT alone

    What this STILL inherits from :class:`ConnectedInvestigationAgent`:
    the ReAct loop scaffolding (tool execution, evidence accumulation,
    context-budget enforcement, retry-on-tool-error, etc.). Those are
    mechanical plumbing every baseline would need; they aren't
    "opensre's reasoning." The honest framing is "minimal-prompt LLM
    with tools," not "pure stdin/stdout LLM" — which would not be a
    meaningful comparison anyway.
    """

    ALLOWED_TOOL_MODULE_PREFIXES: ClassVar[tuple[str, ...]] = (_BENCH_TOOL_MODULE_PREFIX,)

    def _filter_tools(
        self,
        tools: list[RegisteredTool],
    ) -> list[RegisteredTool]:
        # Same bench-package whitelist as Bench + Baseline arms — tool
        # surface is the methodological constant across all three modes.
        return _filter_to_bench_package(tools, self.ALLOWED_TOOL_MODULE_PREFIXES)

    def _build_system_prompt(self, state: dict[str, Any]) -> str:  # noqa: ARG002 — interface contract
        return _PURE_BASELINE_SYSTEM_PROMPT
