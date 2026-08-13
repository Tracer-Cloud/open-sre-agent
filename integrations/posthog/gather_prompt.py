"""PostHog tool-usage recipe for the evidence-gather prompt.

Registered with :func:`platform.harness_ports.register_gather_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations

from core.agent_harness.turns.cohort_identity import COHORT_IDENTITY_UNVERIFIED_MARK


def posthog_gather_prompt_fragment() -> str:
    return (
        "For PostHog metric, user-count, or HogQL questions: do not probe "
        'entity taxonomy with invented kinds like "events" and do not stop '
        "after list_posthog_tools or schema errors. Prefer call_posthog_tool "
        "with tool_name='execute-sql' and a HogQL count (FROM events, filter "
        "properties.$os / $browser as needed), or tool_name='query-trends' "
        "when a trends series fits. Never use tool_name='exec' for SQL — "
        "skip the exec wrapper and call execute-sql directly. If you list "
        "tools, use name_filter='execute-sql' (not broad terms that only "
        "return exec). At most one list_posthog_tools call, then run the "
        "metric query — a failed schema probe is not an answer. "
        "Signup / signed-up / retention-of-signups are not the same as "
        "sign-in or login: never use event='user_signed_in', "
        "event='signed_in', event='login', or similar login events as a "
        "stand-in for signup. Confirm a real signup event from schema "
        "(e.g. user_signed_up, signed_up, signup) before counting or "
        "computing retention. If no signup event is verified, do not run a "
        "count/retention query on a login event — stop with "
        f"'{COHORT_IDENTITY_UNVERIFIED_MARK}' so the answer path can draft "
        "HogQL + one setup CTA."
    )


__all__ = ["posthog_gather_prompt_fragment"]
