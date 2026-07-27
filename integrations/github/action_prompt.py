"""GitHub action-agent prompt fragment — routes `gh` CLI requests to tools.

Registered with :func:`platform.harness_ports.register_action_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def github_action_prompt_fragment() -> str:
    return """GITHUB CLI REQUESTS ARE ACTION TOOLS — NOT HANDOFFS:
When the user asks to create, list, view, edit, close, comment, assign, label,
merge, or search GitHub issues/PRs/repos *as the primary request* (including
github.com/owner/repo URLs and follow-ups like "from this info create an issue on GitHub"),
call github_cli directly. Do NOT emit assistant_handoff for these — they are NOT
docs questions and are NOT covered by the DATA-RETRIEVAL handoff rule. Prefer
github_cli over shell_run / !gh / raw gh. github_cli is action-only and will not
run in gather.
Exception: GitHub issue/PR/repo create/list/view/merge/comment as a *standalone*
request via `gh` is NOT the DATA-RETRIEVAL handoff — call github_cli, as above.
Do NOT use github_cli when the user is diagnosing a crash/failure/outage and
names GitHub among other sources to query (e.g. sentry + github issues +
posthog) — emit investigation_start instead; the investigation gathers those
sources. github_cli is for GitHub-only product operations, not multi-source RCA.
Pass args after the `gh` binary; optional repo as owner/name for -R.
Examples:
* "create an issue titled X with body Y"
  → github_cli(args=["issue", "create", "--title", "X", "--body", "Y"])
* "list open PRs" → github_cli(args=["pr", "list", "--state", "open"])
* "merge PR 45 with squash auto"
  → github_cli(args=["pr", "merge", "45", "--squash", "--auto"])
* "figure out why OpenSRE is crashing on Windows — query sentry, github issues,
  and posthog" → investigation_start ONLY (not github_cli alone or paired with
  investigation_start in the same turn)
After the tool returns, reply briefly from the result summary — do not hand off
asking the user to run `gh` themselves."""


__all__ = ["github_action_prompt_fragment"]
