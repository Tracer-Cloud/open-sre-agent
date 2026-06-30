"""Enforce forbidden *direct* import edges between top-level packages.

Unlike import-linter (which flags transitive chains), this checker only
looks at top-level ``import`` / ``from … import`` statements — the same
AST walk as :mod:`infra.ci.check_import_cycles`. That makes it practical to
enforce layering incrementally: fix a direct edge, keep the contract.

Used by ``make check-imports`` (and :mod:`infra.ci.check_imports`) alongside
import-linter's config contract.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_CI_DIR = Path(__file__).resolve().parent
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from check_import_cycles import _build_graph, discover_first_party_roots  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ``source_prefix -> forbidden destination roots`` for direct imports only.
# Enforces the layering contract documented in ``surfaces/__init__.py``:
# "Nothing first-party may import from surfaces/". Adds an explicit bound
# on ``platform``, ``infra``, ``core``, ``gateway`` so the surfaces ban
# is CI-enforced, not just doc-described.
#
# Note: ``infra`` is excluded from the import-graph's first-party roots
# (see ``check_import_cycles._SKIP_ROOT_DIRS``), so its rule here is
# defensive — it activates the moment ``infra/`` is brought into the
# graph. Keep it documented so the intent is not lost.
_FORBIDDEN_DIRECT: dict[str, frozenset[str]] = {
    "platform": frozenset({"surfaces"}),
    "infra": frozenset({"surfaces"}),
    "core": frozenset({"surfaces"}),
    "gateway": frozenset({"surfaces"}),
    "integrations": frozenset({"tools", "surfaces"}),
    "tools": frozenset({"surfaces"}),
}

# Known direct violations being burned down — remove entries as fixes land.
# Format: ``"source.module -> dest.module"`` (exact modules from the graph).
_BASELINE_IGNORES: frozenset[str] = frozenset(
    {
        # Gateway hosts the interactive_shell runtime — pre-existing reuse
        # to be burned down by extracting shared runtime primitives out of
        # ``surfaces/interactive_shell/`` and into a layer below ``surfaces``.
        "gateway.storage.session.resolver -> surfaces.interactive_shell.runtime.context",
        # Per-vendor integration tool packages still depend on shared tool
        # primitives that live under ``tools/``: ``tools.base.BaseTool``
        # (class-based vendors), ``tools.tool_decorator.tool`` (decorator-
        # based vendors), and the payload helpers under ``tools.utils.*``.
        # Burn down by moving these primitives to a lower layer (likely
        # ``core/`` or ``platform/``) so vendor tools can import them
        # without crossing into ``tools``.
        "integrations.alertmanager.tools -> tools.base",
        "integrations.argocd.tools -> tools.base",
        "integrations.coralogix.tools -> tools.base",
        "integrations.dagster.tools -> tools.tool_decorator",
        "integrations.datadog.tools -> tools.tool_decorator",
        "integrations.datadog.tools -> tools.utils.availability",
        "integrations.datadog.tools -> tools.utils.compaction",
        "integrations.eks.tools -> tools._telemetry",
        "integrations.eks.tools -> tools.tool_decorator",
        "integrations.eks.tools -> tools.utils.availability",
        "integrations.eks.tools -> tools.utils.eks_workload_helper",
        "integrations.elasticsearch.tools -> tools.base",
        "integrations.elasticsearch.tools -> tools.utils.compaction",
        "integrations.google_docs.tools -> tools._telemetry",
        "integrations.google_docs.tools -> tools.tool_decorator",
        "integrations.grafana.tools -> tools.tool_decorator",
        "integrations.groundcover.tools -> tools.tool_decorator",
        "integrations.groundcover.tools -> tools.utils.availability",
        "integrations.groundcover.tools -> tools.utils.groundcover",
        "integrations.helm.tools -> tools.base",
        "integrations.helm.tools -> tools.utils.helm_tools",
        "integrations.honeycomb.tools -> tools.base",
        "integrations.incident_io.tools -> tools.base",
        "integrations.jenkins.tools -> tools.tool_decorator",
        "integrations.clickhouse.tools.clickhouse_query_activity_tool -> tools.tool_decorator",
        "integrations.clickhouse.tools.clickhouse_system_health_tool -> tools.tool_decorator",
        "integrations.jira.tools -> tools.base",
        "integrations.mariadb.tools.mariadb_innodb_status_tool -> tools.tool_decorator",
        "integrations.mariadb.tools.mariadb_process_list_tool -> tools.tool_decorator",
        "integrations.mariadb.tools.mariadb_process_list_tool -> tools.utils.sql_wrapper",
        "integrations.mariadb.tools.mariadb_replication_tool -> tools.tool_decorator",
        "integrations.mariadb.tools.mariadb_slow_queries_tool -> tools.tool_decorator",
        "integrations.mariadb.tools.mariadb_status_tool -> tools.tool_decorator",
        "integrations.mongodb.tools.mongodb_collection_stats_tool -> tools.tool_decorator",
        "integrations.mongodb.tools.mongodb_current_ops_tool -> tools.tool_decorator",
        "integrations.mongodb.tools.mongodb_profiler_tool -> tools.tool_decorator",
        "integrations.mongodb.tools.mongodb_replica_status_tool -> tools.tool_decorator",
        "integrations.mongodb.tools.mongodb_server_status_tool -> tools.tool_decorator",
        "integrations.mongodb_atlas.tools.mongodb_atlas_alerts_tool -> tools.tool_decorator",
        "integrations.mongodb_atlas.tools.mongodb_atlas_clusters_tool -> tools.tool_decorator",
        "integrations.mongodb_atlas.tools.mongodb_atlas_events_tool -> tools.tool_decorator",
        "integrations.mongodb_atlas.tools.mongodb_atlas_metrics_tool -> tools.tool_decorator",
        "integrations.mongodb_atlas.tools.mongodb_atlas_performance_advisor_tool -> tools.tool_decorator",
        "integrations.mysql.tools.mysql_current_processes_tool -> tools.tool_decorator",
        "integrations.mysql.tools.mysql_current_processes_tool -> tools.utils.sql_wrapper",
        "integrations.mysql.tools.mysql_replication_status_tool -> tools.tool_decorator",
        "integrations.mysql.tools.mysql_replication_status_tool -> tools.utils.sql_wrapper",
        "integrations.mysql.tools.mysql_server_status_tool -> tools.tool_decorator",
        "integrations.mysql.tools.mysql_server_status_tool -> tools.utils.sql_wrapper",
        "integrations.mysql.tools.mysql_slow_queries_tool -> tools.tool_decorator",
        "integrations.mysql.tools.mysql_slow_queries_tool -> tools.utils.sql_wrapper",
        "integrations.mysql.tools.mysql_table_stats_tool -> tools.tool_decorator",
        "integrations.mysql.tools.mysql_table_stats_tool -> tools.utils.sql_wrapper",
        "integrations.opsgenie.tools -> tools.base",
        "integrations.pagerduty.tools -> tools.base",
        "integrations.postgresql.tools.postgresql_current_queries_tool -> tools.tool_decorator",
        "integrations.postgresql.tools.postgresql_current_queries_tool -> tools.utils.sql_wrapper",
        "integrations.postgresql.tools.postgresql_locks_tool -> tools.tool_decorator",
        "integrations.postgresql.tools.postgresql_locks_tool -> tools.utils.sql_wrapper",
        "integrations.postgresql.tools.postgresql_replication_status_tool -> tools.tool_decorator",
        "integrations.postgresql.tools.postgresql_replication_status_tool -> tools.utils.sql_wrapper",
        "integrations.postgresql.tools.postgresql_server_status_tool -> tools.tool_decorator",
        "integrations.postgresql.tools.postgresql_server_status_tool -> tools.utils.sql_wrapper",
        "integrations.postgresql.tools.postgresql_slow_queries_tool -> tools.tool_decorator",
        "integrations.postgresql.tools.postgresql_slow_queries_tool -> tools.utils.sql_wrapper",
        "integrations.postgresql.tools.postgresql_table_stats_tool -> tools.tool_decorator",
        "integrations.postgresql.tools.postgresql_table_stats_tool -> tools.utils.sql_wrapper",
        "integrations.prefect.tools -> tools.base",
        "integrations.redis.tools.redis_client_list_tool -> tools.tool_decorator",
        "integrations.redis.tools.redis_key_scan_tool -> tools.tool_decorator",
        "integrations.redis.tools.redis_latency_doctor_tool -> tools.tool_decorator",
        "integrations.redis.tools.redis_list_depth_tool -> tools.tool_decorator",
        "integrations.redis.tools.redis_replication_tool -> tools.tool_decorator",
        "integrations.redis.tools.redis_server_info_tool -> tools.tool_decorator",
        "integrations.redis.tools.redis_slowlog_tool -> tools.tool_decorator",
        "integrations.signoz.tools -> tools.tool_decorator",
        "integrations.signoz.tools -> tools.utils.availability",
        "integrations.signoz.tools -> tools.utils.compaction",
        "integrations.snowflake.tools.snowflake_query_history_tool -> tools._telemetry",
        "integrations.snowflake.tools.snowflake_query_history_tool -> tools.tool_decorator",
        "integrations.splunk.tools -> tools.base",
        "integrations.splunk.tools -> tools.utils.compaction",
        "integrations.tempo.tools -> tools.tool_decorator",
        "integrations.tempo.tools -> tools.utils.availability",
        "integrations.temporal.tools -> tools.base",
        "integrations.vercel.tools -> tools.base",
        "integrations.victoria_logs.tools -> tools.base",
        "integrations.azure.tools.azure_monitor_logs_tool -> tools._telemetry",
        "integrations.azure.tools.azure_monitor_logs_tool -> tools.tool_decorator",
        "integrations.azure_sql.tools.azure_sql_current_queries_tool -> tools.tool_decorator",
        "integrations.azure_sql.tools.azure_sql_current_queries_tool -> tools.utils.sql_wrapper",
        "integrations.azure_sql.tools.azure_sql_resource_stats_tool -> tools.tool_decorator",
        "integrations.azure_sql.tools.azure_sql_server_status_tool -> tools.tool_decorator",
        "integrations.azure_sql.tools.azure_sql_slow_queries_tool -> tools.tool_decorator",
        "integrations.azure_sql.tools.azure_sql_slow_queries_tool -> tools.utils.sql_wrapper",
        "integrations.azure_sql.tools.azure_sql_wait_stats_tool -> tools.tool_decorator",
        "integrations.betterstack.tools.betterstack_logs_tool -> tools.tool_decorator",
        "integrations.hermes.tools.hermes_logs_tool -> tools.tool_decorator",
        "integrations.hermes.tools.hermes_logs_tool -> tools.utils.availability",
        "integrations.hermes.tools.hermes_session_evidence_tool -> tools.tool_decorator",
        "integrations.kafka.tools.kafka_consumer_group_tool -> tools.tool_decorator",
        "integrations.kafka.tools.kafka_topic_health_tool -> tools.tool_decorator",
        "integrations.openclaw.tools.openclaw_mcp_tool -> tools._telemetry",
        "integrations.openclaw.tools.openclaw_mcp_tool -> tools.tool_decorator",
        "integrations.openclaw.tools.openclaw_mcp_tool -> tools.utils.mcp_tool_listing",
        "integrations.openobserve.tools.openobserve_logs_tool -> tools._telemetry",
        "integrations.openobserve.tools.openobserve_logs_tool -> tools.tool_decorator",
        "integrations.opensearch.tools.opensearch_analytics_tool -> tools.tool_decorator",
        "integrations.posthog_mcp.tools.posthog_mcp_tool -> tools._telemetry",
        "integrations.posthog_mcp.tools.posthog_mcp_tool -> tools.tool_decorator",
        "integrations.posthog_mcp.tools.posthog_mcp_tool -> tools.utils.mcp_tool_listing",
        "integrations.rabbitmq.tools.rabbitmq_broker_overview_tool -> tools.tool_decorator",
        "integrations.rabbitmq.tools.rabbitmq_connection_stats_tool -> tools.tool_decorator",
        "integrations.rabbitmq.tools.rabbitmq_consumer_health_tool -> tools.tool_decorator",
        "integrations.rabbitmq.tools.rabbitmq_node_health_tool -> tools.tool_decorator",
        "integrations.rabbitmq.tools.rabbitmq_queue_backlog_tool -> tools.tool_decorator",
        "integrations.sentry.tools.sentry_issue_details_tool -> tools.tool_decorator",
        "integrations.sentry.tools.sentry_issue_events_tool -> tools.tool_decorator",
        "integrations.sentry.tools.sentry_search_issues_tool -> tools._telemetry",
        "integrations.sentry.tools.sentry_search_issues_tool -> tools.tool_decorator",
        "integrations.sentry_mcp.tools.sentry_mcp_tool -> tools._telemetry",
        "integrations.sentry_mcp.tools.sentry_mcp_tool -> tools.tool_decorator",
        "integrations.sentry_mcp.tools.sentry_mcp_tool -> tools.utils.mcp_tool_listing",
        "integrations.supabase.tools.supabase_health_tool -> tools.tool_decorator",
        "integrations.supabase.tools.supabase_storage_tool -> tools.tool_decorator",
        # Hermes Telegram sink reuses watch-dog alarm dispatch (#1500 refactor).
        "integrations.hermes.sinks -> tools.watch_dog.alarms",
        # Integration setup UX still reaches into the CLI wizard.
        "integrations.cli -> surfaces.cli.wizard.integration_health",
        # tools/interactive_shell — pre-existing cross-layer reuse migrated from interactive_shell -> surfaces.interactive_shell (T-1 #3299). Burn down by extracting the shared subprocess-runner + execution-confirm primitives into surfaces/shared/.
        "tools.interactive_shell.actions.cli_command -> surfaces.interactive_shell.runtime.subprocess_runner",
        "tools.interactive_shell.actions.investigation -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.actions.llm_provider -> surfaces.interactive_shell.command_registry",
        "tools.interactive_shell.actions.llm_provider -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.actions.sample_alert -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.actions.slash -> surfaces.interactive_shell.command_registry",
        "tools.interactive_shell.actions.slash -> surfaces.interactive_shell.command_registry.slash_catalog",
        "tools.interactive_shell.actions.slash -> surfaces.interactive_shell.ui",
        "tools.interactive_shell.actions.slash -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.actions.slash -> surfaces.interactive_shell.utils.telemetry.turn_outcome",
        "tools.interactive_shell.actions.task_cancel -> surfaces.interactive_shell.command_registry",
        "tools.interactive_shell.actions.task_cancel -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.actions.task_cancel -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.implementation.claude_code_executor -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.implementation.claude_code_executor -> surfaces.interactive_shell.runtime.subprocess_runner.task_streaming",
        "tools.interactive_shell.implementation.claude_code_executor -> surfaces.interactive_shell.ui",
        "tools.interactive_shell.implementation.claude_code_executor -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.implementation.claude_code_executor -> surfaces.interactive_shell.utils.error_handling.exception_reporting",
        "tools.interactive_shell.shared.investigation_launch -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.shared.investigation_launch -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.shared.investigation_launch -> surfaces.interactive_shell.ui.foreground_investigation",
        "tools.interactive_shell.shell.runner -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.shell.runner -> surfaces.interactive_shell.runtime.subprocess_runner.task_streaming",
        "tools.interactive_shell.shell.runner -> surfaces.interactive_shell.ui",
        "tools.interactive_shell.shell.runner -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.shell.runner -> surfaces.interactive_shell.utils.error_handling.exception_reporting",
        "tools.interactive_shell.synthetic.runner -> surfaces.interactive_shell.runtime",
        "tools.interactive_shell.synthetic.runner -> surfaces.interactive_shell.runtime.subprocess_runner.task_streaming",
        "tools.interactive_shell.synthetic.runner -> surfaces.interactive_shell.ui",
        "tools.interactive_shell.synthetic.runner -> surfaces.interactive_shell.ui.execution_confirm",
        "tools.interactive_shell.synthetic.runner -> surfaces.interactive_shell.utils.error_handling.exception_reporting",
    }
)


@dataclass(frozen=True)
class DirectViolation:
    source: str
    target: str

    @property
    def edge(self) -> str:
        return f"{self.source} -> {self.target}"


def _source_root(module: str) -> str:
    return module.split(".", 1)[0]


def find_direct_violations(
    graph: dict[str, set[str]],
    *,
    forbidden: dict[str, frozenset[str]] | None = None,
    baseline_ignores: frozenset[str] | None = None,
) -> list[DirectViolation]:
    rules = forbidden or _FORBIDDEN_DIRECT
    ignores = baseline_ignores if baseline_ignores is not None else _BASELINE_IGNORES
    violations: list[DirectViolation] = []

    for source_module, targets in sorted(graph.items()):
        source_root = _source_root(source_module)
        forbidden_roots = rules.get(source_root)
        if not forbidden_roots:
            continue
        for target_module in sorted(targets):
            target_root = _source_root(target_module)
            if target_root not in forbidden_roots:
                continue
            edge = DirectViolation(source_module, target_module)
            if edge.edge in ignores:
                continue
            violations.append(edge)
    return violations


def main(argv: list[str] | None = None) -> int:
    del argv
    root = _REPO_ROOT
    first_party_roots = discover_first_party_roots(root)
    graph = _build_graph(root, first_party_roots)
    violations = find_direct_violations(graph)

    if not violations:
        print(
            "No forbidden direct import edges found "
            f"(baseline ignores {len(_BASELINE_IGNORES)} known edges)."
        )
        return 0

    print(f"FAIL: {len(violations)} forbidden direct import edge(s):")
    for violation in violations:
        print(f"  {violation.edge}")
    print(
        "\nFix by moving shared code to a lower layer (platform/common, core/contracts) "
        "or add a temporary baseline entry in infra/ci/check_direct_imports.py "
        "with a linked issue — do not use function-level lazy imports to hide "
        "new direct edges."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
