"""Per-call project selection for the GCP tools.

Two different things get called "multiple GCP projects", and both are resolved
here:

*One credential, many projects.* GCP inherits IAM down the resource hierarchy,
so a service account granted a viewer role at the folder or organization level
reads every project beneath it — unlike AWS, where each account needs its own
assumed role. Modelling those projects as separate integration *instances*
would duplicate one credential N times, so each tool takes an LLM-supplied
``project`` argument resolved against the configured scope.

*Many credentials.* Estates that do not share a folder need one registered
instance per credential (``GCP_INSTANCES``). Each instance contributes its own
projects to the same flat ``project`` namespace, and
:func:`group_projects` maps a resolved selection back to the credential that
can actually reach each project.

Cloud Logging accepts several ``resourceNames`` in a single ``entries.list``
call, so a cross-project query under one credential is one request rather than
a fan-out — hence this returns a *list*, not a single project.

The ``project`` argument is deliberately absent from every tool's
``injected_params``. Anything listed there is protected and overrides what the
model passes, which is exactly how the Kubernetes ``context`` parameter ended
up inert for cluster selection.
"""

from __future__ import annotations

import json
from typing import Any

#: Accepted spellings for "every project this credential can reach".
_ALL_TOKENS = frozenset({"*", "all"})


def group_projects(
    projects: list[str],
    project_configs: dict[str, dict[str, Any]] | None,
) -> list[tuple[dict[str, Any], list[str]]]:
    """Group ``projects`` by the credential config that reaches each one.

    Returns ``[(config, projects), ...]`` in first-seen order. Single-credential
    deployments — the common case — always collapse to one group, so a
    cross-project query stays a single API request.

    Grouping is keyed on the serialized config rather than object identity so it
    survives a round trip through the tool-call boundary.
    """
    configs = project_configs or {}
    grouped: dict[str, tuple[dict[str, Any], list[str]]] = {}
    for project in projects:
        config = configs.get(project, {})
        key = json.dumps(config, sort_keys=True, default=str)
        if key not in grouped:
            grouped[key] = (config, [])
        grouped[key][1].append(project)
    return list(grouped.values())


def resolve_projects(
    requested: str,
    *,
    default_project: str,
    available_projects: list[str] | None,
) -> tuple[list[str], str | None]:
    """Resolve the ``project`` argument to a concrete list of project IDs.

    Returns ``(projects, error)``. ``error`` is non-None only when the caller
    named a project outside the configured set — an unknown name is rejected
    rather than passed through so a hallucinated project ID surfaces as a clear
    message instead of an opaque 403 from Google.

    Accepts an empty value (the default project), ``*``/``all`` (everything
    configured), or a comma-separated list.
    """
    known = list(available_projects or [])
    if default_project and default_project not in known:
        known.insert(0, default_project)

    wanted = (requested or "").strip()
    if not wanted:
        if not default_project:
            return [], "no GCP project is configured; set GCP_PROJECT_ID"
        return [default_project], None

    if wanted.lower() in _ALL_TOKENS:
        if not known:
            return [], "no GCP projects are configured; set GCP_PROJECT_ID"
        return known, None

    names = [part.strip() for part in wanted.split(",") if part.strip()]
    unknown = [name for name in names if name not in known]
    if unknown:
        return [], (
            f"unknown GCP project(s): {', '.join(unknown)}. "
            f"Configured projects: {', '.join(known) or 'none'}. "
            "Call gcp_list_projects to see everything this credential can reach, "
            "and add missing ones to GCP_ADDITIONAL_PROJECTS."
        )
    return names, None


def resource_names(projects: list[str]) -> list[str]:
    """Render project IDs as Cloud Logging ``resourceNames`` entries."""
    return [f"projects/{project}" for project in projects]


#: Cloud Logging: "A maximum of 100 resources may be specified in a single
#: request." Exceeding it fails the whole ``entries.list`` call — the API
#: validates the set, so one project too many loses every project's logs, not
#: just the overflow.
MAX_RESOURCE_NAMES = 100


def resource_name_batches(projects: list[str]) -> list[list[str]]:
    """Split ``projects`` into request-sized ``resourceNames`` batches.

    A single-credential estate of any ordinary size stays one batch and one
    request. Batching exists for the estate that ``GCP_ADDITIONAL_PROJECTS=
    discover`` makes reachable in one line: an org-level grant can resolve
    ``project="*"`` to hundreds of projects, and without this the query that
    needs them most is the one that fails.
    """
    return [
        resource_names(projects[start : start + MAX_RESOURCE_NAMES])
        for start in range(0, len(projects), MAX_RESOURCE_NAMES)
    ]
