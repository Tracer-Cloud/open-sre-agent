"""Expand ``GCP_ADDITIONAL_PROJECTS=discover`` into a concrete allow-list.

GCP inherits IAM down the resource hierarchy, so one service account granted a
viewer role at the folder or organization level already reads every project
beneath it. Naming each of those projects in configuration is pure transcription
— and transcription that goes stale the moment someone creates a project. This
module asks Cloud Resource Manager instead.

**Why a token and not a wildcard.** ``*`` is already taken: a *caller* passes it
to :func:`~integrations.gcp.projects.resolve_projects` to mean "every configured
project". If it also meant "discover" on the config side, the same character
would denote both a set and the act of computing that set, and neither reader
could tell which was intended. Worse, the two are silently compatible — an
operator who writes ``GCP_ADDITIONAL_PROJECTS="*"`` expecting discovery gets a
project whose id is literally ``*``, which passes every local check and then
fails inside Google. ``discover`` cannot be mistaken for a project id.

**Cached with a TTL, failures included.** The list feeds an allow-list consulted
on every GCP tool call, so re-listing per call would put a network round trip in
front of each one. Failures are cached too: the failure that actually happens is
a service account without ``resourcemanager.projects.list``, and caching only
successes would retry — and pay — on every single call.

The TTL is what makes a project created after boot reachable without a restart.
It is a *ceiling on staleness*, not a promise of freshness: nothing runs on a
timer here, so the re-list happens on the first tool call after expiry. An
operator who needs the estate re-read *now* has ``gcp_refresh_discovery``.

**A refresh may only widen.** Two rules, and the second is the one that is easy
to get wrong:

1. A failed discovery yields the configured projects alone. Falling back to
   something broader would let a transient Google error quietly grant read
   access nobody granted.
2. A failed *re*-discovery keeps the previous successful listing. This is the
   opposite of rule 1 and deliberately so: at boot there is nothing better to
   fall back to, but once a good listing exists, replacing it with a failure
   would shrink an allow-list that worked a minute ago — an investigation
   mid-flight would start getting "unknown GCP project" for a project it had
   already queried. Stale-but-working beats correct-and-broken.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field

from config.constants.gcp import (
    GCP_ADDITIONAL_PROJECTS_ENV,
    GCP_DISCOVER_PROJECTS_TOKEN,
    GCP_PROJECT_REFRESH_INTERVAL_ENV,
)
from integrations.config_models import GCPIntegrationConfig
from integrations.gcp.client import (
    RESOURCE_MANAGER_API,
    GCPClientError,
    build_service,
    describe_api_error,
)
from integrations.gcp.refresh import refresh_interval

logger = logging.getLogger(__name__)

#: One page of Resource Manager results. An estate larger than this is real but
#: rare, and every project here is carried in tool params on each GCP call, so
#: an unbounded list would spend context on projects nobody asked about. When
#: the cap bites it is reported rather than silently applied — see
#: :attr:`DiscoveryResult.truncated`.
MAX_DISCOVERED = 200

#: Only ``ACTIVE`` projects are usable; ``DELETE_REQUESTED`` ones still list.
_ACTIVE = "ACTIVE"


def _now() -> float:
    """Monotonic seconds. A function so tests can drive expiry without sleeping.

    Monotonic rather than wall clock: an NTP step backwards would otherwise
    pin a cached listing until the clock caught up.
    """
    return time.monotonic()


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of one Resource Manager listing.

    ``error`` and ``projects`` are not mutually exclusive: a partially
    successful multi-credential expansion reports both.

    ``exception`` is carried rather than reported here on purpose. This module
    is called both from a tool (``gcp_list_projects``, which owes Sentry an
    event tagged with its own name) and from allow-list expansion, which is not
    a tool call and has no name to report under. Reporting centrally would have
    to invent one, and every discovery failure in the process would then be
    attributed to whichever caller the constant named.
    """

    projects: tuple[str, ...] = ()
    error: str = ""
    truncated: bool = False
    exception: Exception | None = None

    @property
    def attempted(self) -> bool:
        """Whether a listing ran at all, as opposed to discovery being off."""
        return bool(self.projects or self.error)


@dataclass(frozen=True)
class _Entry:
    """One cached listing and the moment it stops being served.

    ``expires_at`` is on the entry rather than derived from a stored timestamp
    plus the current interval, so an interval change cannot retroactively expire
    — or un-expire — a listing that is already in hand.
    """

    result: DiscoveryResult
    expires_at: float

    def fresh_at(self, now: float) -> bool:
        return now < self.expires_at


@dataclass
class _Cache:
    """Process-wide memo of discovery per credential.

    The lock guards the dict, deliberately *not* the network call: holding it
    across ``projects.list`` would serialize every GCP tool call in the process
    behind one request. A concurrent expiry therefore duplicates the listing
    once and then converges — cheap, and the alternative is every tool call in
    the process queueing behind one Google request.
    """

    entries: dict[str, _Entry] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, key: str) -> _Entry | None:
        with self.lock:
            return self.entries.get(key)

    def put(self, key: str, entry: _Entry) -> None:
        with self.lock:
            self.entries[key] = entry

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()


_cache = _Cache()


def wants_discovery(values: object) -> bool:
    """Whether ``values`` asks for live discovery.

    Accepts the shapes ``additional_projects`` travels in: the raw
    comma-separated env string, the validated list, or neither.
    """
    return any(item == GCP_DISCOVER_PROJECTS_TOKEN for item in _as_items(values))


def literal_projects(values: object) -> list[str]:
    """Return ``values`` without the discovery token.

    The token is a directive, not a project id. Letting it reach a project list
    is the exact failure ``"*"`` produces — a name that passes every local check
    and is rejected by Google, in a request that may carry real projects down
    with it.
    """
    return [item for item in _as_items(values) if item != GCP_DISCOVER_PROJECTS_TOKEN]


def _as_items(values: object) -> list[str]:
    """Normalize an env string / list / anything else to stripped entries."""
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, (list, tuple)):
        return []
    return [text for text in (str(item).strip() for item in values) if text]


def _cache_key(config: GCPIntegrationConfig) -> str:
    """Key on the credential alone — that is what decides the visible estate.

    Neither the project id nor the configured extras belong here: two instances
    sharing one service account see the same hierarchy regardless of which slice
    of it each was told about, and keying on them would re-list per instance for
    an identical answer.

    Hashed so a service-account key is not held a second time as a dict key,
    where any incidental dump of the cache would print it.
    """
    material = f"{config.service_account_key}\x00{config.impersonate_service_account}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def list_visible_projects(config: GCPIntegrationConfig) -> DiscoveryResult:
    """Ask Cloud Resource Manager what ``config``'s credential can see. Uncached."""
    try:
        service = build_service(config, RESOURCE_MANAGER_API)
        response = service.projects().list(pageSize=MAX_DISCOVERED).execute()
    except GCPClientError as exc:
        return DiscoveryResult(error=str(exc))
    except Exception as exc:  # noqa: BLE001 — every failure degrades to configured-only
        return DiscoveryResult(error=describe_api_error(exc), exception=exc)

    found = tuple(
        str(item.get("projectId", ""))
        for item in (response.get("projects") or [])
        if isinstance(item, dict)
        and item.get("projectId")
        and item.get("lifecycleState", _ACTIVE) == _ACTIVE
    )
    return DiscoveryResult(projects=found, truncated=bool(response.get("nextPageToken")))


def discover(config: GCPIntegrationConfig) -> DiscoveryResult:
    """Cached :func:`list_visible_projects`, re-listed once the TTL lapses."""
    key = _cache_key(config)
    now = _now()
    cached = _cache.get(key)
    if cached is not None and cached.fresh_at(now):
        return cached.result

    result = list_visible_projects(config)
    previous = cached.result if cached is not None else None
    if result.error and previous is not None and previous.projects:
        # Rule 2 in the module docstring: a refresh may only widen. Keep the
        # listing that worked and try again next expiry, rather than narrowing
        # the allow-list under an investigation that is already running.
        logger.warning(
            "GCP project re-discovery failed for the credential covering %s; "
            "keeping the %d project(s) found previously (%s)",
            config.project_id or "the default project",
            len(previous.projects),
            result.error,
        )
        _cache.put(key, _Entry(previous, now + refresh_interval(GCP_PROJECT_REFRESH_INTERVAL_ENV)))
        return previous

    _log_outcome(result, config.project_id, previous)
    _cache.put(key, _Entry(result, now + refresh_interval(GCP_PROJECT_REFRESH_INTERVAL_ENV)))
    return result


def _log_outcome(
    result: DiscoveryResult,
    fallback_project: str,
    previous: DiscoveryResult | None,
) -> None:
    """Report a listing at a level matched to whether it is news.

    A success that found the same projects as last time is logged at debug: the
    TTL makes this the common case, and an info line every interval saying
    nothing changed is the kind of noise that trains an operator to filter out
    the line that eventually does matter.
    """
    covering = fallback_project or "the default project"
    if result.error:
        # Warning, not error: the deployment still works on its configured
        # projects. Not repeated per tool call — the failure is cached too.
        logger.warning(
            "GCP project discovery failed for the credential covering %s; "
            "continuing with configured projects only (%s)",
            covering,
            result.error,
        )
        return
    if result.truncated:
        logger.warning(
            "GCP project discovery returned more than %d projects; the allow-list is "
            "capped there. Name the projects you need in %s instead.",
            MAX_DISCOVERED,
            GCP_ADDITIONAL_PROJECTS_ENV,
        )
        return
    if previous is not None and previous.projects == result.projects:
        logger.debug(
            "GCP project discovery: unchanged at %d project(s) for the credential covering %s.",
            len(result.projects),
            covering,
        )
        return
    logger.info(
        "GCP project discovery: %d project(s) visible to the credential covering %s.",
        len(result.projects),
        covering,
    )


def reset_cache() -> None:
    """Forget every cached listing, so the next call re-lists.

    Used by ``gcp_refresh_discovery`` and by tests. Note what this does *not*
    do: it discards the last good listing rather than refreshing it, so a
    forced re-read that then fails falls back to configured-only. That is the
    right trade for an explicit operator action — it is how you clear a cached
    failure — but it is why nothing calls this on a timer.
    """
    _cache.clear()


__all__ = [
    "MAX_DISCOVERED",
    "DiscoveryResult",
    "discover",
    "list_visible_projects",
    "literal_projects",
    "reset_cache",
    "wants_discovery",
]
