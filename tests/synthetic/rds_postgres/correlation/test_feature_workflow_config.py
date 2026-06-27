from pathlib import Path

from tools.investigation.reporting.upstream_correlation.feature_config import (
    load_feature_workflow_config,
    resolve_feature_keywords,
)


def test_loads_file_based_feature_workflow_config(tmp_path: Path) -> None:
    config_path = tmp_path / "feature_workflows.yaml"
    config_path.write_text(
        """
endpoints:
  /checkout/retry:
    features:
      - checkout_retry_workflow

features:
  checkout_retry_workflow:
    services:
      - checkout-web
    workflow: periodic_checkout_retry
    description: Periodic checkout retry workflow

operator_hints:
  checkout_retry_workflow:
    recently_shipped: true
    scheduled_workflow: true
    note: Recently shipped scheduled checkout retry workflow
""",
        encoding="utf-8",
    )

    config = load_feature_workflow_config(config_path)

    assert config.endpoint_features["/checkout/retry"] == ("checkout_retry_workflow",)
    assert config.features["checkout_retry_workflow"].services == ("checkout-web",)
    assert config.operator_hints["checkout_retry_workflow"].scheduled_workflow is True


def test_resolves_endpoint_feature_and_service_keywords(tmp_path: Path) -> None:
    config_path = tmp_path / "feature_workflows.yaml"
    config_path.write_text(
        """
endpoints:
  /checkout/retry:
    features:
      - checkout_retry_workflow

features:
  checkout_retry_workflow:
    services:
      - checkout-web
    workflow: periodic_checkout_retry

operator_hints:
  checkout_retry_workflow:
    recently_shipped: true
    scheduled_workflow: true
    note: Recently shipped scheduled checkout retry workflow
""",
        encoding="utf-8",
    )

    config = load_feature_workflow_config(config_path)

    keywords = resolve_feature_keywords(
        endpoint="/checkout/retry",
        service_name="checkout-web.latency",
        config=config,
    )

    assert "checkout_retry_workflow" in keywords
    assert "periodic_checkout_retry" in keywords
    assert "scheduled_workflow" in keywords
    assert "recently_shipped" in keywords
