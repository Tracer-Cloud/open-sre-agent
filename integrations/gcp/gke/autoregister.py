"""Register discovered GKE clusters when a long-running process starts.

``opensre integrations add-gke-clusters`` writes to the on-disk integration
store. That is the right place on a workstation and the wrong place in a
container: the store lives in the container filesystem, so it is lost on every
restart and invisible to a second replica. Without this module an operator has
to ``kubectl exec`` into each pod after each rollout, and nothing surfaces the
omission until an investigation reaches for a ``kubernetes_*`` tool and finds
none.

**Opt-in, deliberately.** Registering a cluster widens what the agent can read.
Every ``kubernetes_*`` tool is read-only today — the client's resource dispatch
holds ``read_*`` calls and nothing else, and Secrets are not fetchable — but
read-only is not harmless: ``kubernetes_get_pod_logs`` and
``kubernetes_list_configmaps`` routinely surface credentials and personal data,
and whatever they return lands in LLM context and in investigation reports.
"Every cluster my credential can enumerate is now a source" is a policy decision
an operator should make explicitly, not a side effect of a boot sequence — so
nothing happens unless :data:`GCP_AUTO_REGISTER_GKE_ENV` says so. Scope is bounded three
times over: to the projects that variable names, to the projects the GCP
integration is already configured for (``resolve_projects`` rejects anything
else) so even ``true`` cannot reach an estate nobody configured, and — when an
entry is written ``project/cluster`` — to the named clusters inside them. See
:mod:`integrations.gcp.gke.scope` for that grammar.

**Runs on a daemon thread.** Discovery is several round trips to
``container.googleapis.com`` and verification one more per cluster. Inline, that
puts a remote call in front of the web readiness probe: a slow control plane
would fail readiness for a reason unrelated to the app. On a daemon thread a
slow call costs nothing and cannot delay process exit. The cost is a window
after boot in which the clusters are not yet registered; a turn arriving inside
it sees the tools as unavailable, which is the same state as not opting in.

**Repeats on a cadence.** A cluster created after boot was invisible until
someone restarted the pod, which in a deployment nobody restarts on purpose
means "until the next release". The thread therefore re-runs registration every
:data:`GCP_GKE_REFRESH_INTERVAL_ENV` seconds; setting that to ``0`` restores the
single boot-time run.

Re-running is safe because registration is **additive**: it registers clusters
it finds and leaves everything else alone. It never removes an instance, not
even one it created. That asymmetry is deliberate — a transient
``container.googleapis.com`` error is indistinguishable from a deleted cluster
from here, and the two call for opposite actions. Pruning would mean a bad
minute at Google silently disconnects a healthy cluster mid-investigation; not
pruning means a genuinely deleted cluster lingers as an instance whose tools
fail with a connection error that says so. The second is the better failure.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

from config.constants.gcp import GCP_AUTO_REGISTER_GKE_ENV, GCP_GKE_REFRESH_INTERVAL_ENV
from config.constants.kubernetes import KUBECONFIG_CONTENT_ENV, KUBERNETES_INSTANCES_ENV
from integrations.catalog import (
    classify_integrations,
    load_env_integrations,
    resolve_local_classified_integrations,
)
from integrations.gcp.gke.kubeconfig import AUTH_PLUGIN, plugin_installed
from integrations.gcp.gke.registration import Outcome, register_gke_clusters
from integrations.gcp.gke.scope import ScopeSpec, parse_scopes
from integrations.gcp.refresh import is_off, refresh_interval

#: Values that mean "off". Unset is off too — the point of an opt-in.
_DISABLED = frozenset({"", "0", "false", "no", "off"})

#: Values that mean "every configured project", spelled as the wildcard the
#: shared project grammar already understands rather than a second convention.
_ENABLED = frozenset({"1", "true", "yes", "on"})

#: ``resolve_projects``' wildcard. Scoped to *configured* projects, not to
#: everything the credential can see.
_ALL_CONFIGURED = "*"

#: Distinguishes a cluster this module registered from one an operator added by
#: hand, so `integrations list-clusters` stays readable and a later change can
#: reason about which instances it owns.
_AUTO_TAG = "auto_registered"

_THREAD_NAME = "opensre-gke-autoregister"

#: The one loop this process gets, and the lock that makes "check then start"
#: atomic. Two entry points call in — the ``GKE_AUTOREGISTRATION`` boot step in
#: ``bootstrap.process`` and the
#: import of ``gateway.web.webapp`` — and the gateway process hits *both*, because it
#: serves the web app in a thread (``serve_webapp_in_thread``). Neither caller
#: can know about the other, so the invariant "one registration per process"
#: belongs here rather than in a guard each of them repeats.
_started_thread: threading.Thread | None = None
_start_lock = threading.Lock()


def requested_scope() -> ScopeSpec | None:
    """Return what to register, or ``None`` when the operator did not opt in.

    An operator writing ``GCP_AUTO_REGISTER_GKE=a,b`` gets the same project
    grammar — and the same rejection of unconfigured projects — that every GCP
    tool call already uses. Appending ``/<cluster>`` to any entry narrows it to
    one cluster in that project, for the common case of a project holding one
    cluster worth investigating and several that are not the agent's business.
    """
    raw = os.getenv(GCP_AUTO_REGISTER_GKE_ENV, "").strip()
    folded = raw.lower()
    if folded in _DISABLED:
        return None
    return parse_scopes(_ALL_CONFIGURED if folded in _ENABLED else raw)


def start_gke_autoregistration(logger: logging.Logger) -> threading.Thread | None:
    """Begin auto-registration in the background; return the thread, or ``None``.

    ``None`` means the operator did not opt in, which is the default and not a
    failure. Callers should not join the thread: it exists precisely so that
    boot does not wait on Google.

    Safe to call more than once: the second and later calls return the thread the
    first one started without repeating the work. Registration is idempotent, so
    a duplicate run is harmless in *outcome* — but it is a full round of cluster
    discovery against ``container.googleapis.com`` for a result that is already
    known, and a second thread would not just duplicate the boot run, it would
    halve the refresh interval for the life of the process.
    """
    global _started_thread

    scope = requested_scope()
    if scope is None:
        return None
    with _start_lock:
        if _started_thread is not None:
            return _started_thread
        _started_thread = threading.Thread(
            target=_run,
            args=(logger, scope),
            name=_THREAD_NAME,
            daemon=True,
        )
        _started_thread.start()
        return _started_thread


def _run(logger: logging.Logger, scope: ScopeSpec) -> None:
    """Thread body: register, then keep re-registering until told not to.

    The interval is read once. Re-reading it per iteration would suggest the
    cadence can be changed without a restart, which it cannot — nothing else in
    this process re-reads its environment either.
    """
    interval = refresh_interval(GCP_GKE_REFRESH_INTERVAL_ENV)
    if is_off(interval):
        logger.info(
            "GKE auto-registration will run once (%s disables the refresh loop).",
            GCP_GKE_REFRESH_INTERVAL_ENV,
        )
    else:
        logger.info("GKE auto-registration will re-run every %.0fs.", interval)

    while True:
        _register_guarded(logger, scope)
        if not _wait_for_next_run(interval):
            return


def _register_guarded(logger: logging.Logger, scope: ScopeSpec) -> None:
    """One registration pass that never raises.

    A boot-time convenience must not kill a process — and now that this runs in
    a loop, it must not end the loop either: one failed pass would otherwise
    stop every future pass, turning a transient Google error into a permanent
    loss of refreshing that nothing reports again.
    """
    try:
        register_now(logger, scope)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GKE auto-registration failed (%s)", type(exc).__name__)


def _wait_for_next_run(interval: float) -> bool:
    """Block until the next pass is due; ``False`` means there is no next pass.

    A plain sleep rather than an ``Event``: nothing needs to interrupt this.
    An operator wanting registration *now* calls ``gcp_refresh_discovery``,
    which runs a pass on the calling thread rather than poking this one — so
    the answer arrives in the tool result instead of only in the pod log.
    """
    if is_off(interval):
        return False
    time.sleep(interval)
    return True


def env_declares_kubernetes() -> bool:
    """Whether the environment gives the ``kubernetes_*`` tools a cluster to use.

    The question is not "is a variable set" and not even "does a record come out
    of the loader" — it is "would standing down leave the operator with a working
    integration?" Only classification answers that, so this runs the env records
    through the same classifier the tools read and looks for the service there.

    Each cheaper check fails on a real input:

    * **The variable's presence.** ``KUBERNETES_INSTANCES`` takes a JSON array of
      instance objects; a bare cluster name does not parse, so the loader warns
      and falls through, and the variable contributes nothing.
    * **A record from the loader.** An entry carrying ``name`` and ``tags`` but no
      ``credentials`` parses into a perfectly good-looking record — and the
      classifier then drops it, because an instance with no kubeconfig cannot
      connect to anything.

    Both leave the operator with neither: no env-declared clusters, no
    auto-registered ones, and no ``kubernetes`` integration at all, from config
    that looks set. Deferring only to an environment that actually works is what
    keeps this guard from being a worse footgun than the one it prevents.

    It is also broader than one variable by design: ``KUBECONFIG_CONTENT`` /
    ``KUBECONFIG`` declare a default cluster with ``KUBERNETES_INSTANCES``
    uninvolved, and the store shadows that record exactly as completely.
    """
    return "kubernetes" in classify_integrations(load_env_integrations())


@dataclass(frozen=True)
class RegistrationSummary:
    """What one registration pass did.

    The background thread ignores this and reads the log. ``gcp_refresh_discovery``
    cannot: a tool that answers "I re-registered" without saying what changed
    sends the operator to the pod log, which is the thing they used a tool to
    avoid. ``stood_down`` is set — and the counts left at zero — when a guard
    declined to run at all, a state that is neither success nor failure and
    reads as both if it is not named.
    """

    registered: int = 0
    skipped: int = 0
    failed: int = 0
    instances: tuple[str, ...] = ()
    stood_down: str = ""


def register_now(logger: logging.Logger, scope: ScopeSpec) -> RegistrationSummary:
    """Discover and register, logging the outcome. Synchronous; may raise."""
    if env_declares_kubernetes():
        # The store overrides the environment for a whole service, not per
        # instance, so writing even one discovered cluster here would drop every
        # cluster the operator declared in the environment. Theirs is explicit
        # and ours is inferred; theirs wins, and we do nothing at all.
        logger.info(
            "GKE auto-registration skipped: the environment already declares the kubernetes "
            "integration (%s or %s), and the local store would shadow it wholesale.",
            KUBERNETES_INSTANCES_ENV,
            KUBECONFIG_CONTENT_ENV,
        )
        return RegistrationSummary(
            stood_down=(
                f"the environment already declares the kubernetes integration "
                f"({KUBERNETES_INSTANCES_ENV} or {KUBECONFIG_CONTENT_ENV}); "
                "the local store would shadow it wholesale"
            )
        )

    if not plugin_installed():
        # A GKE kubeconfig carries no credential — it execs the plugin to mint a
        # token. Registering without the plugin stores instances that can never
        # connect, and the failure would look like a cluster problem.
        logger.warning(
            "GKE auto-registration skipped: '%s' is not on PATH, so any kubeconfig "
            "written would be inert.",
            AUTH_PLUGIN,
        )
        return RegistrationSummary(
            stood_down=(f"'{AUTH_PLUGIN}' is not on PATH, so any kubeconfig written would be inert")
        )

    report = register_gke_clusters(
        resolved=resolve_local_classified_integrations(),
        # Both from the one spec: a hand-composed project list that disagreed
        # with the filter would sweep projects the filter then rejects entirely,
        # and the only symptom is "registered 0" with nothing explaining it.
        project=scope.project_selector,
        cluster_scope=scope,
        tags={_AUTO_TAG: "true"},
    )

    for error in report.errors:
        logger.warning("GKE auto-registration: %s", error)
    if report.no_gke:
        # One line, not one per project: with a discovered project list most of
        # the estate has no GKE, and a warning each would repeat every refresh
        # and drown the projects that did fail. Still named, so a project that
        # was expected to hold clusters can be spotted.
        logger.info(
            "GKE auto-registration: %d project(s) have no Kubernetes Engine API — %s",
            len(report.no_gke),
            ", ".join(report.no_gke),
        )
    if report.excluded:
        # The scope is operator intent, so this is confirmation rather than a
        # problem — but it is also the only place a mistyped cluster name shows
        # up. Listing what was passed over next to what was kept lets the
        # spelling be compared without a second run.
        logger.info(
            "GKE auto-registration: %d cluster(s) outside %s=%s — %s",
            len(report.excluded),
            GCP_AUTO_REGISTER_GKE_ENV,
            scope,
            ", ".join(report.excluded),
        )
    registered: list[str] = []
    for result in report.results:
        if result.outcome is Outcome.REGISTERED:
            registered.append(result.instance)
            logger.info(
                "GKE auto-registration: registered %s (%s) as '%s'.",
                result.cluster,
                result.project,
                result.instance,
            )
        elif result.outcome is Outcome.FAILED:
            logger.warning(
                "GKE auto-registration: %s (%s) failed — %s",
                result.cluster,
                result.project,
                result.detail,
            )
    logger.info(
        "GKE auto-registration finished: registered %d, skipped %d, failed %d.",
        report.count(Outcome.REGISTERED),
        report.count(Outcome.SKIPPED),
        report.count(Outcome.FAILED),
    )
    return RegistrationSummary(
        registered=report.count(Outcome.REGISTERED),
        skipped=report.count(Outcome.SKIPPED),
        failed=report.count(Outcome.FAILED),
        instances=tuple(registered),
    )


__all__ = [
    "RegistrationSummary",
    "env_declares_kubernetes",
    "register_now",
    "requested_scope",
    "start_gke_autoregistration",
]
