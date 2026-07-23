"""What the hosted GitHub MCP server needs before it is considered configured.

Transport is fixed to Streamable HTTP — the only mode anyone selects in
practice, and SSE/stdio are deprecated for the hosted server. Only the fields
a user is actually prompted for are declared here.

``auth_token`` is optional: the MCP server can authenticate upstream. When a
token is present, the verifier probes the hosted server; CLI/wizard surfaces
that already ran the richer repo-scope validation can call
:func:`apply_setup` with ``verify=None`` so the probe is not repeated.

``username`` is store-only and never prompted — surfaces that already know the
authenticated login pass it in so the welcome banner can greet by GitHub
handle. :attr:`finalize` stamps analytics when that login is present.
"""

from __future__ import annotations

from config.constants.github import (
    GITHUB_MCP_AUTH_TOKEN_ENV,
    GITHUB_MCP_MODE_ENV,
    GITHUB_MCP_TOOLSETS_ENV,
    GITHUB_MCP_URL_ENV,
)
from integrations.github.mcp import (
    DEFAULT_GITHUB_MCP_MODE,
    DEFAULT_GITHUB_MCP_TOOLSETS,
    DEFAULT_GITHUB_MCP_URL,
)
from integrations.github.verifier import verify_github
from integrations.setup_flow import IntegrationSetupSpec, SetupField

MODE_FIELD = "mode"
URL_FIELD = "url"
AUTH_TOKEN_FIELD = "auth_token"
TOOLSETS_FIELD = "toolsets"
USERNAME_FIELD = "username"

_DEFAULT_TOOLSETS = ",".join(DEFAULT_GITHUB_MCP_TOOLSETS)


def _identify_github_user(credentials: dict[str, str | None]) -> str:
    """Stamp analytics with the authenticated GitHub login when present."""
    username = (credentials.get(USERNAME_FIELD) or "").strip()
    if not username:
        return ""
    from platform.analytics.cli import identify_github_username

    identify_github_username(username)
    return ""


GITHUB_SETUP = IntegrationSetupSpec(
    service="github",
    fields=(
        SetupField(
            name=MODE_FIELD,
            label="GitHub MCP mode",
            env_var=GITHUB_MCP_MODE_ENV,
            constant=DEFAULT_GITHUB_MCP_MODE,
        ),
        SetupField(
            name=URL_FIELD,
            label="GitHub MCP URL",
            prompt="MCP URL",
            env_var=GITHUB_MCP_URL_ENV,
            default=DEFAULT_GITHUB_MCP_URL,
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="GitHub auth token",
            prompt="GitHub PAT / auth token",
            env_var=GITHUB_MCP_AUTH_TOKEN_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=TOOLSETS_FIELD,
            label="GitHub MCP toolsets",
            prompt="Toolsets",
            env_var=GITHUB_MCP_TOOLSETS_ENV,
            default=_DEFAULT_TOOLSETS,
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="GitHub username",
            # Store-only: populated by surfaces that already validated a login,
            # never prompted. Blank is fine — finalize no-ops without one.
            required=False,
        ),
    ),
    verify=verify_github,
    finalize=_identify_github_user,
)

__all__ = [
    "AUTH_TOKEN_FIELD",
    "GITHUB_SETUP",
    "MODE_FIELD",
    "TOOLSETS_FIELD",
    "URL_FIELD",
    "USERNAME_FIELD",
]
