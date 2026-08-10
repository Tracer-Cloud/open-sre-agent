"""``gcp_metrics_scope``: scope resolution, project-number mapping, degradation.

The failure this tool exists to prevent is concluding "that project has nothing
in it" from a query against the wrong one, so the tests that matter most are the
ones pinning *which* project is reported and *which* API version is called —
``monitoring/v3`` has no ``metricsScopes`` resource at all.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

import pytest

from integrations.gcp.client import MONITORING_API, MONITORING_SCOPE_API, RESOURCE_MANAGER_API
from integrations.gcp.tools import gcp_metrics_scope_tool
from integrations.gcp.tools.gcp_metrics_scope_tool.scopes import (
    normalize_monitored,
    normalize_scopes,
    scope_resource_name,
)
from tools.registry import get_registered_tool

_SCOPING_NUMBER = "100000000001"
_WORKLOAD_NUMBER = "200000000002"
_SCOPING_ID = "obs-project"
_WORKLOAD_ID = "workload-a"

_SCOPE_NAME = f"locations/global/metricsScopes/{_SCOPING_NUMBER}"

_RM_PROJECTS = {
    "projects": [
        {
            "projectId": _SCOPING_ID,
            "projectNumber": _SCOPING_NUMBER,
            "lifecycleState": "ACTIVE",
        },
        {
            "projectId": _WORKLOAD_ID,
            "projectNumber": _WORKLOAD_NUMBER,
            "lifecycleState": "ACTIVE",
        },
    ]
}


# --- fakes -------------------------------------------------------------------


class _GoogleApiError(Exception):
    """A ``googleapiclient``-shaped error: an HTTP status plus a JSON body.

    ``api_not_enabled`` reads ``ErrorInfo.reason`` out of that body and nothing
    else, so a fake that carries only a message cannot exercise the split
    between a disabled API and a genuine denial — which is the split A13 and
    A14 exist to pin.
    """

    def __init__(self, status: HTTPStatus, reason: str = "") -> None:
        super().__init__(f"HTTP {int(status)} from the Google API")
        self.status_code = int(status)
        details = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}]
        self.content = json.dumps(
            {
                "error": {
                    "code": int(status),
                    "message": "the caller does not have permission",
                    "details": details if reason else [],
                }
            }
        ).encode("utf-8")


class _Executable:
    """A discovery request object: ``execute()`` returns a page or raises."""

    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self._payload = payload

    def execute(self) -> dict[str, Any]:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeMetricsScopes:
    def __init__(
        self,
        calls: list[tuple[str, dict[str, Any]]],
        forward: dict[str, Any] | Exception,
        reverse: dict[str, Any] | Exception,
    ) -> None:
        self._calls = calls
        self._forward = forward
        self._reverse = reverse

    def get(self, name: str) -> _Executable:
        self._calls.append(("get", {"name": name}))
        return _Executable(self._forward)

    def listMetricsScopesByMonitoredProject(  # noqa: N802 — Google API method name
        self,
        monitoredResourceContainer: str,  # noqa: N803 — Google API kwarg
    ) -> _Executable:
        self._calls.append(
            ("listMetricsScopesByMonitoredProject", {"container": monitoredResourceContainer})
        )
        return _Executable(self._reverse)


class _FakeGlobal:
    def __init__(self, scopes: _FakeMetricsScopes) -> None:
        self._scopes = scopes

    def metricsScopes(self) -> _FakeMetricsScopes:  # noqa: N802 — Google API method name
        return self._scopes


class _FakeLocations:
    """Exposes ``global_`` and nothing else.

    ``global`` is a Python keyword, so googleapiclient's ``fix_method_name``
    renames the resource. A plain class raises ``AttributeError`` for
    ``getattr(obj, "global")``, which is what turns A3 into an assertion rather
    than a comment.
    """

    def __init__(self, scopes: _FakeMetricsScopes) -> None:
        self._scopes = scopes

    def global_(self) -> _FakeGlobal:
        return _FakeGlobal(self._scopes)


class _FakeScopeService:
    def __init__(self, scopes: _FakeMetricsScopes) -> None:
        self._scopes = scopes

    def locations(self) -> _FakeLocations:
        return _FakeLocations(self._scopes)


class _FakeRMProjects:
    """Serves Resource Manager pages in request order, recording each request.

    ``**kwargs`` rather than a fixed signature so that a ``pageToken=`` on the
    second request is *recorded* and asserted on, instead of raising a
    TypeError that reads like a broken fake.
    """

    _LABEL = "rm.projects.list"

    def __init__(
        self,
        pages: list[dict[str, Any]] | Exception,
        calls: list[tuple[str, dict[str, Any]]],
    ) -> None:
        self._pages = pages
        self._calls = calls

    def list(self, **kwargs: Any) -> _Executable:
        served = sum(1 for call in self._calls if call[0] == self._LABEL)
        self._calls.append((self._LABEL, dict(kwargs)))
        if isinstance(self._pages, Exception):
            return _Executable(self._pages)
        if served >= len(self._pages):
            return _Executable({"projects": []})
        return _Executable(self._pages[served])


class _FakeRMService:
    def __init__(
        self,
        pages: list[dict[str, Any]] | Exception,
        calls: list[tuple[str, dict[str, Any]]],
    ) -> None:
        self._pages = pages
        self._calls = calls

    def projects(self) -> _FakeRMProjects:
        return _FakeRMProjects(self._pages, self._calls)


class _Harness:
    """Stands in for ``build_service``, recording which APIs were asked for."""

    def __init__(
        self,
        forward: dict[str, Any] | Exception,
        reverse: dict[str, Any] | Exception,
        rm: dict[str, Any] | list[dict[str, Any]] | Exception,
        rm_build_fails: bool,
    ) -> None:
        self.apis: list[tuple[str, str]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._scopes = _FakeMetricsScopes(self.calls, forward, reverse)
        self._rm = rm if isinstance(rm, Exception | list) else [rm]
        self._rm_build_fails = rm_build_fails

    def build_service(self, _config: Any, api: tuple[str, str]) -> Any:
        self.apis.append(api)
        if api == MONITORING_SCOPE_API:
            return _FakeScopeService(self._scopes)
        if api == RESOURCE_MANAGER_API:
            if self._rm_build_fails and isinstance(self._rm, Exception):
                raise self._rm
            return _FakeRMService(self._rm, self.calls)
        raise AssertionError(f"the tool asked for an API it must never build: {api}")


class _Reports:
    """Records every ``report_run_error`` the tool makes, with its severity."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, exc: BaseException, **kwargs: Any) -> None:
        self.calls.append({"exc": exc, **kwargs})


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    forward: dict[str, Any] | Exception | None = None,
    reverse: dict[str, Any] | Exception | None = None,
    rm: dict[str, Any] | list[dict[str, Any]] | Exception | None = None,
    rm_build_fails: bool = False,
) -> _Harness:
    harness = _Harness(
        forward or {},
        reverse or {},
        _RM_PROJECTS if rm is None else rm,
        rm_build_fails,
    )
    monkeypatch.setattr(gcp_metrics_scope_tool, "build_service", harness.build_service)
    return harness


def _record_reports(monkeypatch: pytest.MonkeyPatch) -> _Reports:
    reports = _Reports()
    monkeypatch.setattr(gcp_metrics_scope_tool, "report_run_error", reports)
    return reports


def _monitored(*numbers: str) -> dict[str, Any]:
    return {
        "name": _SCOPE_NAME,
        "monitoredProjects": [{"name": f"projects/{number}"} for number in numbers],
    }


def _call(**kwargs: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "default_project": _SCOPING_ID,
        "available_projects": [_SCOPING_ID, _WORKLOAD_ID],
    }
    payload.update(kwargs)
    return gcp_metrics_scope_tool.gcp_metrics_scope(**payload)


# --- A1–A4: registration, API version, keyword-safe resource, path shape -----


def test_tool_is_registered_and_project_is_not_injected() -> None:
    registered = get_registered_tool("gcp_metrics_scope")

    assert registered is not None
    assert registered.source == "gcp"
    # Injected params override model input. Protecting `project` is exactly how
    # the Kubernetes `context` parameter ended up inert.
    assert "project" not in (registered.injected_params or ())


def test_the_scope_client_is_built_on_monitoring_v1_not_v3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install(monkeypatch, forward=_monitored(_SCOPING_NUMBER))

    result = _call(project=_SCOPING_ID)

    assert result["found"] is True
    assert harness.apis[0] == MONITORING_SCOPE_API
    # v3 exposes no metricsScopes resource, so "tidying" the constant 404s
    # every call.
    assert MONITORING_API not in harness.apis


def test_the_global_resource_is_reached_through_the_keyword_safe_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install(monkeypatch, forward=_monitored(_SCOPING_NUMBER))

    result = _call(project=_SCOPING_ID)

    # The fake exposes `global_` only, so reaching the resource any other way
    # raises AttributeError before a request is ever built.
    assert result["found"] is True
    # And the v1 path has to survive the trip to the call site: the helper being
    # correct says nothing about the request actually carrying what it returned.
    assert harness.calls[0] == ("get", {"name": scope_resource_name(_SCOPING_ID)})


def test_scope_resource_name_is_the_v1_path() -> None:
    # The discovery document constrains this to
    # ^locations/global/metricsScopes/[^/]+$; a projects/{p} path never leaves
    # the client.
    assert scope_resource_name(_SCOPING_ID) == f"locations/global/metricsScopes/{_SCOPING_ID}"


# --- A5–A9: monitored_projects ------------------------------------------------


def test_monitored_projects_are_reported_with_numbers_and_resolved_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, forward=_monitored(_SCOPING_NUMBER, _WORKLOAD_NUMBER))

    result = _call(project=_SCOPING_ID)

    assert result["project_ids_resolved"] is True
    assert [row["project_number"] for row in result["monitored_projects"]] == [
        _SCOPING_NUMBER,
        _WORKLOAD_NUMBER,
    ]
    # A bare number is useless: no other GCP tool takes one.
    assert [row["project_id"] for row in result["monitored_projects"]] == [
        _SCOPING_ID,
        _WORKLOAD_ID,
    ]


@pytest.mark.parametrize("failure", ["build", "list"])
def test_ids_are_blank_and_flagged_when_resource_manager_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    sentry_events: list[BaseException],
    failure: str,
) -> None:
    # Both guards are pinned: the one around building the Resource Manager
    # client, and the one inside project_id_index around the listing itself.
    _install(
        monkeypatch,
        forward=_monitored(_SCOPING_NUMBER, _WORKLOAD_NUMBER),
        rm=_GoogleApiError(HTTPStatus.FORBIDDEN, reason="IAM_PERMISSION_DENIED"),
        rm_build_fails=failure == "build",
    )

    result = _call(project=_SCOPING_ID)

    # resourcemanager.projects.list is a permission a perfectly good Monitoring
    # principal often lacks. The numbers are still the answer.
    assert result["found"] is True
    assert result["project_ids_resolved"] is False
    assert [row["project_id"] for row in result["monitored_projects"]] == ["", ""]
    assert sentry_events == []


def test_a_self_only_scope_is_reported_as_self_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, forward=_monitored(_SCOPING_NUMBER))

    result = _call(project=_SCOPING_ID)

    # The co-located topology. Reading this as "found nothing" is the exact
    # wrong conclusion.
    assert result["self_scoped"] is True
    assert result["monitored_project_count"] == 1
    assert "only itself" in result["note"]


def test_the_scoping_project_is_identified_inside_its_own_monitored_list() -> None:
    rows = normalize_monitored(
        [{"name": f"projects/{_SCOPING_NUMBER}"}, {"name": f"projects/{_WORKLOAD_NUMBER}"}],
        _SCOPING_NUMBER,
    )

    assert [row["is_scoping_project"] for row in rows] == [True, False]


def test_monitored_projects_outside_the_configured_scope_are_named_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, forward=_monitored(_SCOPING_NUMBER, _WORKLOAD_NUMBER))

    result = gcp_metrics_scope_tool.gcp_metrics_scope(
        project=_SCOPING_ID,
        default_project=_SCOPING_ID,
        available_projects=[_SCOPING_ID],
    )

    # Without this the operator is never told what to add to the allow-list.
    assert result["unconfigured_project_ids"] == [_WORKLOAD_ID]
    assert result["queryable_project_ids"] == []
    assert "GCP_ADDITIONAL_PROJECTS" in result["note"]


# --- A10–A12: containing_scopes ----------------------------------------------


def test_containing_scopes_marks_only_the_first_entry_as_self() -> None:
    # The API documents it: the scope representing the specified monitored
    # project is always the first entry.
    rows = normalize_scopes(
        [
            {"name": f"locations/global/metricsScopes/{_WORKLOAD_NUMBER}"},
            {"name": f"locations/global/metricsScopes/{_SCOPING_NUMBER}"},
        ]
    )

    assert [row["is_self"] for row in rows] == [True, False]


def test_containing_scopes_names_the_observability_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        reverse={
            "metricsScopes": [
                {"name": f"locations/global/metricsScopes/{_WORKLOAD_NUMBER}"},
                {"name": f"locations/global/metricsScopes/{_SCOPING_NUMBER}"},
            ]
        },
    )

    result = _call(project=_WORKLOAD_ID, direction="containing_scopes")

    # The project's own scope is not an observability project for itself.
    assert result["observability_project_ids"] == [_SCOPING_ID]


def test_containing_scopes_calls_the_reverse_method_with_a_project_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install(monkeypatch, reverse={"metricsScopes": []})

    _call(project=_WORKLOAD_ID, direction="containing_scopes")

    method, kwargs = harness.calls[0]
    assert method == "listMetricsScopesByMonitoredProject"
    assert kwargs["container"] == f"projects/{_WORKLOAD_ID}"


# --- A13–A16: degradation, refusal, response shape ---------------------------


def test_a_disabled_monitoring_api_degrades_without_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    sentry_events: list[BaseException],
) -> None:
    _install(
        monkeypatch,
        forward=_GoogleApiError(HTTPStatus.FORBIDDEN, reason="SERVICE_DISABLED"),
    )

    result = _call(project=_SCOPING_ID)

    # Most projects in a large estate never enabled Monitoring. Reporting each
    # one files a Sentry error per turn, forever.
    assert result["available"] is False
    assert result["api_enabled"] is False
    assert sentry_events == []


def test_a_real_permission_denial_is_still_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        forward=_GoogleApiError(HTTPStatus.FORBIDDEN, reason="IAM_PERMISSION_DENIED"),
    )
    reports = _record_reports(monkeypatch)

    result = _call(project=_SCOPING_ID)

    # Over-broad degradation would swallow the denial an operator must fix.
    assert result["found"] is False
    assert len(reports.calls) == 1
    assert reports.calls[0]["severity"] == "warning"


def test_more_than_one_project_is_refused_rather_than_silently_narrowed() -> None:
    result = _call(project=f"{_SCOPING_ID},{_WORKLOAD_ID}")

    # Reading only the first would produce an answer that reads as
    # authoritative and is not.
    assert result["available"] is False
    assert "one metrics scope per call" in result["error"]


@pytest.mark.parametrize("scenario", ["success", "disabled", "denial", "unknown_direction"])
def test_both_result_keys_are_present_on_every_path(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    _record_reports(monkeypatch)
    if scenario == "success":
        _install(monkeypatch, forward=_monitored(_SCOPING_NUMBER))
    elif scenario == "disabled":
        _install(
            monkeypatch,
            forward=_GoogleApiError(HTTPStatus.FORBIDDEN, reason="SERVICE_DISABLED"),
        )
    elif scenario == "denial":
        _install(monkeypatch, forward=_GoogleApiError(HTTPStatus.NOT_FOUND))

    direction = "sideways" if scenario == "unknown_direction" else "monitored_projects"
    result = _call(project=_SCOPING_ID, direction=direction)

    # A consumer must never key-error on the other direction's key.
    assert "monitored_projects" in result
    assert "containing_scopes" in result


# --- A16–A18: the Resource Manager listing is paged, and a cap is not a denial


def _rm_page(project_id: str, number: str, *, token: str = "") -> dict[str, Any]:
    """One Resource Manager page holding a single ACTIVE project."""
    page: dict[str, Any] = {
        "projects": [{"projectId": project_id, "projectNumber": number, "lifecycleState": "ACTIVE"}]
    }
    if token:
        page["nextPageToken"] = token
    return page


def test_a_project_on_the_second_listing_page_still_resolves_to_an_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading only page one blanks the id of every project after it.

    ``project_discovery.discover`` already pages this exact call; the scope tool
    copied the request and dropped the ``nextPageToken``. Driven through the
    tool, not the helper, because the defect was that the *caller* stopped at
    one page — a helper-level test passes either way.
    """
    # Arrange: the monitored workload is only listed on page two.
    harness = _install(
        monkeypatch,
        forward=_monitored(_SCOPING_NUMBER, _WORKLOAD_NUMBER),
        rm=[
            _rm_page(_SCOPING_ID, _SCOPING_NUMBER, token="page-2"),
            _rm_page(_WORKLOAD_ID, _WORKLOAD_NUMBER),
        ],
    )

    # Act.
    result = _call(project=_SCOPING_ID)

    # Assert: the id resolved, and the second request carried the page token.
    assert result["queryable_project_ids"] == [_WORKLOAD_ID]
    assert result["project_ids_resolved"] is True
    tokens = [kwargs.get("pageToken") for label, kwargs in harness.calls if label.startswith("rm.")]
    assert tokens == [None, "page-2"]


@pytest.mark.parametrize("direction", ["monitored_projects", "containing_scopes"])
def test_a_capped_project_listing_is_not_reported_as_a_missing_permission(
    monkeypatch: pytest.MonkeyPatch, direction: str
) -> None:
    """Running out of pages must not send an operator to re-grant IAM.

    Both notes describe blank ids, but only one of them is actionable. Telling
    someone to grant ``resourcemanager.projects.list`` when the listing was
    merely capped sends them to a permission they demonstrably already have —
    the call succeeded five times. Parametrized because the two directions build
    their note in separate branches.
    """
    # Arrange: every page hands back another token, so the cap is what stops it.
    endless = [_rm_page(f"proj-{index}", str(index), token="more") for index in range(6)]
    _install(
        monkeypatch,
        forward=_monitored(_SCOPING_NUMBER, _WORKLOAD_NUMBER),
        reverse={
            "metricsScopes": [
                {"name": f"locations/global/metricsScopes/{_WORKLOAD_NUMBER}"},
                {"name": f"locations/global/metricsScopes/{_SCOPING_NUMBER}"},
            ]
        },
        rm=endless,
    )

    # Act.
    note = _call(project=_SCOPING_ID, direction=direction)["note"]

    # Assert: the note names the cap and does not prescribe an IAM grant.
    assert "capped" in note
    assert "grant resourcemanager.projects.list" not in note


# --- A19–A24: the fields a reader acts on, and the gaps that hide from them ---


def test_a_containing_scope_row_carries_the_name_and_number_it_was_matched_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scope name and number are the join key back to every other GCP tool.

    ``observability_project_ids`` alone is not enough: an id resolves only when
    Resource Manager is readable, and when it is not the number is the whole
    answer. Asserted through the tool because the shaper being right says
    nothing about the response carrying what it built.
    """
    # Arrange: the workload's own scope first, the observability scope second.
    _install(
        monkeypatch,
        reverse={
            "metricsScopes": [
                {"name": f"locations/global/metricsScopes/{_WORKLOAD_NUMBER}"},
                {"name": f"locations/global/metricsScopes/{_SCOPING_NUMBER}"},
            ]
        },
    )

    # Act.
    rows = _call(project=_WORKLOAD_ID, direction="containing_scopes")["containing_scopes"]

    # Assert.
    assert [row["scope_name"] for row in rows] == [
        f"locations/global/metricsScopes/{_WORKLOAD_NUMBER}",
        f"locations/global/metricsScopes/{_SCOPING_NUMBER}",
    ]
    assert [row["scope_project_number"] for row in rows] == [_WORKLOAD_NUMBER, _SCOPING_NUMBER]
    assert [row["scope_project_id"] for row in rows] == [_WORKLOAD_ID, _SCOPING_ID]
    assert [row["is_self"] for row in rows] == [True, False]


def test_a_project_in_only_its_own_scope_is_told_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty observability list is the answer, not a dead end.

    Without the note this reads as "the lookup found nothing", which is the
    exact wrong conclusion — it means the project's signals are its own, so
    query it directly.
    """
    # Arrange: the reverse lookup returns the project's own scope and nothing else.
    _install(
        monkeypatch,
        reverse={"metricsScopes": [{"name": f"locations/global/metricsScopes/{_WORKLOAD_NUMBER}"}]},
    )

    # Act.
    result = _call(project=_WORKLOAD_ID, direction="containing_scopes")

    # Assert.
    assert result["observability_project_ids"] == []
    assert "No other project's metrics scope contains this one" in result["note"]


def test_a_tombstoned_monitored_project_is_flagged_not_reported_as_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deleted project still sits in the scope, and reads as a live one.

    Cloud Monitoring keeps the entry so historical series stay attributed.
    Reporting it as an ordinary monitored project sends an investigation at a
    project that no longer exists.
    """
    # Arrange.
    _install(
        monkeypatch,
        forward={
            "name": _SCOPE_NAME,
            "monitoredProjects": [
                {"name": f"projects/{_SCOPING_NUMBER}"},
                {"name": f"projects/{_WORKLOAD_NUMBER}", "isTombstoned": True},
            ],
        },
    )

    # Act.
    rows = _call(project=_SCOPING_ID)["monitored_projects"]

    # Assert.
    assert [row["tombstoned"] for row in rows] == [False, True]


def test_a_project_pending_deletion_is_named_rather_than_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two halves of one gap: the filter that blanks the id, and the field that owns it.

    Resource Manager lists a project awaiting deletion, so the ACTIVE filter is
    what stops its id being handed back as a queryable target. But blanking the
    id used to remove the row from ``queryable_project_ids`` *and*
    ``unconfigured_project_ids`` at once, so the project vanished from
    everything a reader acts on while sitting in plain sight in the scope.
    """
    # Arrange: the workload project is DELETE_REQUESTED, the scoping one ACTIVE.
    _install(
        monkeypatch,
        forward=_monitored(_SCOPING_NUMBER, _WORKLOAD_NUMBER),
        rm={
            "projects": [
                {
                    "projectId": _SCOPING_ID,
                    "projectNumber": _SCOPING_NUMBER,
                    "lifecycleState": "ACTIVE",
                },
                {
                    "projectId": _WORKLOAD_ID,
                    "projectNumber": _WORKLOAD_NUMBER,
                    "lifecycleState": "DELETE_REQUESTED",
                },
            ]
        },
    )

    # Act.
    result = _call(project=_SCOPING_ID)

    # Assert: no id, in neither id-keyed list, and named by number.
    assert [row["project_id"] for row in result["monitored_projects"]] == [_SCOPING_ID, ""]
    assert result["queryable_project_ids"] == []
    assert result["unconfigured_project_ids"] == []
    assert result["unresolved_project_numbers"] == [_WORKLOAD_NUMBER]
    assert "could not be matched to a project id" in result["note"]


def test_the_project_listing_stops_at_five_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is what makes an unbounded estate safe to page at all.

    Pinned on the call count rather than on the note: the "capped" note stays
    correct at any cap, so a cap quietly lowered to two would keep every note
    test green while resolving a fifth of the ids it used to.
    """
    # Arrange: every page hands back another token.
    harness = _install(
        monkeypatch,
        forward=_monitored(_SCOPING_NUMBER, _WORKLOAD_NUMBER),
        rm=[_rm_page(f"proj-{index}", str(index), token="more") for index in range(8)],
    )

    # Act.
    _call(project=_SCOPING_ID)

    # Assert.
    assert sum(1 for label, _ in harness.calls if label == "rm.projects.list") == 5


@pytest.mark.parametrize("failure", ["build", "list"])
def test_a_resource_manager_fault_is_reported_while_a_denial_is_not(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Blank ids look the same either way; only one of the two causes is a bug.

    The note a reader gets tells them to grant resourcemanager.projects.list.
    That is the right advice for a 403 and useless advice for a 500 — and the
    500 is invisible unless it is reported, because the tool degrades cleanly
    through both.

    Parametrized over both failure sites for the same reason the sibling
    degradation test is: the ``except`` around ``build_service`` and the one
    inside ``project_id_index`` are separate guards, and pinning only the
    listing leaves a client that cannot be constructed at all — a credential or
    config fault, never posture — swallowed with the suite green.
    """
    for status, expected in ((HTTPStatus.FORBIDDEN, 0), (HTTPStatus.INTERNAL_SERVER_ERROR, 1)):
        # Arrange.
        _install(
            monkeypatch,
            forward=_monitored(_SCOPING_NUMBER, _WORKLOAD_NUMBER),
            rm=_GoogleApiError(status),
            rm_build_fails=failure == "build",
        )
        reports = _record_reports(monkeypatch)

        # Act.
        result = _call(project=_SCOPING_ID)

        # Assert: degraded identically, reported differently.
        assert result["found"] is True
        assert result["project_ids_resolved"] is False
        assert len(reports.calls) == expected, (failure, status)
        if expected:
            assert reports.calls[0]["method"] == "cloudresourcemanager.projects.list"
            assert reports.calls[0]["severity"] == "warning"


def test_a_containing_scope_with_no_resolvable_id_is_named_rather_than_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty observability list already means something else, and that is the bug.

    ``observability_project_ids: []`` is the tool's way of saying "this project
    sits in no other metrics scope". A scope that came back but could not be
    matched to an id produced exactly that response — the wrong answer to the
    one question this direction exists to answer, delivered with
    ``project_ids_resolved: true`` behind it because that flag is global.
    """
    # Arrange: two scopes, and Resource Manager knows only the workload's own.
    _install(
        monkeypatch,
        reverse={
            "metricsScopes": [
                {"name": f"locations/global/metricsScopes/{_WORKLOAD_NUMBER}"},
                {"name": f"locations/global/metricsScopes/{_SCOPING_NUMBER}"},
            ]
        },
        rm={
            "projects": [
                {
                    "projectId": _WORKLOAD_ID,
                    "projectNumber": _WORKLOAD_NUMBER,
                    "lifecycleState": "ACTIVE",
                }
            ]
        },
    )

    # Act.
    result = _call(project=_WORKLOAD_ID, direction="containing_scopes")

    # Assert: still no id to offer, but the scope is named and the note says so.
    assert result["scope_count"] == 2
    assert result["observability_project_ids"] == []
    assert result["unresolved_scope_numbers"] == [_SCOPING_NUMBER]
    assert "could not be matched to a project id" in result["note"]
    # And it must not read as the self-only answer.
    assert "No other project's metrics scope contains this one" not in result["note"]


def test_a_capped_listing_that_resolved_everything_keeps_the_actionable_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caveat that does not apply must not displace the note a reader acts on.

    The truncation note says "some ids are blank". When none are, it is false —
    and ranked unconditionally above the unconfigured note it also suppressed the
    one line telling the reader what to add to GCP_ADDITIONAL_PROJECTS.
    """
    # Arrange: page one resolves both projects and still hands back a token, so
    # the walk is capped while nothing is left unresolved.
    _install(
        monkeypatch,
        forward=_monitored(_SCOPING_NUMBER, _WORKLOAD_NUMBER),
        rm=[dict(_RM_PROJECTS, nextPageToken="more") for _ in range(6)],
    )

    # Act: the workload project is monitored but not configured.
    result = _call(project=_SCOPING_ID, available_projects=[_SCOPING_ID])

    # Assert.
    assert result["unresolved_project_numbers"] == []
    assert result["unconfigured_project_ids"] == [_WORKLOAD_ID]
    assert "not in GCP_ADDITIONAL_PROJECTS" in result["note"]
    assert "capped" not in result["note"]


def test_a_capped_listing_that_named_every_containing_scope_says_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same gate on the other direction, where there is no note behind it.

    Nothing outranks the truncation caveat here, so an ungated version reads as
    a clean pass — the answer is complete either way. It is still a caveat about
    blanks that do not exist, on the direction whose whole output is ids.
    """
    # Arrange: both scopes resolvable on page one, and the walk still capped.
    _install(
        monkeypatch,
        reverse={
            "metricsScopes": [
                {"name": f"locations/global/metricsScopes/{_WORKLOAD_NUMBER}"},
                {"name": _SCOPE_NAME},
            ]
        },
        rm=[dict(_RM_PROJECTS, nextPageToken="more") for _ in range(6)],
    )

    # Act.
    result = _call(project=_WORKLOAD_ID, direction="containing_scopes")

    # Assert.
    assert result["observability_project_ids"] == [_SCOPING_ID]
    assert result["unresolved_scope_numbers"] == []
    assert result["note"] == ""
