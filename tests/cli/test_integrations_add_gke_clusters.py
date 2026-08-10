"""``opensre integrations add-gke-clusters`` surface behavior.

The registration logic itself is covered in
``tests/integrations/test_gcp_gke_registration.py``; this pins the parts that
only exist at the CLI boundary — the up-front auth-plugin gate, tag parsing,
and the exit code an operator's script will branch on.
"""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from integrations.gcp import gke as gke_package
from integrations.gcp.gke.registration import ClusterRegistration, Outcome, RegistrationReport
from platform.common.exit_codes import ERROR, SUCCESS
from surfaces.cli.commands.integrations import add_gke_clusters_command


def _report(*results: ClusterRegistration, errors: list[str] | None = None) -> RegistrationReport:
    return RegistrationReport(results=list(results), errors=list(errors or []))


def _registered(name: str = "prod") -> ClusterRegistration:
    return ClusterRegistration(name, "acme", name, Outcome.REGISTERED, f"Cluster '{name}' saved.")


class _Recorder:
    """Captures the keyword arguments the command forwards to registration."""

    def __init__(self, report: RegistrationReport) -> None:
        self.report = report
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> RegistrationReport:
        self.calls.append(kwargs)
        return self.report


@pytest.fixture
def _no_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the command away from the real integration store."""

    def _resolved() -> dict[str, Any]:
        return {"gcp": {"project_id": "acme"}}

    monkeypatch.setattr("integrations.catalog.resolve_local_classified_integrations", _resolved)


def _set_plugin(monkeypatch: pytest.MonkeyPatch, installed: bool) -> None:
    def _installed() -> bool:
        return installed

    monkeypatch.setattr(gke_package, "plugin_installed", _installed)


def _set_register(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    monkeypatch.setattr(gke_package, "register_gke_clusters", recorder)


@pytest.mark.usefixtures("_no_store")
def test_a_missing_auth_plugin_stops_the_run_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(_report())
    _set_plugin(monkeypatch, installed=False)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, [])

    assert result.exit_code == ERROR
    assert "gke-gcloud-auth-plugin" in result.output
    assert recorder.calls == []


@pytest.mark.usefixtures("_no_store")
def test_a_dry_run_proceeds_without_the_plugin_but_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(_report(_registered()))
    _set_plugin(monkeypatch, installed=False)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, ["--dry-run"])

    assert result.exit_code == SUCCESS
    assert "Warning" in result.output
    assert recorder.calls[0]["dry_run"] is True


@pytest.mark.usefixtures("_no_store")
def test_the_run_reports_each_cluster_and_a_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(
        _report(
            _registered("prod"),
            ClusterRegistration(
                "staging", "acme", "staging", Outcome.SKIPPED, "already registered"
            ),
        )
    )
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, [])

    assert result.exit_code == SUCCESS
    assert "+ prod (acme) -> prod" in result.output
    assert "= staging (acme) -> staging" in result.output
    assert "registered 1, skipped 1, failed 0." in result.output


@pytest.mark.usefixtures("_no_store")
def test_a_failed_cluster_produces_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(
        _report(ClusterRegistration("prod", "acme", "prod", Outcome.FAILED, "connection refused"))
    )
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, [])

    assert result.exit_code == ERROR
    assert "! prod (acme) -> prod: connection refused" in result.output


@pytest.mark.usefixtures("_no_store")
def test_a_discovery_error_produces_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(_report(errors=["acme: HTTP 403: denied"]))
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, [])

    assert result.exit_code == ERROR
    assert "acme: HTTP 403: denied" in result.output


@pytest.mark.usefixtures("_no_store")
def test_an_empty_estate_says_so_rather_than_printing_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(_report())
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, [])

    assert result.exit_code == SUCCESS
    assert "No GKE clusters found" in result.output


@pytest.mark.usefixtures("_no_store")
def test_a_project_without_the_api_is_named_but_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It is not a failure — but it is the reason a project the operator named is missing."""
    report = _report(_registered())
    report.no_gke = ["acme-staging"]
    recorder = _Recorder(report)
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, [])

    assert result.exit_code == SUCCESS
    assert "no Kubernetes Engine API: acme-staging" in result.output


@pytest.mark.usefixtures("_no_store")
def test_flags_reach_the_registration_call(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(_report(_registered()))
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(
        add_gke_clusters_command,
        ["--project", "acme,research", "--tag", "env=prod", "--overwrite", "--no-verify"],
    )

    assert result.exit_code == SUCCESS
    call = recorder.calls[0]
    assert call["project"] == "acme,research"
    assert call["tags"] == {"env": "prod"}
    assert call["overwrite"] is True
    assert call["verify"] is False


@pytest.mark.usefixtures("_no_store")
def test_no_cluster_flag_sends_no_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default has always been "register everything --project found"."""
    recorder = _Recorder(_report(_registered()))
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, [])

    assert result.exit_code == SUCCESS
    assert recorder.calls[0]["cluster_scope"] is None


@pytest.mark.usefixtures("_no_store")
def test_cluster_flags_narrow_what_is_registered_without_moving_the_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--project`` decides what is discovered; ``--cluster`` only filters it.

    Deriving projects from ``--cluster`` too would give the run two answers about
    where to look, and the flag can name a cluster without naming its project.
    """
    recorder = _Recorder(_report(_registered("checkout")))
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(
        add_gke_clusters_command,
        ["--project", "acme", "--cluster", "checkout", "--cluster", "billing"],
    )

    assert result.exit_code == SUCCESS
    call = recorder.calls[0]
    assert call["project"] == "acme"
    scope = call["cluster_scope"]
    assert scope.admits("acme", "checkout")
    assert scope.admits("acme", "billing")
    assert not scope.admits("acme", "payments")


@pytest.mark.usefixtures("_no_store")
def test_a_cluster_name_that_matched_nothing_is_answered_with_what_was_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo is the one way --cluster fails, and it fails by doing nothing."""
    report = _report()
    report.excluded = ["checkout (acme)", "billing (acme)"]
    recorder = _Recorder(report)
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, ["--cluster", "chekout"])

    assert "checkout (acme)" in result.output
    assert "billing (acme)" in result.output
    assert "No GKE cluster matched --cluster" in result.output


@pytest.mark.usefixtures("_no_store")
def test_a_malformed_tag_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(_report())
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, ["--tag", "oops"])

    assert result.exit_code == ERROR
    assert "expected KEY=VALUE" in result.output
    assert recorder.calls == []


def test_the_resolver_the_command_uses_yields_projects_registration_can_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command's resolver must produce the shape ``gcp_tool_params`` reads.

    Every other test here stubs ``register_gke_clusters``, and the registration
    tests build their own classified dict, so nothing exercised the seam between
    them. It was broken: the command passed ``resolve_effective_integrations``,
    whose ``{service: {"source": ..., "config": {...}}}`` wrapper the GCP
    sanitizer discards wholesale. The command then reported "no GCP projects are
    configured" on a deployment with ``GCP_PROJECT_ID`` set, and no test noticed.

    Asserting on the shape rather than mocking it is the point — a mock would
    have agreed with the broken code, which is exactly what ``_no_store`` did.
    """
    from integrations.catalog import resolve_local_classified_integrations
    from integrations.gcp.tool_params import gcp_tool_params

    monkeypatch.setenv("GCP_PROJECT_ID", "acme")
    monkeypatch.setenv("GCP_ADDITIONAL_PROJECTS", "acme-staging")

    scope = gcp_tool_params(resolve_local_classified_integrations(store_integrations=[]))

    assert scope["default_project"] == "acme"
    assert scope["available_projects"] == ["acme", "acme-staging"]
