"""Audit tests for ``scoring._taxonomy_for_root_cause``.

The mapping must match the dataset's actual ``fault_taxonomy`` ground-truth
values in ``benchmark/<system>/<category>/<id>/metadata.json``. Two
assignments were previously wrong and silently cost a1 points on every
case using those root_causes; tests below pin the corrected behavior.
"""

from __future__ import annotations

import pytest

from tests.benchmarks.cloudopsbench.scoring import _taxonomy_for_root_cause


@pytest.mark.parametrize(
    ("root_cause", "expected_taxonomy"),
    [
        # Previously WRONG (regression-pin) — verified against
        # benchmark/.../metadata.json ground_truth.fault_taxonomy:
        ("missing_secret_binding", "Startup_Fault"),
        ("service_sidecar_port_conflict", "Runtime_Fault"),
        ("missing_service_account", "Admission_Fault"),
        # Already correct, but pin them so the next refactor can't silently
        # break the mapping:
        ("mysql_invalid_credentials", "Runtime_Fault"),
        ("image_registry_dns_failure", "Startup_Fault"),
        ("oom_killed", "Runtime_Fault"),
        ("incorrect_image_reference", "Startup_Fault"),
        ("service_dns_resolution_failure", "Service_Routing_Fault"),
        ("kube_scheduler_unavailable", "Infrastructure_Fault"),
        # Prefix rule: anything starting with namespace_*
        ("namespace_anything_at_all", "Admission_Fault"),
        # Unknown root_cause falls back to Performance_Fault (paper default).
        ("totally_unknown_fault", "Performance_Fault"),
    ],
)
def test_taxonomy_for_root_cause_matches_dataset_ground_truth(
    root_cause: str, expected_taxonomy: str
) -> None:
    assert _taxonomy_for_root_cause(root_cause) == expected_taxonomy
