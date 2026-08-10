"""Google Cloud Platform environment variable names.

Distinct from the ``VERTEX_AI_*`` names in :mod:`config.constants.llm`: those
select the LLM that powers the agent, these select the GCP estate the agent
*investigates*. They are frequently the same project, but conflating them
breaks any deployment that runs Vertex in one project and infrastructure in
another.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Final

#: Primary project every tool call defaults to.
GCP_PROJECT_ID_ENV: Final[str] = "GCP_PROJECT_ID"

#: Google's own standard project variable, honoured as a fallback so a pod that
#: already sets it for the client libraries needs no extra configuration.
GOOGLE_CLOUD_PROJECT_ENV: Final[str] = "GOOGLE_CLOUD_PROJECT"

#: Comma-separated additional project IDs reachable with the same credential.
#: Accepts literal ids, :data:`GCP_DISCOVER_PROJECTS_TOKEN`, or a mix of both.
#: There is deliberately no ``*`` here: ``*`` is what a *caller* passes to mean
#: "every configured project", and honouring it as a config value too would
#: make one character mean both "all of them" and "the set of all of them",
#: which cannot be resolved without knowing which side wrote it.
GCP_ADDITIONAL_PROJECTS_ENV: Final[str] = "GCP_ADDITIONAL_PROJECTS"

#: Value for :data:`GCP_ADDITIONAL_PROJECTS_ENV` meaning "ask Cloud Resource
#: Manager which projects this credential can see, and allow all of them".
#: A word rather than a symbol because it names an *action* with a latency and
#: a permission cost (``resourcemanager.projects.list``), not a pattern match.
GCP_DISCOVER_PROJECTS_TOKEN: Final[str] = "discover"

#: Service-account JSON — either the literal document or a path to it. Ends in
#: ``_KEY`` so it is keyring-eligible and must be read through
#: ``resolve_env_credential``, never bare ``os.getenv``.
GCP_SERVICE_ACCOUNT_KEY_ENV: Final[str] = "GCP_SERVICE_ACCOUNT_KEY"

#: Service account to impersonate on top of the ambient credential. Lets one
#: Workload Identity binding reach several estates without minting JSON keys.
GCP_IMPERSONATE_SERVICE_ACCOUNT_ENV: Final[str] = "GCP_IMPERSONATE_SERVICE_ACCOUNT"

#: Default page size for log/metric queries.
GCP_MAX_RESULTS_ENV: Final[str] = "GCP_MAX_RESULTS"

#: JSON array registering several named GCP estates at once. See
#: ``docs/multi-instance-integrations.mdx``.
GCP_INSTANCES_ENV: Final[str] = "GCP_INSTANCES"

#: Opt-in: register discovered GKE clusters as Kubernetes instances at process
#: start. Off unless set, because it widens what the agent can read — every
#: cluster the credential can enumerate in the configured projects becomes a
#: source for the ``kubernetes_*`` tools, and pod logs and configmaps routinely
#: carry credentials and personal data.
#: Accepts ``true`` for every configured project, the same comma-separated
#: project grammar the GCP tools take, or ``project/cluster`` entries to narrow
#: it to named clusters — the granularity that matters when a project holds one
#: cluster worth investigating and several that are not the agent's business.
GCP_AUTO_REGISTER_GKE_ENV: Final[str] = "GCP_AUTO_REGISTER_GKE"

#: How often to re-run GKE auto-registration, in seconds. ``0`` (or any of the
#: "off" spellings) keeps the original behaviour: one run at process start.
#: Only meaningful when :data:`GCP_AUTO_REGISTER_GKE_ENV` is on — this controls
#: the cadence of a thing that is already opted into, never whether it happens.
GCP_GKE_REFRESH_INTERVAL_ENV: Final[str] = "GCP_GKE_REFRESH_INTERVAL"

#: How long a Cloud Resource Manager project listing stays fresh, in seconds.
#: A ceiling on staleness, not a promise of freshness: the re-list happens on
#: the next tool call after expiry, not on a timer.
GCP_PROJECT_REFRESH_INTERVAL_ENV: Final[str] = "GCP_PROJECT_REFRESH_INTERVAL"

#: Default for :data:`GCP_PROJECT_REFRESH_INTERVAL_ENV` and
#: :data:`GCP_GKE_REFRESH_INTERVAL_ENV`. Thirty minutes is chosen against how
#: often an estate actually changes (rarely) versus how long an operator will
#: tolerate the agent not seeing a project they just created. The manual
#: ``gcp_refresh_discovery`` tool exists because no interval is a good answer to
#: the second question.
GCP_DEFAULT_REFRESH_INTERVAL_SECONDS: Final[float] = 1800.0

#: Socket timeout for every Google API call, in seconds.
#: ``httplib2`` — the transport ``google-api-python-client`` uses — takes a
#: timeout only when the ``Http`` object is *constructed*, and defaults to none.
#: Without this, a wedged control plane hangs a call forever; on the background
#: refresh loops that means the loop stops and nothing says so.
GCP_HTTP_TIMEOUT_SECONDS: Final[float] = 30.0

#: Statuses that mean "this credential was never meant to reach that" — the
#: operator's IAM posture rather than a fault. A tool degrades on these *without*
#: telemetry, because reporting posture files one Sentry event per turn for as
#: long as the grant stands. Precedent: the Rootly On-Call 403 and the Kubernetes
#: namespace 403. Shared rather than per-tool: two frozensets of the same name in
#: one integration, with different members, is a grep that answers wrong.
GCP_UNENTITLED_STATUSES: Final[frozenset[HTTPStatus]] = frozenset(
    {HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND}
)

#: Cloud Monitoring adds ``501``: an estate that does not serve alert instances
#: at all answers "not implemented" rather than denying the read, and that is
#: still posture rather than a fault.
GCP_MONITORING_UNENTITLED_STATUSES: Final[frozenset[HTTPStatus]] = GCP_UNENTITLED_STATUSES | {
    HTTPStatus.NOT_IMPLEMENTED
}

__all__ = [
    "GCP_ADDITIONAL_PROJECTS_ENV",
    "GCP_AUTO_REGISTER_GKE_ENV",
    "GCP_DEFAULT_REFRESH_INTERVAL_SECONDS",
    "GCP_DISCOVER_PROJECTS_TOKEN",
    "GCP_GKE_REFRESH_INTERVAL_ENV",
    "GCP_HTTP_TIMEOUT_SECONDS",
    "GCP_MONITORING_UNENTITLED_STATUSES",
    "GCP_UNENTITLED_STATUSES",
    "GCP_IMPERSONATE_SERVICE_ACCOUNT_ENV",
    "GCP_INSTANCES_ENV",
    "GCP_MAX_RESULTS_ENV",
    "GCP_PROJECT_ID_ENV",
    "GCP_PROJECT_REFRESH_INTERVAL_ENV",
    "GCP_SERVICE_ACCOUNT_KEY_ENV",
    "GOOGLE_CLOUD_PROJECT_ENV",
]
