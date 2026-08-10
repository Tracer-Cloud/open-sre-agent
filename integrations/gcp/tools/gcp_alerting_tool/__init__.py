"""Read Cloud Monitoring alerting for one metrics scope.

Three read actions over one scope: what fired (``alerts``), why it was
configured to fire (``policies``), and what objective it protects (``slos``).
Read-only throughout — no acknowledge, no silence, no snooze, no policy edit.
That is a deliberate exclusion, not a gap: the failure mode of a wrongly
silenced alert is invisible until the next incident.

All three resources are scoped to a Cloud Monitoring **metrics scope**, so a
project that hosts monitoring for workloads running elsewhere answers for those
workloads too. The resource labels on a returned alert name the project the
workload actually runs in, which is the field that stops "nothing there matches"
being concluded from a query against the observability project.
"""

from __future__ import annotations

import math
from http import HTTPStatus
from typing import Any

from config.constants.gcp import GCP_MONITORING_UNENTITLED_STATUSES
from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.client import (
    MONITORING_API,
    GCPClientError,
    api_not_enabled,
    api_status,
    build_service,
    describe_api_error,
)
from integrations.gcp.projects import resolve_projects
from integrations.gcp.tool_params import config_from, gcp_tool_params
from integrations.gcp.tools.gcp_alerting_tool.alerts import (
    normalize_alert,
    runtime_project_ids,
    select_alerts,
)
from integrations.gcp.tools.gcp_alerting_tool.policies import (
    condition_filters,
    normalize_policy,
)
from integrations.gcp.tools.gcp_alerting_tool.policies import (
    keep_for_name as keep_policy,
)
from integrations.gcp.tools.gcp_alerting_tool.slos import (
    keep_for_name as keep_slo,
)
from integrations.gcp.tools.gcp_alerting_tool.slos import (
    normalize_service,
    normalize_slo,
)

_COMPONENT = "integrations.gcp.tools.gcp_alerting_tool"

ALERTS = "alerts"
POLICIES = "policies"
SLOS = "slos"
_ACTIONS = (ALERTS, POLICIES, SLOS)

_STATES = ("open", "closed", "any")
_DEFAULT_STATE = "open"
_ANY_STATE = "any"

#: Page sizes. ``_MAX_PAGE`` bounds one turn's context; ``_MAX_SERVICES`` bounds
#: the 1+N SLO walk, which costs one round trip per service.
_MAX_PAGE = 200
_MAX_SERVICES = 25
_DEFAULT_LIMIT = 100

#: Maximum pages to fetch when searching for policies by name.
_MAX_POLICY_PAGES = 5

#: 30 days, matching the schema description.
_MAX_HOURS = 24.0 * 30
_DEFAULT_HOURS = 24.0

#: Discovery method ids, used as the Sentry ``method`` tag.
_BUILD_METHOD = "monitoring.discovery.build"
_ALERTS_METHOD = "monitoring.projects.alerts.list"
_POLICIES_LIST_METHOD = "monitoring.projects.alertPolicies.list"
_POLICIES_GET_METHOD = "monitoring.projects.alertPolicies.get"
_SERVICES_METHOD = "monitoring.services.list"
_SLO_METHOD = "monitoring.services.serviceLevelObjectives.list"

#: Long prose extracted from tuple displays to avoid implicit string concatenation.
_USE_CASE_ALERT_PROJECT_WORKLOAD = (
    "An alert names a project: read the alert itself and find which project "
    "and workload it actually fired against"
)

_USE_CASE_CONDITION_THRESHOLD = (
    "Finding the condition and threshold an alert was built on, to re-run it "
    "as a gcp_monitoring_query"
)

_ANTI_EXAMPLE_SPLIT_ESTATE = (
    "Do not pass a list or '*' — this reads one metrics scope per call, and a "
    "scoping project already answers for every project it monitors."
)

_ANTI_EXAMPLE_POLICIES_FIRING = (
    "Do not treat action='policies' as a list of what is firing. It is "
    "configuration: an enabled policy with no open alert is the normal state."
)

_ANTI_EXAMPLE_SLO_BURN = (
    "Do not read action='slos' as current error-budget burn. It returns the "
    "objective's definition; burn is a separate Cloud Monitoring time series."
)

_ANTI_EXAMPLE_RAW_DATA = (
    "Do not use this for raw log lines or metric points — that is "
    "gcp_logging_query and gcp_monitoring_query."
)

#: Long prose lives in module constants: two adjacent literals inside a list or
#: call display are indistinguishable from a missing comma.
_DESCRIPTION = (
    "Read Cloud Monitoring alerting for one metrics scope. action='alerts' "
    "lists alert instances the scope opened, still-open ones included, each "
    "carrying the resource and metric labels that name the project and "
    "workload it fired against. action='policies' returns alert policy "
    "configuration — conditions, filters, thresholds, durations, severity. "
    "action='slos' returns service level objectives and their goals. All "
    "three read the metrics scope, so a project that hosts monitoring for "
    "workloads running elsewhere answers for those workloads too. Read-only: "
    "nothing here acknowledges, silences or edits anything."
)

_USE_CASES = (
    _USE_CASE_ALERT_PROJECT_WORKLOAD,
    _USE_CASE_CONDITION_THRESHOLD,
    "Listing what is currently open in an observability project",
    "Checking whether a policy is disabled or invalid before concluding nothing is wrong",
    "Reading the SLO an alert burns against, with its goal and period",
)

_ANTI_EXAMPLES = (
    _ANTI_EXAMPLE_SPLIT_ESTATE,
    _ANTI_EXAMPLE_POLICIES_FIRING,
    _ANTI_EXAMPLE_SLO_BURN,
    "Do not use this to silence or acknowledge anything. It cannot, and no OpenSRE tool can.",
    _ANTI_EXAMPLE_RAW_DATA,
)

_ACTION_DESCRIPTION = (
    "alerts: alert instances, newest first, with state, open and close times, "
    "severity, and the resource/metric labels that place them. policies: alert "
    "policy configuration. slos: service level objectives per service."
)

_PROJECT_DESCRIPTION = (
    "One GCP project id — a Cloud Monitoring metrics scope. Not a list and not "
    "'*'. When an alert names a project, pass that project: alert policies, "
    "alerts and SLOs live where they were created, which is often a dedicated "
    "observability project rather than where the workload runs. Read "
    "resource_labels.project_id on a returned alert to see which project it "
    "actually fired against. Omit for the default project. Call "
    "gcp_metrics_scope to see what a scope covers."
)

_HOURS_DESCRIPTION = (
    "alerts only. Lookback window. An alert counts as in-window if it is still "
    "open, or if it opened or closed inside it. Capped at 30 days."
)

_NAME_DESCRIPTION = (
    "Case-insensitive substring of the alert policy display name (actions "
    "alerts and policies) or of the SLO or service display name (action slos)."
)

_POLICY_ID_DESCRIPTION = (
    "action='policies' only. Fetch one policy by the policy_id an alert "
    "reported, instead of listing them all."
)

_NO_OPEN_ALERTS_NOTE = (
    "No open alerts in this scope for the window. Widen hours, or set "
    "state='any' to see what closed."
)

_ALL_DISABLED_NOTE = "Every matching policy is disabled, so nothing here can fire."

#: The same finding, hedged, when the walk stopped before the estate ran out.
#: "Every matching policy is disabled" is a claim about the whole estate, and a
#: capped search has not seen the whole estate.
_SOME_DISABLED_NOTE = (
    "Every policy found so far is disabled, so none of them can fire — but the "
    "search was capped before the whole estate was covered."
)

_SLO_NOTE = (
    "SLO configuration only. Current error-budget burn is a separate Cloud "
    "Monitoring time series; resource_name is the SLO identifier you need to "
    "query it."
)


def _cross_project_note(ids: list[str]) -> str:
    """Prose for alerts that fired against a project other than the one queried."""
    return (
        "These alerts fired against project(s) other than the one queried: "
        f"{', '.join(ids)}. That is the metrics scope working as intended — the "
        "workload runs there, monitoring lives here."
    )


def _capped_search_message(pages: int) -> str:
    """Prose for a name search that ran out of pages before it ran out of estate."""
    return f"Search was capped after {pages} pages and did not cover the whole estate."


def _disabled_api_message(target: str) -> str:
    """Prose for a project that never switched Cloud Monitoring on."""
    return (
        f"the Cloud Monitoring API is not enabled in {target}. Enable "
        "monitoring.googleapis.com there, or ask about a different project."
    )


#: Per action: what the caller asked for, and what is still worth trying. Every
#: action has a sibling that answers part of the same question, so a refusal
#: names it rather than ending the investigation there.
_UNENTITLED_PROSE = {
    ALERTS: (
        "alert instances",
        "Alert policy configuration is still readable with action='policies'.",
    ),
    POLICIES: (
        "alert policy configuration",
        "Alerts that already fired are still readable with action='alerts'.",
    ),
    SLOS: (
        "service level objectives",
        "Alert policy configuration is still readable with action='policies'.",
    ),
}


def _unentitled_message(target: str, exc: Exception, action: str) -> str:
    """Prose for a read the caller's grants do not reach."""
    subject, fallback = _UNENTITLED_PROSE[action]
    return (
        f"Cloud Monitoring did not serve {subject} for {target} "
        f"({describe_api_error(exc)}). {fallback}"
    )


def _missing_policy_note(target: str, policy_id: str) -> str:
    """Prose for a ``policy_id`` that no longer resolves in the queried project."""
    return (
        f"No alert policy {policy_id} in {target}. Cloud Monitoring answered, so "
        "the read itself worked — the policy was most likely deleted after the "
        "alert that named it fired. Drop policy_id to list what does exist."
    )


def _too_many_projects_message(count: int) -> str:
    """Prose for a caller that asked for more than one scope at a time."""
    return (
        f"gcp_alerting reads one metrics scope per call; got {count}. A scope "
        "already covers every project it monitors."
    )


def _empty_results() -> dict[str, Any]:
    """The three result keys, all empty.

    Present on every path so a consumer that asked for one action never
    key-errors reading the shape of another.
    """
    return {"alerts": [], "policies": [], "slos": []}


def _unavailable(error: str, **extra: Any) -> dict[str, Any]:
    """Unavailable envelope carrying all three result keys."""
    return tool_unavailable("gcp", error, **_empty_results(), **extra)


def _no_such_policy(target: str, policy_id: str) -> dict[str, Any]:
    """A ``policy_id`` the caller asked for that Cloud Monitoring does not hold.

    Shaped like a successful empty listing rather than an unavailability, because
    that is what happened: the API was reached, the project was reachable, and
    the answer is "there is no such policy".
    """
    return {
        "found": True,
        "project": target,
        "action": POLICIES,
        "policy_id": policy_id,
        "policy_count": 0,
        "policies": [],
        "condition_filters": [],
        "truncated": False,
        "total_size": 0,
        "note": _missing_policy_note(target, policy_id),
        "alerts": [],
        "slos": [],
    }


def _failed(error: str, target: str, action: str) -> dict[str, Any]:
    """Failure envelope carrying all three result keys."""
    return {
        "found": False,
        "error": error,
        "project": target,
        "action": action,
        **_empty_results(),
    }


def _as_int(value: Any, default: int) -> int:
    """Coerce a model-supplied integer, falling back rather than raising.

    ``OverflowError`` is in the net because ``int(float("inf"))`` raises it
    rather than ``ValueError``, and a model can put ``Infinity`` in JSON.
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _window_hours(value: Any) -> float:
    """Clamp the lookback to (0, 30 days], falling back rather than raising.

    ``OverflowError`` is in the net for the same reason as in :func:`_as_int`,
    from the other direction: ``json.loads`` yields an arbitrary-precision
    ``int`` for a long numeric literal, and ``float()`` refuses one too large to
    represent. ``hours`` is a model-facing parameter, so that input is reachable.
    """
    try:
        hours = float(value)
    except (TypeError, ValueError, OverflowError):
        return _DEFAULT_HOURS
    # NaN fails every comparison, so it slips past both the ``<= 0`` guard and
    # ``min``, and only surfaces as an uncaught error when ``timedelta`` refuses
    # it several frames away. Reject it here, where the fallback still means
    # something. ``inf`` needs no special case — ``min`` reads it as "as far
    # back as you will go", which is what the caller asked for.
    if math.isnan(hours) or hours <= 0:
        return _DEFAULT_HOURS
    return min(hours, _MAX_HOURS)


@tool(
    name="gcp_alerting",
    display_name="Cloud Monitoring alerting",
    source="gcp",
    description=_DESCRIPTION,
    use_cases=list(_USE_CASES),
    anti_examples=list(_ANTI_EXAMPLES),
    surfaces=("investigation", "action"),
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_ACTIONS),
                "default": ALERTS,
                "description": _ACTION_DESCRIPTION,
            },
            # Deliberately not PROJECT_PROPERTY: it advertises '*' and comma
            # lists. All three resources are scope-scoped, so a fan-out would
            # issue N calls and return each alert once per scope containing it.
            "project": {
                "type": "string",
                "default": "",
                "description": _PROJECT_DESCRIPTION,
            },
            "state": {
                "type": "string",
                "enum": list(_STATES),
                "default": _DEFAULT_STATE,
                "description": "alerts only. Filter by alert state.",
            },
            "hours": {
                "type": "number",
                "default": _DEFAULT_HOURS,
                "description": _HOURS_DESCRIPTION,
            },
            "name_contains": {
                "type": "string",
                "default": "",
                "description": _NAME_DESCRIPTION,
            },
            "policy_id": {
                "type": "string",
                "default": "",
                "description": _POLICY_ID_DESCRIPTION,
            },
            "limit": {"type": "integer", "default": _DEFAULT_LIMIT},
        },
        "required": [],
    },
    is_available=gcp_available,
    extract_params=gcp_tool_params,
)
def gcp_alerting(
    action: str = ALERTS,
    project: str = "",
    state: str = _DEFAULT_STATE,
    hours: float = _DEFAULT_HOURS,
    name_contains: str = "",
    policy_id: str = "",
    limit: int = _DEFAULT_LIMIT,
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
    # ``gcp_tool_params`` injects a ``limit`` from the instance's ``max_results``
    # (default 100). It is deliberately not in ``injected_params`` because it is
    # a model-facing selector, so ``tc.input`` overrides it when the model
    # supplies one. When the model is silent the effective limit is
    # ``max_results``, not the schema default — keep the two in step.
    **_injected: Any,
) -> dict[str, Any]:
    """Read alert instances, alert policies or SLOs for one metrics scope."""
    wanted = (action or ALERTS).strip()
    if wanted not in _ACTIONS:
        return _unavailable(f"unknown action '{action}'; valid values: {', '.join(_ACTIONS)}")

    wanted_state = (state or _DEFAULT_STATE).strip().lower()
    if wanted_state not in _STATES:
        return _unavailable(f"unknown state '{state}'; valid values: {', '.join(_STATES)}")

    projects, error = resolve_projects(
        project, default_project=default_project, available_projects=available_projects
    )
    if error:
        return _unavailable(error)
    # One metrics scope per call. Refuse rather than silently reading only the
    # first: a narrowed answer here reads as authoritative and is not.
    if len(projects) > 1:
        return _unavailable(_too_many_projects_message(len(projects)))

    target = projects[0]
    page = max(1, min(_as_int(limit, _DEFAULT_LIMIT), _MAX_PAGE))

    try:
        config = config_from((project_configs or {}).get(target), fallback_project=target)
        service = build_service(config, MONITORING_API)
    except GCPClientError as exc:
        return _unavailable(str(exc))
    except Exception as exc:
        report_run_error(
            exc,
            tool_name="gcp_alerting",
            source="gcp",
            component=_COMPONENT,
            method=_BUILD_METHOD,
            extras={"project": target, "action": wanted},
        )
        return _failed(describe_api_error(exc), target, wanted)

    if wanted == ALERTS:
        return _read_alerts(service, target, wanted_state, hours, name_contains, page)
    if wanted == POLICIES:
        return _read_policies(service, target, name_contains, policy_id, page)
    return _read_slos(service, target, name_contains, page)


def _read_alerts(
    service: Any,
    target: str,
    state: str,
    hours: Any,
    name_contains: str,
    limit: int,
) -> dict[str, Any]:
    """List alert instances and filter them client-side."""
    window = _window_hours(hours)
    # A state filter discards rows, so scan wider than the limit or it starves.
    # Capped so one turn cannot pull an unbounded page.
    scan = max(1, min(limit * 4 if state != _ANY_STATE else limit, _MAX_PAGE))

    try:
        # No server-side ``filter``. It matches "any fields belonging to the
        # alert or its subfields", loose enough that a wrong expression returns
        # silently empty instead of a 400 — and a silently empty alert list is
        # the exact failure this tool exists to fix.
        # Server already defaults to openTime desc, so no orderBy needed.
        payload = (
            service.projects().alerts().list(parent=f"projects/{target}", pageSize=scan).execute()
        )
    except Exception as exc:
        if api_not_enabled(exc):
            return _unavailable(
                _disabled_api_message(target),
                api_enabled=False,
                project=target,
                action=ALERTS,
            )
        if api_status(exc) in GCP_MONITORING_UNENTITLED_STATUSES:
            # Alert instances are served by a newer endpoint than alert
            # policies. An estate that does not serve them is a capability gap,
            # not a fault, and telemetry here would fire on every turn.
            return _unavailable(
                _unentitled_message(target, exc, ALERTS),
                api_enabled=False,
                project=target,
                action=ALERTS,
            )
        report_run_error(
            exc,
            tool_name="gcp_alerting",
            source="gcp",
            component=_COMPONENT,
            method=_ALERTS_METHOD,
            severity="warning",
            extras={"project": target, "state": state},
        )
        return _failed(describe_api_error(exc), target, ALERTS)

    raw = payload.get("alerts") or []
    scanned = len(raw)
    normalized = [normalize_alert(entry) for entry in raw if isinstance(entry, dict)]
    matched = select_alerts(normalized, state, window, name_contains)
    # Counted before the slice: reporting the truncated length as the total is
    # how a caller concludes "only 2 fired" from 40 that did.
    matched_count = len(matched)
    rows = matched[:limit]

    runtime_ids = runtime_project_ids(rows)
    elsewhere = [project_id for project_id in runtime_ids if project_id != target]

    if elsewhere:
        note = _cross_project_note(elsewhere)
    elif matched_count == 0 and state == _DEFAULT_STATE:
        note = _NO_OPEN_ALERTS_NOTE
    else:
        note = ""

    return {
        "found": True,
        "project": target,
        "action": ALERTS,
        "state": state,
        "window_hours": window,
        "scanned": scanned,
        # The server page filled up, so matches older than the oldest row here
        # may exist. Distinct from the client-side slice.
        "truncated": bool(payload.get("nextPageToken")),
        "total_size": int(payload.get("totalSize", 0) or 0),
        "alert_count": matched_count,
        "alerts": rows,
        "runtime_project_ids": runtime_ids,
        "note": note,
        "policies": [],
        "slos": [],
    }


def _read_policies(
    service: Any,
    target: str,
    name_contains: str,
    policy_id: str,
    limit: int,
) -> dict[str, Any]:
    """Read alert policy configuration, either all of it or one policy by id."""
    wanted_id = (policy_id or "").strip()
    policies_resource = service.projects().alertPolicies()
    page_count = 0

    try:
        if wanted_id:
            # The id an alert reported, handed straight back. Without this the
            # alerts → policies loop has no way to close.
            payload = {
                "alertPolicies": [
                    policies_resource.get(
                        name=f"projects/{target}/alertPolicies/{wanted_id}"
                    ).execute()
                ]
            }
            raw = payload.get("alertPolicies") or []
            normalized = [normalize_policy(entry) for entry in raw if isinstance(entry, dict)]
            matched = [policy for policy in normalized if keep_policy(policy, name_contains)]
            matched_count = len(matched)
            rows = matched[:limit]
            truncated = False
            total_size = 0
        else:
            # ``name_contains`` is applied client-side, so a match can sit on a
            # later page. Walk up to ``_MAX_POLICY_PAGES`` rather than declaring
            # a policy absent on the strength of page one.
            #
            # A *search* pages at full width; a plain listing pages at ``limit``.
            # Coupling the two would let "show me 5 policies matching latency"
            # scan 25 rows instead of 1000 and still report the same "capped
            # after 5 pages" note — search depth is not the reader's display
            # width to spend. ``limit`` is already clamped to ``_MAX_PAGE``.
            page_size = _MAX_PAGE if name_contains else limit
            matched = []
            token = None
            total_size = 0

            while page_count < _MAX_POLICY_PAGES and len(matched) < limit:
                list_kwargs = {"name": f"projects/{target}", "pageSize": page_size}
                if token:
                    list_kwargs["pageToken"] = token

                payload = policies_resource.list(**list_kwargs).execute()
                raw = payload.get("alertPolicies") or []
                # Keep the first estate size we are told. Google is not obliged
                # to repeat ``totalSize`` on a continuation page, and a plain
                # reassignment would report an estate of zero policies on the
                # strength of a field the second page simply left out.
                total_size = total_size or int(payload.get("totalSize", 0) or 0)

                normalized = [normalize_policy(entry) for entry in raw if isinstance(entry, dict)]
                page_matched = [
                    policy for policy in normalized if keep_policy(policy, name_contains)
                ]
                matched.extend(page_matched)

                token = payload.get("nextPageToken")
                page_count += 1

                if not token:
                    break

            truncated = bool(token)
            matched_count = len(matched)
            rows = matched[:limit]
    except Exception as exc:
        if api_not_enabled(exc):
            return _unavailable(
                _disabled_api_message(target),
                api_enabled=False,
                project=target,
                action=POLICIES,
            )
        # A 404 on the single-policy path is an answer, not an entitlement gap.
        # Ranked above the unentitled branch because that branch would report
        # api_enabled=False and prose about grants for what is a stale reference
        # — and this path exists precisely to close the alerts → policies loop,
        # where an alert naming a since-deleted policy is the ordinary case.
        if wanted_id and api_status(exc) == HTTPStatus.NOT_FOUND:
            return _no_such_policy(target, wanted_id)
        # A denied or unserved read is the operator's IAM posture, not a fault.
        # Reporting it files one Sentry event per turn for as long as the grant
        # stays as it is — the Rootly On-Call 403 and the Kubernetes namespace
        # 403 both had to learn this.
        if api_status(exc) in GCP_MONITORING_UNENTITLED_STATUSES:
            return _unavailable(
                _unentitled_message(target, exc, POLICIES),
                api_enabled=False,
                project=target,
                action=POLICIES,
            )
        report_run_error(
            exc,
            tool_name="gcp_alerting",
            source="gcp",
            component=_COMPONENT,
            method=_POLICIES_GET_METHOD if wanted_id else _POLICIES_LIST_METHOD,
            severity="warning",
            extras={"project": target, "policy_id": wanted_id},
        )
        return _failed(describe_api_error(exc), target, POLICIES)

    # Read over every match, not over ``rows``: ``rows`` is the display slice,
    # so a page of disabled policies would otherwise claim the whole estate is
    # dark while an enabled one sits just past ``limit``.
    disabled_only = bool(matched) and not any(policy["enabled"] for policy in matched)
    # A capped *search* is the only case worth a note. Without ``name_contains``
    # the loop stops because the caller's ``limit`` was reached, and the
    # ``truncated`` field already says that in a form a caller can branch on.
    #
    # ``truncated`` alone is not enough: it means "the server had more", which is
    # also true when a search filled ``limit`` on page one. Saying "capped after
    # 1 pages" there blames the page budget for a stop the caller's own display
    # width caused. Only the page cap running out is a capped search.
    capped_search = (
        bool(name_contains) and not wanted_id and truncated and page_count >= _MAX_POLICY_PAGES
    )

    if disabled_only and capped_search:
        note = _SOME_DISABLED_NOTE
    elif disabled_only:
        note = _ALL_DISABLED_NOTE
    elif capped_search:
        # Say so on every capped search, not only the empty ones: a caller that
        # got three hits out of five pages is just as entitled to know the
        # estate was not fully covered.
        note = _capped_search_message(page_count)
    else:
        note = ""

    return {
        "found": True,
        "project": target,
        "action": POLICIES,
        # Echoed back so a caller closing the alerts → policies loop can see
        # which id this answer is about, empty string on the listing path.
        "policy_id": wanted_id,
        "policy_count": matched_count,
        "policies": rows,
        "condition_filters": condition_filters(rows),
        "truncated": truncated,
        "total_size": total_size,
        "note": note,
        "alerts": [],
        "slos": [],
    }


def _read_slos(
    service: Any,
    target: str,
    name_contains: str,
    limit: int,
) -> dict[str, Any]:
    """Walk the project's services and read each one's objectives.

    Deliberately 1+N rather than the ``services/-`` wildcard: the discovery
    document documents that wildcard only under the ``workspaces/`` form, and a
    wildcard that silently returns nothing is worse than N calls that work.
    """
    services_resource = service.services()

    try:
        services_payload = services_resource.list(
            parent=f"projects/{target}", pageSize=_MAX_SERVICES
        ).execute()
    except Exception as exc:
        if api_not_enabled(exc):
            return _unavailable(
                _disabled_api_message(target),
                api_enabled=False,
                project=target,
                action=SLOS,
            )
        # Same reasoning as the alerts path: a grant the caller does not hold is
        # the operator's IAM posture, and telemetry here fires once per turn for
        # as long as that posture stands.
        if api_status(exc) in GCP_MONITORING_UNENTITLED_STATUSES:
            return _unavailable(
                _unentitled_message(target, exc, SLOS),
                api_enabled=False,
                project=target,
                action=SLOS,
            )
        report_run_error(
            exc,
            tool_name="gcp_alerting",
            source="gcp",
            component=_COMPONENT,
            method=_SERVICES_METHOD,
            severity="warning",
            extras={"project": target},
        )
        return _failed(describe_api_error(exc), target, SLOS)

    raw_services = [
        entry for entry in services_payload.get("services") or [] if isinstance(entry, dict)
    ]
    services_truncated = bool(services_payload.get("nextPageToken")) or (
        len(raw_services) >= _MAX_SERVICES
    )

    slos: list[dict[str, Any]] = []
    partial_errors: list[str] = []
    slos_truncated = False

    for entry in raw_services:
        summary = normalize_service(entry)
        resource_name = summary["resource_name"]
        if not resource_name:
            continue
        try:
            objectives = (
                services_resource.serviceLevelObjectives()
                .list(parent=resource_name, view="FULL", pageSize=limit)
                .execute()
            )
        except Exception as exc:
            # One service the caller cannot read must not discard the rest —
            # the same per-target degradation gcp_error_reporting_top_errors
            # applies across projects. A denial still lands in partial_errors
            # so the reader sees the gap; it just does not raise a Sentry event
            # every turn for a grant nobody intends to widen.
            if api_status(exc) not in GCP_MONITORING_UNENTITLED_STATUSES:
                report_run_error(
                    exc,
                    tool_name="gcp_alerting",
                    source="gcp",
                    component=_COMPONENT,
                    method=_SLO_METHOD,
                    severity="warning",
                    extras={"project": target, "service": resource_name},
                )
            label = summary["display_name"] or resource_name
            partial_errors.append(f"{label}: {describe_api_error(exc)}")
            continue

        # Check if this service's SLOs were truncated
        if objectives.get("nextPageToken"):
            slos_truncated = True

        for objective in objectives.get("serviceLevelObjectives") or []:
            if isinstance(objective, dict):
                slos.append(normalize_slo(objective, summary))

    matched = [slo for slo in slos if keep_slo(slo, name_contains)]

    return {
        "found": True,
        "project": target,
        "action": SLOS,
        "service_count": len(raw_services),
        "services_truncated": services_truncated,
        "slos_truncated": slos_truncated,
        # Counted before the slice, for the same reason as alert_count.
        "slo_count": len(matched),
        "slos": matched[:limit],
        "partial_errors": partial_errors,
        "note": _SLO_NOTE,
        "alerts": [],
        "policies": [],
    }
