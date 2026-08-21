"""GitHub tool-usage recipe for the evidence-gather prompt.

Registered with :func:`infrastructure.harness_ports.register_gather_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def github_gather_prompt_fragment() -> str:
    return (
        "For GitHub repository metadata such as star count, forks, visibility, "
        "or default branch, call get_github_repository — do not use "
        "search_github_code or search_github_issues for those questions.\n"
        "For GitHub star history, day-by-day stars, stars gained in a recent "
        "window, or star velocity, call get_github_star_history. Use the user's "
        "requested day count when present; otherwise use its 30-day default. "
        "Do not use execute_python_code or scan raw stargazer pages yourself for "
        "these questions."
    )


__all__ = ["github_gather_prompt_fragment"]
