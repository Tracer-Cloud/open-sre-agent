"""Agent-callable architecture audit tool."""

from __future__ import annotations

from typing import Any, cast

from core.tool_framework.tool_decorator import tool
from integrations.github.client import resolve_github_token
from integrations.github.helpers import github_creds, github_source_available
from tools.architecture_issue_tool.models import ViolationKind, build_error_result
from tools.architecture_issue_tool.repo_workspace import WorkspaceError, cloned_github_repo
from tools.architecture_issue_tool.scan import run_architecture_scan

_DEFAULT_CATEGORY_SET = frozenset(
    {
        "layer_import",
        "direct_import",
        "compatibility_shim",
        "misplaced_module",
    }
)


def _github_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        (github_source_available(sources) or resolve_github_token(None))
        and gh.get("owner")
        and gh.get("repo")
    )


def _github_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    return {"owner": gh.get("owner"), "repo": gh.get("repo"), **github_creds(gh)}


@tool(
    name="find_architecture_violations",
    source="github",
    description=(
        "Clone and scan a GitHub repository for architecture violations using "
        "polyglot tree-sitter import graphs; propose atomic refactor tasks."
    ),
    use_cases=[
        "Auditing layer/import violations in a GitHub repository",
        "Finding compatibility shims and misplaced modules before a refactor sprint",
        "Producing a Markdown audit summary and atomic refactor task suggestions",
    ],
    anti_examples=[
        "Executing refactors automatically",
        "Creating GitHub issues directly from this tool",
        "Scanning only the local OpenSRE checkout without specifying owner/repo",
    ],
    requires=["owner", "repo"],
    surfaces=("investigation", "chat"),
    side_effect_level="read_only",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "ref": {"type": "string"},
            "strict_layers": {"type": "boolean", "default": True},
            "include_baselines": {"type": "boolean", "default": False},
            "categories": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "layer_import",
                        "direct_import",
                        "compatibility_shim",
                        "misplaced_module",
                    ],
                },
            },
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_available,
    extract_params=_github_extract_params,
)
def find_architecture_violations(
    owner: str,
    repo: str,
    ref: str = "",
    strict_layers: bool = True,
    include_baselines: bool = False,
    categories: list[str] | None = None,
    github_token: str | None = None,
    local_path: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Scan a GitHub repository for architecture violations."""
    normalized_categories: list[ViolationKind] | None = None
    if categories is not None:
        normalized_categories = [
            cast(ViolationKind, item) for item in categories if item in _DEFAULT_CATEGORY_SET
        ]

    try:
        with cloned_github_repo(
            owner,
            repo,
            ref=ref,
            token=github_token,
            local_path=local_path,
        ) as workspace:
            return run_architecture_scan(
                workspace,
                strict_layers=strict_layers,
                include_baselines=include_baselines,
                categories=normalized_categories,
            )
    except WorkspaceError as exc:
        return build_error_result(owner=owner, repo=repo, error=str(exc), ref=ref)
