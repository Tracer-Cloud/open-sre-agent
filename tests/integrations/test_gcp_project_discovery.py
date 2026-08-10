"""``GCP_ADDITIONAL_PROJECTS=discover``: token handling, expansion, and caching.

The behaviours pinned here are the ones whose failure is silent. A leaked token
looks like a configured project until Google rejects it; a discovery failure
that widened instead of narrowed would grant read access nobody granted; and a
cache that missed would put a Resource Manager round trip in front of every GCP
tool call without anything in the output saying so.
"""

from __future__ import annotations

from typing import Any

import pytest

from config.constants.gcp import GCP_PROJECT_REFRESH_INTERVAL_ENV
from integrations.config_models import GCPIntegrationConfig
from integrations.gcp import project_discovery
from integrations.gcp.project_discovery import (
    DiscoveryResult,
    discover,
    literal_projects,
    wants_discovery,
)
from integrations.gcp.projects import MAX_RESOURCE_NAMES, resolve_projects, resource_name_batches
from integrations.gcp.tool_params import gcp_tool_params


class _Listing:
    """Counts calls so "cached" can be asserted as a fact, not inferred."""

    def __init__(self, *results: DiscoveryResult) -> None:
        self._results = list(results)
        self.configs: list[GCPIntegrationConfig] = []

    def __call__(self, config: GCPIntegrationConfig) -> DiscoveryResult:
        self.configs.append(config)
        # Repeat the last result once exhausted: a test asserting the cache
        # holds must not pass merely because the fake ran out of answers.
        index = min(len(self.configs) - 1, len(self._results) - 1)
        return self._results[index]

    @property
    def calls(self) -> int:
        return len(self.configs)


def _sources(**overrides: Any) -> dict[str, Any]:
    payload = {"project_id": "acme", "additional_projects": "discover"}
    payload.update(overrides)
    return {"gcp": GCPIntegrationConfig.model_validate(payload).model_dump()}


# --- token handling ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["discover", ["discover"], " discover , extra ", ["extra", "discover"]],
)
def test_wants_discovery_reads_every_shape_the_value_travels_in(raw: object) -> None:
    """The value reaches this code as an env string, a validated list, or neither."""
    assert wants_discovery(raw) is True


@pytest.mark.parametrize("raw", ["", None, "acme,other", ["acme"], 42, {"a": 1}])
def test_wants_discovery_is_false_for_anything_else(raw: object) -> None:
    assert wants_discovery(raw) is False


def test_literal_projects_drops_the_token_and_keeps_the_rest() -> None:
    assert literal_projects(" discover , pinned , ") == ["pinned"]


def test_the_token_never_becomes_a_project_id() -> None:
    """Regression guard for the failure ``GCP_ADDITIONAL_PROJECTS="*"`` produced.

    A directive that survives into a project list passes every local check and
    is rejected by Google — and Cloud Logging validates one request's
    ``resourceNames`` as a set, so it takes the real projects down with it
    rather than degrading. Neither accessor may emit it.
    """
    config = GCPIntegrationConfig.model_validate(
        {"project_id": "acme", "additional_projects": "discover,pinned"}
    )

    assert config.discovery_requested is True
    assert config.all_projects == ["acme", "pinned"]
    assert "discover" not in config.all_projects


def test_a_project_genuinely_named_discover_is_still_reachable() -> None:
    """The token shadows the id only in ``additional_projects``, not as primary."""
    config = GCPIntegrationConfig.model_validate({"project_id": "discover"})

    assert config.all_projects == ["discover"]
    assert config.discovery_requested is False


# --- expansion into the allow-list -------------------------------------------


def test_discovered_projects_join_the_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    listing = _Listing(DiscoveryResult(projects=("acme", "found-a", "found-b")))
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)

    params = gcp_tool_params(_sources())

    assert params["available_projects"] == ["acme", "found-a", "found-b"]
    projects, error = resolve_projects(
        "found-b", default_project="acme", available_projects=params["available_projects"]
    )
    assert (projects, error) == (["found-b"], None)


def test_the_configured_project_stays_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resource Manager's ordering must not decide which project is the default."""
    listing = _Listing(DiscoveryResult(projects=("zzz-first", "acme")))
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)

    params = gcp_tool_params(_sources())

    assert params["default_project"] == "acme"
    assert params["available_projects"][0] == "acme"


def test_pinned_projects_survive_alongside_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    listing = _Listing(DiscoveryResult(projects=("found",)))
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)

    params = gcp_tool_params(_sources(additional_projects="discover,pinned"))

    assert params["available_projects"] == ["acme", "pinned", "found"]


def test_no_token_means_no_api_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery is opt-in; the default deployment must stay offline here."""
    listing = _Listing(DiscoveryResult(projects=("should-not-appear",)))
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)

    params = gcp_tool_params(_sources(additional_projects="pinned"))

    assert listing.calls == 0
    assert params["available_projects"] == ["acme", "pinned"]


# --- failure must narrow, never widen ----------------------------------------


def test_a_failed_listing_falls_back_to_configured_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = _Listing(DiscoveryResult(error="HTTP 403: permission denied"))
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)

    params = gcp_tool_params(_sources(additional_projects="discover,pinned"))

    assert params["available_projects"] == ["acme", "pinned"]
    # And the tools still reject what was never configured, rather than opening
    # up because the allow-list came back short.
    _projects, error = resolve_projects(
        "somewhere-else", default_project="acme", available_projects=params["available_projects"]
    )
    assert error is not None


def test_a_failed_listing_is_not_retried_on_every_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The common failure is a missing grant — permanent, and paid per call."""
    listing = _Listing(DiscoveryResult(error="HTTP 403: permission denied"))
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)

    for _ in range(5):
        gcp_tool_params(_sources())

    assert listing.calls == 1


# --- caching ------------------------------------------------------------------


def test_one_listing_serves_every_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    listing = _Listing(DiscoveryResult(projects=("acme", "found")))
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)

    results = [gcp_tool_params(_sources())["available_projects"] for _ in range(4)]

    assert listing.calls == 1
    assert results == [["acme", "found"]] * 4


def test_separate_credentials_are_cached_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two service accounts see two estates; one cache entry would merge them."""
    listing = _Listing(
        DiscoveryResult(projects=("estate-one",)),
        DiscoveryResult(projects=("estate-two",)),
    )
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)

    first = discover(GCPIntegrationConfig(project_id="a", service_account_key='{"type":"one"}'))
    second = discover(GCPIntegrationConfig(project_id="b", service_account_key='{"type":"two"}'))

    assert listing.calls == 2
    assert first.projects == ("estate-one",)
    assert second.projects == ("estate-two",)


def test_the_same_credential_is_not_relisted_for_a_second_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The credential decides the visible estate; the project id is irrelevant."""
    listing = _Listing(DiscoveryResult(projects=("shared",)))
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)

    discover(GCPIntegrationConfig(project_id="a", impersonate_service_account="sa@x"))
    discover(GCPIntegrationConfig(project_id="b", impersonate_service_account="sa@x"))

    assert listing.calls == 1


def test_the_cache_key_does_not_hold_the_service_account_key() -> None:
    """A credential must not sit in a dict key any casual dump would print."""
    secret = '{"private_key":"SUPER-SECRET"}'

    key = project_discovery._cache_key(
        GCPIntegrationConfig(project_id="a", service_account_key=secret)
    )

    assert "SUPER-SECRET" not in key
    assert len(key) == 64  # sha256 hex


# --- the TTL ------------------------------------------------------------------


class _Clock:
    """A monotonic clock the test advances by hand.

    Sleeping for a real TTL is not an option, and shortening the TTL to
    milliseconds would test a configuration nothing runs: ``refresh_interval``
    clamps anything under a minute.
    """

    def __init__(self) -> None:
        self.seconds = 1000.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(project_discovery, "_now", fake)
    return fake


def test_a_listing_is_not_repeated_before_the_ttl_lapses(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    listing = _Listing(DiscoveryResult(projects=("acme",)))
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)
    monkeypatch.setenv(GCP_PROJECT_REFRESH_INTERVAL_ENV, "600")

    discover(GCPIntegrationConfig(project_id="acme"))
    clock.advance(599)
    discover(GCPIntegrationConfig(project_id="acme"))

    assert listing.calls == 1


def test_a_project_created_after_boot_appears_once_the_ttl_lapses(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    """The whole point of the TTL: no restart to see a new project."""
    listing = _Listing(
        DiscoveryResult(projects=("acme",)),
        DiscoveryResult(projects=("acme", "created-just-now")),
    )
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)
    monkeypatch.setenv(GCP_PROJECT_REFRESH_INTERVAL_ENV, "600")

    first = discover(GCPIntegrationConfig(project_id="acme"))
    clock.advance(601)
    second = discover(GCPIntegrationConfig(project_id="acme"))

    assert listing.calls == 2
    assert first.projects == ("acme",)
    assert second.projects == ("acme", "created-just-now")


def test_a_failed_refresh_keeps_the_last_good_listing(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    """A refresh may only widen.

    Replacing a good listing with a failure would narrow an allow-list that
    worked a minute ago — an investigation already querying ``found`` would
    start getting "unknown GCP project" for it, and the cause would look like
    the project, not like a blip at Google.
    """
    listing = _Listing(
        DiscoveryResult(projects=("acme", "found")),
        DiscoveryResult(error="HTTP 503: backend error"),
    )
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)
    monkeypatch.setenv(GCP_PROJECT_REFRESH_INTERVAL_ENV, "600")

    discover(GCPIntegrationConfig(project_id="acme"))
    clock.advance(601)
    after_failure = discover(GCPIntegrationConfig(project_id="acme"))

    assert listing.calls == 2
    assert after_failure.projects == ("acme", "found")
    assert after_failure.error == ""


def test_a_failed_refresh_does_not_retry_until_the_next_expiry(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    """Keeping the old listing must not also mean re-listing on every call.

    Extending the expiry is what separates "serve the stale answer" from "treat
    the entry as permanently expired" — which would put a failing Resource
    Manager call in front of every GCP tool call for as long as the outage.
    """
    listing = _Listing(
        DiscoveryResult(projects=("acme", "found")),
        DiscoveryResult(error="HTTP 503: backend error"),
    )
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)
    monkeypatch.setenv(GCP_PROJECT_REFRESH_INTERVAL_ENV, "600")

    discover(GCPIntegrationConfig(project_id="acme"))
    clock.advance(601)
    discover(GCPIntegrationConfig(project_id="acme"))
    for _ in range(5):
        clock.advance(10)
        discover(GCPIntegrationConfig(project_id="acme"))

    assert listing.calls == 2


def test_a_failure_with_nothing_to_fall_back_on_is_still_cached(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    """Boot-time behaviour is unchanged: the first failure is the cached answer."""
    listing = _Listing(DiscoveryResult(error="HTTP 403: permission denied"))
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)
    monkeypatch.setenv(GCP_PROJECT_REFRESH_INTERVAL_ENV, "600")

    first = discover(GCPIntegrationConfig(project_id="acme"))
    clock.advance(10)
    second = discover(GCPIntegrationConfig(project_id="acme"))

    assert listing.calls == 1
    assert first.error == second.error == "HTTP 403: permission denied"


def test_a_recovered_credential_widens_the_listing_again(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    """A cached failure must expire too, or a fixed IAM grant needs a restart."""
    listing = _Listing(
        DiscoveryResult(error="HTTP 403: permission denied"),
        DiscoveryResult(projects=("acme", "now-visible")),
    )
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)
    monkeypatch.setenv(GCP_PROJECT_REFRESH_INTERVAL_ENV, "600")

    discover(GCPIntegrationConfig(project_id="acme"))
    clock.advance(601)
    recovered = discover(GCPIntegrationConfig(project_id="acme"))

    assert recovered.projects == ("acme", "now-visible")
    assert recovered.error == ""


def test_refreshing_can_be_switched_off_entirely(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    """``0`` restores the original behaviour: one listing for the process."""
    listing = _Listing(DiscoveryResult(projects=("acme",)))
    monkeypatch.setattr(project_discovery, "list_visible_projects", listing)
    monkeypatch.setenv(GCP_PROJECT_REFRESH_INTERVAL_ENV, "0")

    discover(GCPIntegrationConfig(project_id="acme"))
    clock.advance(10_000_000)
    discover(GCPIntegrationConfig(project_id="acme"))

    assert listing.calls == 1


# --- request batching ---------------------------------------------------------


def test_an_ordinary_estate_is_still_one_request() -> None:
    """Batching must not cost the common case an extra round trip."""
    assert resource_name_batches(["a", "b", "c"]) == [["projects/a", "projects/b", "projects/c"]]


def test_no_projects_means_no_requests() -> None:
    assert resource_name_batches([]) == []


def test_a_discovered_estate_is_split_below_the_api_ceiling() -> None:
    """Cloud Logging caps ``resourceNames`` at 100 and fails the whole call above it.

    This is the batching ``discover`` exists to require: an org-level grant can
    resolve ``project="*"`` to hundreds of projects, and unbatched that makes
    the broadest query the only one guaranteed to fail.
    """
    batches = resource_name_batches([f"p{index}" for index in range(250)])

    assert [len(batch) for batch in batches] == [100, 100, 50]
    assert all(len(batch) <= MAX_RESOURCE_NAMES for batch in batches)
    # Every project appears exactly once, in order — a split must not drop or
    # duplicate a project, which would silently skew the log window.
    flattened = [name for batch in batches for name in batch]
    assert flattened == [f"projects/p{index}" for index in range(250)]
