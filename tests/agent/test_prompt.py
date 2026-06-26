from __future__ import annotations

from core.orchestration.node.investigate.prompt import (
    _relevant_sources,
    build_system_prompt,
    format_alert_context,
)


def test_build_system_prompt_non_hermes_uses_generic_category_instruction() -> None:
    prompt = build_system_prompt({"alert_source": "grafana"})

    assert (
        "One of database / infrastructure / code_bug / configuration / network / performance"
        in prompt
    )
    assert "Hermes root cause category taxonomy" not in prompt
    assert "agent_hang" not in prompt


def test_build_system_prompt_includes_dependency_traversal_rule() -> None:
    prompt = build_system_prompt({"alert_source": "grafana"})

    assert "Dependency traversal (connection failures only)" in prompt
    assert "connection refused" in prompt
    assert "does not bias localization" in prompt


def test_build_system_prompt_hermes_includes_hermes_taxonomy_only() -> None:
    prompt = build_system_prompt({"alert_source": "hermes"})

    assert "Hermes root cause category taxonomy" in prompt
    assert "agent_hang" in prompt
    assert "delivery_hang" in prompt
    assert "ghost_session" in prompt
    assert "connection_exhaustion" not in prompt


def test_generic_alert_matches_relevant_integration_by_content() -> None:
    context = format_alert_context(
        {
            "alert_name": "High error rate in payments ETL",
            "alert_source": "generic",
            "pipeline_name": "payments_etl",
            "severity": "critical",
            "message": "payments_etl is failing with repeated database connection errors",
            "resolved_integrations": {
                "postgresql": {"host": "orders-db", "database": "orders", "port": 5432},
            },
        }
    )

    assert "Call these tools first (from: postgresql" in context


def test_generic_alert_excludes_unrelated_integrations() -> None:
    context = format_alert_context(
        {
            "alert_name": "High error rate in payments ETL",
            "alert_source": "generic",
            "pipeline_name": "payments_etl",
            "severity": "critical",
            "message": "payments_etl is failing with repeated database connection errors",
            "resolved_integrations": {
                "postgresql": {"host": "orders-db", "database": "orders", "port": 5432},
                "datadog": {"connection_verified": True, "api_key": "x", "app_key": "y"},
            },
        }
    )

    # Only the content-relevant integration is in the call-first list; Datadog
    # has no content signal here and must be relegated to secondary.
    assert "Call these tools first (from: postgresql)" in context
    assert "Secondary integrations" in context


def test_generic_alert_without_signal_does_not_fan_out() -> None:
    context = format_alert_context(
        {
            "alert_name": "Something went wrong",
            "alert_source": "generic",
            "pipeline_name": "widgets",
            "severity": "critical",
            "message": "an unexpected problem occurred",
            "resolved_integrations": {
                "datadog": {"connection_verified": True, "api_key": "x", "app_key": "y"},
                "vercel": {"connection_verified": True, "token": "z"},
            },
        }
    )

    # No content signal points to a specific integration: the agent must be told
    # to pick relevant ones, NOT to call every connected integration first.
    assert "Call these tools first" not in context
    assert "call only the integration(s) directly relevant" in context
    assert "Do not call integrations" in context


def test_generic_alert_honors_context_sources_annotation() -> None:
    context = format_alert_context(
        {
            "alert_name": "Something went wrong",
            "alert_source": "generic",
            "pipeline_name": "widgets",
            "severity": "critical",
            "message": "an unexpected problem occurred",
            "raw_alert": {
                "commonAnnotations": {"context_sources": "datadog"},
            },
            "resolved_integrations": {
                "datadog": {"connection_verified": True, "api_key": "x", "app_key": "y"},
                "vercel": {"connection_verified": True, "token": "z"},
            },
        }
    )

    assert "Call these tools first (from: datadog)" in context


def test_alert_context_uses_planned_actions_when_present() -> None:
    context = format_alert_context(
        {
            "alert_name": "High error rate",
            "alert_source": "generic",
            "pipeline_name": "payments",
            "severity": "critical",
            "planned_actions": ["get_sre_guidance"],
            "plan_rationale": "Knowledge guidance is the selected fallback.",
            "resolved_integrations": {
                "grafana": {"url": "http://grafana", "api_key": "x"},
                "datadog": {"connection_verified": True, "api_key": "x", "app_key": "y"},
            },
        }
    )

    assert "Use the planned investigation actions first" in context
    assert "`get_sre_guidance`" in context
    assert "Plan rationale: Knowledge guidance is the selected fallback." in context
    assert "`query_datadog_logs`" not in context


def test_relevant_sources_matches_db_symptom_and_excludes_unrelated() -> None:
    state = {
        "alert_name": "High error rate in payments ETL",
        "alert_source": "generic",
        "pipeline_name": "payments_etl",
        "message": "payments_etl is failing with repeated database connection errors",
    }
    tools_by_source = {"postgresql": [], "vercel": [], "knowledge": []}

    # The DB-connection symptom matches Postgres; Vercel is irrelevant and the
    # secondary "knowledge" source is never a candidate.
    assert _relevant_sources(state, tools_by_source) == ["postgresql"]


def test_relevant_sources_empty_when_no_content_signal() -> None:
    state = {
        "alert_name": "Something is wrong",
        "alert_source": "generic",
        "pipeline_name": "mystery",
        "message": "an unexplained problem occurred",
    }
    tools_by_source = {"postgresql": [], "vercel": []}

    assert _relevant_sources(state, tools_by_source) == []


def test_relevant_sources_honors_explicit_context_sources() -> None:
    state = {
        "alert_name": "Something is wrong",
        "alert_source": "generic",
        "pipeline_name": "mystery",
        "message": "an unexplained problem occurred",
        "raw_alert": {"commonAnnotations": {"context_sources": "vercel"}},
    }
    tools_by_source = {"postgresql": [], "vercel": []}

    # Explicit declaration wins even though the content has no Vercel keyword.
    assert _relevant_sources(state, tools_by_source) == ["vercel"]


def test_alert_context_surfaces_v2_contract_hints_for_tool_selection() -> None:
    context = format_alert_context(
        {
            "alert_name": "RDS latency spike",
            "alert_source": "rds",
            "pipeline_name": "orders",
            "severity": "critical",
            "resolved_integrations": {
                "rds": {"db_instance_identifier": "orders-db", "region": "us-east-1"},
                "postgresql": {"host": "orders-db", "database": "orders", "port": 5432},
            },
        }
    )

    assert "Call these tools first (from: rds" in context
    assert "`describe_rds_instance`" in context
    assert "source_id=aws_rds" in context
    assert "evidence=deployment_metadata" in context
    assert "avoid=Use this tool to inspect SQL query text or Postgres locks." in context
