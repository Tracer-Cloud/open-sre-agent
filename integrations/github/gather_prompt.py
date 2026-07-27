"""GitHub tool-usage recipe for the evidence-gather prompt.

Registered with :func:`platform.harness_ports.register_gather_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def github_gather_prompt_fragment() -> str:
    return (
        "For GitHub repository metadata such as star count, forks, visibility, "
        "or default branch, call get_github_repository — do not use "
        "search_github_code or search_github_issues for those questions."
    )


__all__ = ["github_gather_prompt_fragment"]
