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

from app.agent.investigation import ConnectedInvestigationAgent


class BenchInvestigationAgent(ConnectedInvestigationAgent):
    """Bench subclass that requires N tool calls before allowing conclusion.

    Threshold is a class attribute so subclasses or tests can override it
    without rebuilding the agent instance. Default 8 is calibrated for
    CloudOpsBench's median win-trajectory (~15-20 tool calls) while
    leaving headroom: even a perfect 8-call run is within the loop cap.
    """

    MIN_TOOL_CALLS = 8

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
