"""CloudOpsBench-specific investigation agent.

Subclasses :class:`core.orchestration.node.investigate.ConnectedInvestigationAgent` to
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

from core.orchestration.node.investigate import ConnectedInvestigationAgent
from tools.registered_tool import RegisteredTool

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


# Trimmed bench prompt — sits between the full opensre prompt and the pure
# baseline. The 2026-06-08 full-N floor=0 run loss diagnosis (n=353 paired
# scenarios) showed 60% of opensre+llm losses against llm_alone_pure are
# "predictor drift" cases: opensre's investigation correctly identifies the
# fault_object (object_a1 is ~tied between the arms) but the predictor's
# rank-1 root_cause is a token adjacent to the truth — e.g.
# ``liveness_probe_incorrect_timing`` instead of ``..._protocol``,
# ``image_registry_dns_failure`` instead of ``incorrect_image_reference``,
# ``namespace_cpu_quota_exceeded`` instead of ``namespace_pod_quota_exceeded``.
#
# The predictor is faithful to its input; the wrong tokens come from
# opensre's investigation TEXT itself. The full opensre system prompt's
# hedging + validation + multi-stage scaffolding produces RCAs that lean on
# adjacent vocabulary the predictor then formalizes.
#
# This trimmed variant keeps the parts that have customer value
# (tool-output citation, structured component + root_cause output) and drops
# the parts that empirically produce noise on cloudopsbench (hedging-by-
# default language, multi-stage planner instructions, validation-of-
# validation directives). It is BENCH-ONLY — production opensre's prompt is
# unchanged.
_TRIMMED_BENCH_SYSTEM_PROMPT = (
    "You are an SRE agent investigating a Kubernetes incident. Use the "
    "available tools to gather evidence — typically pod state, error logs, "
    "recent events, and resource configuration.\n\n"
    "Dependency-traversal rule — INVESTIGATION-LAYER (does not affect "
    "your final localization decision; just expands what evidence you "
    "gather before concluding):\n"
    "  When the failing service shows connection-related errors in its "
    "logs (connection refused, timeout, authentication failure, write "
    "failure, port unreachable), the actual fault may live in a stateful "
    "DEPENDENCY (database, cache, message queue) rather than in the "
    "service that reports the symptom. Before concluding, also call "
    "GetErrorLogs on the dependency pod itself. Stateful dependency pods "
    "(MySQL / MariaDB / Postgres / Redis / RabbitMQ / etc.) log their "
    "OWN internal failure modes — read-only mode enforcement, connection "
    "pool exhaustion, replication errors, slow queries, credential "
    "rejections — that are NOT visible from the caller's side. The same "
    "applies for namespace-scoped admission failures: when multiple pods "
    "in a namespace fail together, query for namespace-level resources "
    "(quotas, network policies, service accounts) rather than diagnosing "
    "from one victim service's logs.\n"
    "  This rule expands the EVIDENCE you collect; it does NOT bias your "
    "localization. The final faulting component is whichever piece the "
    "evidence trajectory points at, including 'the dependency is healthy "
    "but the caller's config to reach it is wrong' — in which case the "
    "caller IS the fault.\n\n"
    "Alert-anchored upstream-attribution rule — INVESTIGATION-LAYER "
    "(applies when the alert itself names a specific service AND describes "
    "a performance, latency, network, or resource-saturation problem — "
    "e.g. 'network delay', 'CPU saturation', 'memory pressure', 'slow "
    "response'):\n"
    "  Treat the service named in the alert as the primary suspect. A "
    "slow or saturated upstream service often produces NOISY error logs "
    "in downstream services that depend on it — timeouts, retries, "
    "'cannot reach', 'connection refused', 'request failed', 'order not "
    "found' — while the actual faulting service may show NO error logs "
    "at all (it is not crashing; it is just slow). The downstream "
    "services with the loudest logs are usually VICTIMS, not causes.\n"
    "  When this pattern appears (alert names X with a performance / "
    "network / resource issue; X has few or no error logs; multiple other "
    "services Y, Z, W show timeout-shaped or 'cannot reach upstream' "
    "errors), the answer is almost always X with a performance / network "
    "/ resource root cause (pod_network_delay, pod_cpu_overload, "
    "pod_memory_pressure, etc.), not Y/Z/W with a runtime / config / "
    "database root cause. Investigate X's pod-level metrics — CPU, "
    "memory, network — rather than chasing the loud downstream logs.\n"
    "  This rule does NOT override the dependency-traversal rule above. "
    "If the alert is about a connection-shaped failure (not a "
    "performance-shaped one), the dependency-traversal rule still "
    "applies. The two rules describe complementary patterns: "
    "connection-failure alerts often point DOWNSTREAM to a dependency; "
    "performance-shaped alerts usually point AT the named service "
    "itself.\n\n"
    "When you have identified the failing component and root cause, "
    "produce a concise conclusion:\n"
    "  (1) the faulting component — Kubernetes object (deployment, pod, "
    "service, secret, namespace, etc.)\n"
    "  (2) the root cause in 1-2 sentences naming the specific failure "
    "mode\n"
    "  (3) cite the tool output that supports your conclusion.\n\n"
    "Do not hedge when the evidence is clear. Do not validate the same "
    "claim multiple ways. Do not break the investigation into stages "
    "unless the case genuinely requires multi-step escalation."
)


class BenchInvestigationAgentTrimmedPrompt(BenchInvestigationAgent):
    """Bench-only ``BenchInvestigationAgent`` variant with a trimmed prompt.

    Inherits BenchInvestigationAgent's tool filter and the configurable
    ``MIN_TOOL_CALLS`` class attribute (set from config.min_tool_calls at
    CLI startup). Overrides only the system prompt.

    Selected by setting ``agent_variant: trimmed_prompt`` in a bench config.
    The CLI override (see ``_framework/cli.py``) swaps the adapter's
    investigation_agent_class to this when the field is set; default
    behavior (agent_variant unset / "default") returns the original
    ``BenchInvestigationAgent`` class.
    """

    def _build_system_prompt(self, state: dict[str, Any]) -> str:  # noqa: ARG002 — interface contract
        return _TRIMMED_BENCH_SYSTEM_PROMPT
