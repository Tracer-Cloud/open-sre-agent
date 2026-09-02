"""The Yandex Cloud tool family has to fit the per-turn schema budget."""

from __future__ import annotations

import pytest

from tools.registry import get_registered_tool_map


class TestRegistration:
    @pytest.mark.parametrize(
        "name",
        [
            "find_yc_api",
            "execute_yc_operation",
            "query_yc_metrics",
            "list_yc_metrics",
            "read_yc_logs",
            "list_yc_log_groups",
            "list_yc_instances",
            "get_yc_instance_diagnostics",
            "get_yc_lb_health",
            "list_yc_db_clusters",
            "get_yc_db_cluster",
            "read_yc_db_logs",
        ],
    )
    def test_the_tool_is_discoverable(self, name: str) -> None:
        assert name in get_registered_tool_map()

    def test_the_family_stays_inside_the_schema_budget(self) -> None:
        """Thirty-two schemas reach the model per turn, shared with everything else.

        Seventeen is the line for this whole family, and it is meant to be
        defended rather than raised. The pressure is real: connecting a
        Kubernetes cluster brings twelve more tools from OpenSRE's own
        integration, and an investigation with both has already been observed
        dropping tools it could not fit. Later families land under this same
        ceiling, so an addition should come from merging two existing tools
        rather than from moving this number.
        """
        family = {
            "find_yc_api",
            "execute_yc_operation",
            "query_yc_metrics",
            "list_yc_metrics",
            "read_yc_logs",
            "list_yc_log_groups",
            "list_yc_instances",
            "get_yc_instance_diagnostics",
            "get_yc_lb_health",
            "list_yc_db_clusters",
            "get_yc_db_cluster",
            "read_yc_db_logs",
        }
        registered = get_registered_tool_map()
        yc_tools = [name for name in registered if name in family]

        assert set(yc_tools) == family
        assert len(yc_tools) <= 17
