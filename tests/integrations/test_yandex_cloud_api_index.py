"""The agent must be able to reach any readable Yandex Cloud endpoint.

Only a handful of the API's ~900 read endpoints get a purpose-built tool. The
rest are reached by looking the path up in an index generated from Yandex's own
protobuf definitions and passing it to the generic reader — never by guessing a
path, and never by shelling out to the `yc` CLI, which is not installed and
authenticates separately.
"""

from __future__ import annotations

import json
import re

import pytest

from integrations.yandex_cloud.api_index import (
    _INDEX_FILE,
    canonical_service,
    endpoint_count,
    known_services,
    lookup,
    provenance,
    search,
)
from integrations.yandex_cloud.endpoints import resolve_endpoint
from tools.registry import get_registered_tool_map


class TestTheIndexIsUsable:
    def test_it_covers_the_whole_api_not_a_handful_of_services(self) -> None:
        assert endpoint_count() > 900
        assert len(known_services()) > 65

    def test_every_service_resolves_to_a_reachable_host(self) -> None:
        """An indexed path nothing can be sent to is worse than an absent one."""
        unreachable = [name for name in known_services() if not resolve_endpoint(name)]

        assert unreachable == []

    def test_it_holds_reads_only(self) -> None:
        """A get: binding is not proof a method only reads.

        OperationService.Cancel is bound to GET /operations/{id}:cancel, so
        filtering on the HTTP verb alone let a write path into an index whose
        entire purpose is to hold none. The RPC name is checked as well, by
        prefix: Yandex names mutations GetFoo/ListFoo versus CancelFoo, so a
        prefix match catches CancelOperation as well as bare Cancel.
        """
        entries = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))["endpoints"]
        verbs = [entry["rpc"].rsplit(".", 1)[-1] for entry in entries]
        mutating = [
            verb
            for verb in verbs
            for prefix in (
                "Cancel",
                "Create",
                "Delete",
                "Deploy",
                "Disable",
                "Enable",
                "Move",
                "Rebalance",
                "Reset",
                "Restart",
                "Rollback",
                "Start",
                "Stop",
                "Update",
                "Upsert",
            )
            if verb.startswith(prefix)
        ]

        assert mutating == []

    def test_no_path_carries_a_mutating_action_suffix(self) -> None:
        """The client would refuse these anyway; they must not be advertised either."""
        entries = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))["endpoints"]
        offenders = [
            entry["path"]
            for entry in entries
            if any(
                entry["path"].rsplit("/", 1)[-1].lower().endswith(action)
                for action in (":cancel", ":stop", ":start", ":restart", ":delete", ":move")
            )
        ]

        assert offenders == []

    def test_paths_are_paths_not_urls(self) -> None:
        entries = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))["endpoints"]

        assert all(entry["path"].startswith("/") for entry in entries)
        assert not any("://" in entry["path"] for entry in entries)


class TestTheIndexSaysWhereItCameFrom:
    """Generated data with no provenance cannot be told apart from stale data.

    Nothing about a list of endpoints reveals its age, so the file records the
    cloudapi commit it was built from. Without that, the only way to know
    whether a missing service is genuinely missing or merely not regenerated
    yet is to rebuild and diff.
    """

    def test_it_names_the_repository_it_was_generated_from(self) -> None:
        assert provenance()["source"] == "https://github.com/yandex-cloud/cloudapi"

    def test_it_pins_the_exact_commit(self) -> None:
        commit = provenance()["commit"]

        assert len(commit) == 40
        assert all(character in "0123456789abcdef" for character in commit)

    def test_it_carries_both_dates(self) -> None:
        """When cloudapi was last changed, and when the index was built from it."""
        recorded = provenance()

        for key in ("commit_date", "generated"):
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", recorded[key]), key

    def test_the_endpoints_are_not_mistaken_for_provenance(self) -> None:
        assert "endpoints" not in provenance()


class TestServicesTheNamingGapUsedToHide:
    """The generator maps a proto package to an endpoint host by name.

    Where the registry hyphenates and the protos do not, that mapping found
    nothing and the endpoints were dropped without a word — 34 of them across
    six services, which looked from the outside like Yandex not offering an API.
    An explicit alias table covers the cases; these keep it honest.
    """

    @pytest.mark.parametrize(
        "service",
        [
            "clouddesktops",
            "smart-web-security",
            "smart-captcha",
            "connection-manager",
            "serverless-mcp-gateway",
            "managed-ytsaurus",
        ],
    )
    def test_the_service_is_reachable(self, service: str) -> None:
        assert service in known_services()
        assert resolve_endpoint(service)

    def test_every_alias_points_at_a_host_that_exists(self) -> None:
        """A typo in the alias table would silently drop endpoints again."""
        from integrations.yandex_cloud.build_api_index import _REGISTRY_ALIASES

        unresolvable = [name for name in _REGISTRY_ALIASES.values() if not resolve_endpoint(name)]

        assert unresolvable == []


class TestEveryEntryHasTheShapeTheReaderExpects:
    def test_the_required_fields_are_present_and_filled(self) -> None:
        entries = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))["endpoints"]

        assert entries
        for entry in entries:
            for field in ("service", "path", "rpc", "package"):
                assert entry.get(field), f"{field} missing in {entry}"

    def test_declared_parameters_are_named(self) -> None:
        """A parameter without a name tells the agent nothing it can act on."""
        entries = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))["endpoints"]

        for entry in entries:
            for param in entry.get("params", ()):
                assert param.get("name"), f"unnamed parameter in {entry['path']}"

    def test_no_endpoint_is_listed_twice_for_one_service(self) -> None:
        entries = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))["endpoints"]
        keys = [(entry["service"], entry["path"]) for entry in entries]

        assert len(keys) == len(set(keys))


class TestSearchFindsWhatAnOperatorWouldAsk:
    @pytest.mark.parametrize(
        ("query", "expected_path"),
        [
            ("instances", "/compute/v1/instances"),
            ("security groups", "/vpc/v1/securityGroups"),
            ("postgresql clusters", "/managed-postgresql/v1/clusters"),
            ("kubernetes clusters", "/managed-kubernetes/v1/clusters"),
            ("certificates", "/certificate-manager/v1/certificates"),
            ("registries", "/container-registry/v1/registries"),
            ("dns zones", "/dns/v1/zones"),
            ("secrets", "/lockbox/v1/secrets"),
        ],
    )
    def test_plain_words_reach_the_right_endpoint(self, query: str, expected_path: str) -> None:
        assert expected_path in [match.path for match in search(query)]

    @pytest.mark.parametrize(
        ("query", "expected_service"),
        [
            ("instances", "compute"),
            ("vm", "compute"),
            ("k8s nodes", "managed-kubernetes"),
            ("postgres clusters", "managed-postgresql"),
            ("logs", "logging"),
            ("certs", "certificate-manager"),
        ],
    )
    def test_the_first_hit_is_the_one_worth_calling(
        self, query: str, expected_service: str
    ) -> None:
        """Several services own a resource called `instances`; the obvious one must win."""
        assert search(query)[0].service == expected_service

    def test_shorthand_an_operator_would_type_still_finds_it(self) -> None:
        """ "k8s" and "vm" appear nowhere in the API — without aliases they read as
        "Yandex Cloud cannot do that"."""
        assert search("k8s clusters")
        assert search("vms")

    def test_a_term_does_not_match_the_middle_of_another_word(self) -> None:
        """Substring matching sent `logs` to /v1/catalogs, which is not a log at all."""
        assert all("catalog" not in match.path.lower() for match in search("logs"))

    def test_collections_rank_above_single_resource_reads(self) -> None:
        """An agent with no id yet needs the list endpoint, not the get-by-id one."""
        first = search("compute instances")[0]

        assert "{" not in first.path

    def test_a_service_filter_narrows_a_shared_word(self) -> None:
        matches = search("clusters", service="managed-clickhouse")

        assert matches
        assert {match.service for match in matches} == {"managed-clickhouse"}


class TestTheToolAnswers:
    def test_it_returns_paths_ready_for_the_generic_reader(self) -> None:
        result = get_registered_tool_map()["find_yc_api"].run(query="security groups", limit=2)

        assert result["count"] > 0
        first = result["endpoints"][0]
        assert first["service"] and first["path"].startswith("/")

    def test_no_query_lists_the_services_instead_of_failing(self) -> None:
        """ "What can you see?" is a real question, and it has an answer."""
        result = get_registered_tool_map()["find_yc_api"].run()

        assert len(result["services"]) > 50
        assert result["count"] == 0

    def test_an_unmatched_search_says_so_without_inventing_a_path(self) -> None:
        result = get_registered_tool_map()["find_yc_api"].run(query="zzzz-not-a-resource")

        assert result["endpoints"] == []
        assert "note" in result


class TestParametersComeWithThePath:
    """A path alone is half an answer: the cluster-logs endpoint answers
    "unknown service type" until serviceType is passed, and the path says nothing."""

    def test_the_cluster_logs_endpoint_carries_its_service_type(self) -> None:
        match = next(
            endpoint for endpoint in search("postgresql logs") if endpoint.path.endswith(":logs")
        )
        params = {param["name"]: param for param in match.params}

        assert "serviceType" in params
        assert "POSTGRESQL" in params["serviceType"]["values"]

    def test_the_tool_hands_them_to_the_caller(self) -> None:
        result = get_registered_tool_map()["find_yc_api"].run(query="postgresql logs", limit=1)

        assert result["endpoints"][0]["params"]

    def test_paging_is_not_repeated_per_endpoint(self) -> None:
        """pageSize/pageToken are uniform across the API; listing them is noise."""
        names = {param["name"] for endpoint in search("clusters") for param in endpoint.params}

        assert "pageSize" not in names
        assert "pageToken" not in names


class TestTheShellCanUseThem:
    """The interactive shell only sees the action surface; investigation-only tools
    leave it shelling out to a `yc` binary that is not there."""

    @pytest.mark.parametrize(
        "name",
        [
            "find_yc_api",
            "execute_yc_operation",
            "query_yc_metrics",
            "list_yc_metrics",
            "read_yc_logs",
            "list_yc_log_groups",
            "get_yc_lb_health",
        ],
    )
    def test_it_is_on_the_action_surface(self, name: str) -> None:
        assert "action" in get_registered_tool_map()[name].surfaces

    def test_metrics_and_logs_are_there_because_the_generic_reader_cannot_reach_them(
        self,
    ) -> None:
        """Monitoring reads over POST and logs over gRPC, so GET cannot substitute."""
        tools = get_registered_tool_map()

        for name in ("query_yc_metrics", "read_yc_logs"):
            assert "action" in tools[name].surfaces
        # Plain GET resources stay off the action surface: execute_yc_operation covers them.
        assert "action" not in tools["list_yc_instances"].surfaces


class TestLookupIsTheAllowlist:
    """execute_yc_operation may send only what lookup accepts."""

    def test_a_listed_collection_resolves(self) -> None:
        match = lookup("compute", "/compute/v1/instances")

        assert match is not None
        assert match.path == "/compute/v1/instances"

    def test_a_concrete_id_matches_the_templated_get(self) -> None:
        match = lookup("compute", "/compute/v1/instances/abc")

        assert match is not None
        assert (
            "{instance_id}" in match.path
            or "{instanceId}" in match.path
            or "instances/" in match.path
        )

    def test_an_action_suffix_does_not_match_the_plain_resource(self) -> None:
        listed = lookup("compute", "/compute/v1/instances/abc:serialPortOutput")
        plain = lookup("compute", "/compute/v1/instances/abc")

        assert plain is not None
        if listed is not None:
            assert listed.path != plain.path

    def test_a_path_the_index_does_not_list_is_none(self) -> None:
        assert lookup("compute", "/compute/v1/notARealCollection") is None

    def test_a_real_path_on_the_wrong_service_is_none(self) -> None:
        assert lookup("iam", "/compute/v1/instances") is None


class TestServiceNamesTheToolsActuallyUse:
    """The index answers to the names the tools use, not only the proto names."""

    def test_the_tools_alb_name_reaches_the_index(self) -> None:
        assert lookup("alb", "/apploadbalancer/v1/loadBalancers") is not None

    def test_a_canonical_name_still_works(self) -> None:
        assert lookup("apploadbalancer", "/apploadbalancer/v1/loadBalancers") is not None

    def test_an_unknown_service_stays_unknown(self) -> None:
        assert lookup("not-a-service", "/x/v1/y") is None

    @pytest.mark.parametrize("service", ["ALB", "Alb", " alb "])
    def test_the_index_is_reached_however_the_name_is_typed(self, service: str) -> None:
        """Host resolution case-folds, so the allowlist has to as well.

        A name that reads as unknown here and as a real host to the client is a
        way past the gate, not a typo.
        """
        assert canonical_service(service) in known_services()
        assert lookup(service, "/apploadbalancer/v1/loadBalancers") is not None
