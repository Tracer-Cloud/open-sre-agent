"""GitHub integration package.

Other tiers import GitHub behavior through this module, not the files inside it.
The client names load eagerly; the rest are re-exported lazily through
``__getattr__`` so importing the package does not pull heavier submodules (the
login device-flow and its dependencies) until a caller actually needs them.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token

#: Public name -> the submodule that defines it, imported on first access.
_LAZY_EXPORTS: dict[str, str] = {
    "github_creds": "integrations.github.helpers",
    "saved_github_username": "integrations.github.identity",
    "GitHubLoginResult": "integrations.github.login",
    "authenticate_and_configure_github": "integrations.github.login",
    "ERR_GITHUB_TOKEN": "integrations.github.pull_requests",
    "GitHubPullRequestError": "integrations.github.pull_requests",
    "PullRequest": "integrations.github.pull_requests",
    "open_pull_request": "integrations.github.pull_requests",
    "resolve_repo_scope": "integrations.github.pull_requests",
}


def __getattr__(name: str) -> object:
    """Resolve a lazily re-exported name to its submodule attribute (PEP 562).

    Resolved on every access rather than cached in module globals, so a test that
    patches the owning submodule's attribute is reflected here.
    """
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_path), name)


if TYPE_CHECKING:
    from integrations.github.helpers import github_creds
    from integrations.github.identity import saved_github_username
    from integrations.github.login import GitHubLoginResult, authenticate_and_configure_github
    from integrations.github.pull_requests import (
        ERR_GITHUB_TOKEN,
        GitHubPullRequestError,
        PullRequest,
        open_pull_request,
        resolve_repo_scope,
    )


__all__ = [
    "ERR_GITHUB_TOKEN",
    "GitHubApiError",
    "GitHubLoginResult",
    "GitHubPullRequestError",
    "GitHubRestClient",
    "PullRequest",
    "authenticate_and_configure_github",
    "github_creds",
    "open_pull_request",
    "resolve_github_token",
    "resolve_repo_scope",
    "saved_github_username",
]
