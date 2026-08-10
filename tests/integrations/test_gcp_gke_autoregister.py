"""Boot-time GKE auto-registration: opt-in, bounded, and never destructive."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from config.constants.gcp import GCP_AUTO_REGISTER_GKE_ENV, GCP_GKE_REFRESH_INTERVAL_ENV
from config.constants.kubernetes import (
    KUBECONFIG_CONTENT_ENV,
    KUBECONFIG_CONTEXT_ENV,
    KUBECONFIG_NAMESPACE_ENV,
    KUBECONFIG_PATH_ENV,
    KUBERNETES_INSTANCES_ENV,
)
from integrations.gcp.gke import autoregister
from integrations.gcp.gke.registration import (
    ClusterRegistration,
    Outcome,
    RegistrationReport,
)
from integrations.gcp.gke.scope import ANY, ClusterScope, parse_scopes
from integrations.gcp.refresh import NEVER


class _Recorder:
    """Stand in for ``register_gke_clusters`` and remember how it was called."""

    def __init__(self, report: RegistrationReport | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._report = report or RegistrationReport()

    def __call__(self, **kwargs: Any) -> RegistrationReport:
        self.calls.append(kwargs)
        return self._report


def _resolved() -> dict[str, Any]:
    return {}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GCP_AUTO_REGISTER_GKE_ENV, raising=False)
    # Every variable that makes the environment declare a kubernetes integration,
    # not just the instances one. KUBECONFIG especially: it is set on virtually
    # every developer machine, and the guard reads it, so leaving it in place
    # would make these tests pass or fail depending on whose laptop ran them.
    for name in (
        KUBERNETES_INSTANCES_ENV,
        KUBECONFIG_PATH_ENV,
        KUBECONFIG_CONTENT_ENV,
        KUBECONFIG_CONTEXT_ENV,
        KUBECONFIG_NAMESPACE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    # The once-per-process guard is module state, so without this the first test
    # to start a thread would silently suppress every later one.
    monkeypatch.setattr(autoregister, "_started_thread", None)
    # Boot-only by default. Every test below that joins the thread is about the
    # *first* pass; with the shipped default the thread would then sleep for
    # half an hour and each of those joins would time out on a live thread.
    # The refresh loop has its own section, which sets this explicitly.
    monkeypatch.setenv(GCP_GKE_REFRESH_INTERVAL_ENV, "0")


@pytest.fixture
def _ready(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """Everything auto-registration depends on, stubbed to succeed."""
    recorder = _Recorder()
    monkeypatch.setattr(autoregister, "plugin_installed", lambda: True)
    monkeypatch.setattr(autoregister, "resolve_local_classified_integrations", _resolved)
    monkeypatch.setattr(autoregister, "register_gke_clusters", recorder)
    return recorder


# --- opt-in --------------------------------------------------------------------


def test_unset_means_off() -> None:
    """The default must be off: registering widens what the agent can reach."""
    assert autoregister.requested_scope() is None


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", "", "   "])
def test_falsey_values_are_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, value)

    assert autoregister.requested_scope() is None


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_truthy_values_mean_every_configured_project(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """``*`` is the shared grammar's wildcard, and it spans *configured* projects
    only — ``resolve_projects`` rejects anything else, so opting in cannot reach
    an estate nobody configured."""
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, value)

    scope = autoregister.requested_scope()

    assert scope is not None
    assert scope.project_selector == "*"
    assert not scope.names_clusters


def test_a_project_list_narrows_to_those_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "proj-a,proj-b")

    scope = autoregister.requested_scope()

    assert scope is not None
    assert scope.project_selector == "proj-a,proj-b"
    assert scope.admits("proj-a", "anything")
    assert not scope.admits("proj-c", "anything")


def test_a_qualified_entry_narrows_to_one_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason the filter exists: a project holding one cluster worth reaching."""
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "proj-a/checkout")

    scope = autoregister.requested_scope()

    assert scope is not None
    # Discovery still sweeps the whole project — a cluster cannot be listed by
    # name alone — but only the named one is registered.
    assert scope.project_selector == "proj-a"
    assert scope.admits("proj-a", "checkout")
    assert not scope.admits("proj-a", "billing")


def test_qualified_and_bare_entries_mix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "proj-a/checkout,proj-b")

    scope = autoregister.requested_scope()

    assert scope is not None
    assert scope.scopes == (ClusterScope("proj-a", "checkout"), ClusterScope("proj-b"))


def test_a_wildcard_project_takes_the_named_cluster_anywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For an operator who knows the cluster name but not which project holds it."""
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "*/checkout")

    scope = autoregister.requested_scope()

    assert scope is not None
    assert scope.scopes == (ClusterScope(ANY, "checkout"),)
    assert scope.project_selector == "*"


def test_nothing_starts_when_opted_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode() -> bool:
        raise AssertionError("must not touch the plugin when opted out")

    monkeypatch.setattr(autoregister, "plugin_installed", _explode)

    assert autoregister.start_gke_autoregistration(logging.getLogger(__name__)) is None


# --- the precedence guard ------------------------------------------------------


def test_kubernetes_instances_env_stops_registration_entirely(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    """Explicit operator config must survive.

    ``merge_local_integrations`` lets the store override the environment for a
    *whole service*, not per instance. Registering even one discovered cluster
    would therefore replace the entire ``KUBERNETES_INSTANCES`` record and
    silently drop every cluster the operator declared. Standing down is the only
    non-destructive answer.
    """
    monkeypatch.setenv(KUBERNETES_INSTANCES_ENV, '[{"name":"eks-prod","kubeconfig":"..."}]')

    with caplog.at_level(logging.INFO):
        autoregister.register_now(logging.getLogger(__name__), parse_scopes("*"))

    assert _ready.calls == []
    assert KUBERNETES_INSTANCES_ENV in caplog.text


def test_an_empty_kubernetes_instances_env_does_not_block(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """Whitespace is not a declared cluster set."""
    monkeypatch.setenv(KUBERNETES_INSTANCES_ENV, "   ")

    autoregister.register_now(logging.getLogger(__name__), parse_scopes("*"))

    assert len(_ready.calls) == 1


@pytest.mark.parametrize(
    "value",
    ["prod-cluster-a", "gke-prod,gke-dev", "[]", "{}", "not json at all"],
)
def test_an_unparseable_instances_env_declares_nothing_so_we_still_register(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder, value: str
) -> None:
    """Set-but-meaningless must not stand us down.

    ``KUBERNETES_INSTANCES`` is a JSON array of instance objects. Anything else —
    a bare cluster name is the obvious typo — fails to parse, and the loader
    falls through to the legacy vars, so the variable contributes **zero**
    clusters. Standing down for it would leave the operator with neither: no
    env-declared clusters and no auto-registered ones, from an env var that looks
    set. Deferring only to config that actually exists is what keeps the guard
    from being a footgun of its own.
    """
    monkeypatch.setenv(KUBERNETES_INSTANCES_ENV, value)

    autoregister.register_now(logging.getLogger(__name__), parse_scopes("*"))

    assert len(_ready.calls) == 1, _ready.calls


def test_an_instance_without_credentials_is_not_a_working_cluster(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """Parseable is not the same as usable.

    ``[{"name": "x", "tags": {...}}]`` with no ``credentials`` parses into a
    record that looks entirely reasonable, and the classifier then drops it —
    an instance with no kubeconfig cannot connect to anything. Standing down for
    it would again leave the operator with neither: no env clusters, no
    auto-registered ones, no kubernetes integration at all.
    """
    monkeypatch.setenv(
        KUBERNETES_INSTANCES_ENV, '[{"name": "prod-cluster-a", "tags": {"env": "utility"}}]'
    )

    autoregister.register_now(logging.getLogger(__name__), parse_scopes("*"))

    assert len(_ready.calls) == 1, _ready.calls


def test_an_instance_with_credentials_does_stand_us_down(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """The same entry, once it can actually reach a cluster, is authoritative."""
    monkeypatch.setenv(
        KUBERNETES_INSTANCES_ENV,
        '[{"name": "prod-cluster-a", "tags": {"env": "utility"},'
        ' "credentials": {"kubeconfig_path": "/etc/kube/config",'
        ' "context": "gke_p_us-central1_prod-cluster-a", "namespace": "default"}}]',
    )

    autoregister.register_now(logging.getLogger(__name__), parse_scopes("*"))

    assert _ready.calls == []


def test_a_single_cluster_declared_via_kubeconfig_also_stands_us_down(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """The hazard is the whole-record override, not one variable's name.

    ``KUBECONFIG_CONTENT`` declares a default cluster without
    ``KUBERNETES_INSTANCES`` being involved, and the store shadows that record
    exactly as completely. A guard that read one variable name would sail past
    this and silently disconnect the operator's only cluster.
    """
    monkeypatch.setenv(KUBECONFIG_CONTENT_ENV, "apiVersion: v1\nkind: Config\n")

    autoregister.register_now(logging.getLogger(__name__), parse_scopes("*"))

    assert _ready.calls == []


# --- refusing to register instances that cannot work ---------------------------


def test_a_missing_auth_plugin_stops_registration(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    """A GKE kubeconfig holds no credential — it execs the plugin to mint a token.

    Without the plugin every instance written would be permanently unable to
    connect, and the failure would read as a cluster problem rather than a
    missing binary.
    """
    monkeypatch.setattr(autoregister, "plugin_installed", lambda: False)

    with caplog.at_level(logging.WARNING):
        autoregister.register_now(logging.getLogger(__name__), parse_scopes("*"))

    assert _ready.calls == []
    assert "gke-gcloud-auth-plugin" in caplog.text


# --- what gets handed to the registrar -----------------------------------------


def test_the_selector_and_the_auto_tag_reach_the_registrar(_ready: _Recorder) -> None:
    autoregister.register_now(logging.getLogger(__name__), parse_scopes("proj-a"))

    call = _ready.calls[0]
    assert call["project"] == "proj-a"
    assert call["tags"] == {"auto_registered": "true"}


def test_the_scope_and_the_projects_it_implies_arrive_together(_ready: _Recorder) -> None:
    """One spec drives both halves, so discovery cannot sweep what the filter rejects.

    A hand-composed project list beside the filter is the failure worth guarding:
    it registers nothing and logs no reason.
    """
    scope = parse_scopes("proj-a/checkout")
    autoregister.register_now(logging.getLogger(__name__), scope)

    call = _ready.calls[0]
    assert call["project"] == "proj-a"
    assert call["cluster_scope"] is scope


def test_an_unqualified_opt_in_sends_a_scope_that_filters_nothing(_ready: _Recorder) -> None:
    """``GCP_AUTO_REGISTER_GKE=true`` must behave exactly as it did before filtering."""
    autoregister.register_now(logging.getLogger(__name__), parse_scopes("*"))

    assert _ready.calls[0]["cluster_scope"].admits("any-project", "any-cluster")


def test_clusters_the_scope_passed_over_are_named_in_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A mistyped cluster name has no other symptom than nothing being registered."""
    report = RegistrationReport(excluded=["billing (proj-a)", "payments (proj-a)"])
    monkeypatch.setattr(autoregister, "plugin_installed", lambda: True)
    monkeypatch.setattr(autoregister, "resolve_local_classified_integrations", _resolved)
    monkeypatch.setattr(autoregister, "register_gke_clusters", _Recorder(report))

    with caplog.at_level(logging.INFO):
        autoregister.register_now(logging.getLogger(__name__), parse_scopes("proj-a/chekout"))

    # The value as written next to the names it did not match: enough to spot the
    # typo without a second discovery run.
    assert "proj-a/chekout" in caplog.text
    assert "billing (proj-a)" in caplog.text
    assert "payments (proj-a)" in caplog.text


def test_registering_everything_logs_no_exclusions(
    _ready: _Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        autoregister.register_now(logging.getLogger(__name__), parse_scopes("*"))

    assert "outside" not in caplog.text


def test_existing_clusters_are_never_overwritten(_ready: _Recorder) -> None:
    """``overwrite`` stays at its default. Repointing an instance an operator
    already registered would change where every later ``kubernetes_*`` call
    lands, which a boot-time convenience has no business doing."""
    autoregister.register_now(logging.getLogger(__name__), parse_scopes("*"))

    assert _ready.calls[0].get("overwrite") in (None, False)


# --- failure containment -------------------------------------------------------


def test_the_thread_body_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A convenience that runs at boot must not be able to kill the process."""

    def _boom(_logger: logging.Logger, _selector: str) -> None:
        raise RuntimeError("control plane unreachable")

    monkeypatch.setattr(autoregister, "register_now", _boom)

    autoregister._run(logging.getLogger(__name__), parse_scopes("*"))


def test_discovery_errors_are_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    report = RegistrationReport(errors=["project 'proj-b' could not be listed"])
    monkeypatch.setattr(autoregister, "plugin_installed", lambda: True)
    monkeypatch.setattr(autoregister, "resolve_local_classified_integrations", _resolved)
    monkeypatch.setattr(autoregister, "register_gke_clusters", _Recorder(report))

    with caplog.at_level(logging.WARNING):
        autoregister.register_now(logging.getLogger(__name__), parse_scopes("*"))

    assert "proj-b" in caplog.text


def test_a_failed_cluster_is_reported_with_its_detail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    report = RegistrationReport(
        results=[
            ClusterRegistration("c1", "proj-a", "c1", Outcome.FAILED, "private endpoint only"),
            ClusterRegistration("c2", "proj-a", "c2", Outcome.REGISTERED, "registered"),
        ]
    )
    monkeypatch.setattr(autoregister, "plugin_installed", lambda: True)
    monkeypatch.setattr(autoregister, "resolve_local_classified_integrations", _resolved)
    monkeypatch.setattr(autoregister, "register_gke_clusters", _Recorder(report))

    with caplog.at_level(logging.INFO):
        autoregister.register_now(logging.getLogger(__name__), parse_scopes("*"))

    assert "private endpoint only" in caplog.text
    assert "registered 1, skipped 0, failed 1" in caplog.text


# --- the refresh loop ----------------------------------------------------------


class _Passes:
    """Counts registration passes and ends the loop after ``limit`` of them.

    Substituted for the sleep rather than for the clock: the loop's only exit
    is what ``_wait_for_next_run`` returns, so driving it here is what makes
    "it runs more than once" assertable without waiting a real interval.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.waits = 0

    def __call__(self, _interval: float) -> bool:
        self.waits += 1
        return self.waits < self.limit


def test_registration_repeats_rather_than_running_once(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """A cluster created after boot was invisible until the pod restarted."""
    monkeypatch.setenv(GCP_GKE_REFRESH_INTERVAL_ENV, "600")
    monkeypatch.setattr(autoregister, "_wait_for_next_run", _Passes(limit=3))

    autoregister._run(logging.getLogger(__name__), parse_scopes("*"))

    assert len(_ready.calls) == 3


def test_a_failed_pass_does_not_end_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure mode the loop introduces, and the reason for the inner guard.

    Without it one transient ``container.googleapis.com`` error would stop every
    future pass — a permanent loss of refreshing, reported once at warning and
    never again.
    """
    calls: list[int] = []

    def _boom(_logger: logging.Logger, _scope: Any) -> None:
        calls.append(1)
        raise RuntimeError("control plane unreachable")

    monkeypatch.setenv(GCP_GKE_REFRESH_INTERVAL_ENV, "600")
    monkeypatch.setattr(autoregister, "register_now", _boom)
    monkeypatch.setattr(autoregister, "_wait_for_next_run", _Passes(limit=3))

    autoregister._run(logging.getLogger(__name__), parse_scopes("*"))

    assert len(calls) == 3


def test_the_interval_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """``0`` restores the original behaviour: one pass, then the thread ends."""
    monkeypatch.setenv(GCP_GKE_REFRESH_INTERVAL_ENV, "0")

    autoregister._run(logging.getLogger(__name__), parse_scopes("*"))

    assert len(_ready.calls) == 1


def test_switched_off_is_the_only_way_the_loop_ends() -> None:
    """Pinned directly, because everything above stubs this function out."""
    assert autoregister._wait_for_next_run(NEVER) is False


# --- the background thread -----------------------------------------------------


def test_opting_in_starts_a_daemon_thread(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """Daemon, so a hung control-plane call cannot delay process exit; threaded,
    so an unbounded remote call never sits in front of the readiness probe."""
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "true")

    thread = autoregister.start_gke_autoregistration(logging.getLogger(__name__))

    assert thread is not None
    assert thread.daemon
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert _ready.calls[0]["project"] == "*"


def test_a_second_call_reuses_the_first_thread_instead_of_rediscovering(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """Two entry points call in, and the gateway process reaches both.

    The ``GKE_AUTOREGISTRATION`` boot step in ``bootstrap.process`` calls it
    directly, and importing
    ``gateway.web.webapp`` calls it at module scope — which the gateway also
    does, via ``serve_webapp_in_thread``. Registration is idempotent, so the
    duplicate was harmless in outcome, but it was a second full round of cluster
    discovery for an answer already known, and it logged a bogus second summary.
    """
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "true")
    logger = logging.getLogger(__name__)

    first = autoregister.start_gke_autoregistration(logger)
    assert first is not None
    first.join(timeout=5)

    second = autoregister.start_gke_autoregistration(logger)

    assert second is first
    assert len(_ready.calls) == 1, _ready.calls


def test_opting_out_is_decided_before_the_once_guard(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """Turning it off must not be sticky in the other direction either.

    The guard remembers a thread, not a decision, so a call made while opted out
    records nothing and a later opt-in still runs.
    """
    assert autoregister.start_gke_autoregistration(logging.getLogger(__name__)) is None

    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "true")
    thread = autoregister.start_gke_autoregistration(logging.getLogger(__name__))

    assert thread is not None
    thread.join(timeout=5)
    assert len(_ready.calls) == 1, _ready.calls
