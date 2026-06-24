from __future__ import annotations

from tests.synthetic.hermes_rca.hermes_schemas import VALID_HERMES_FAILURE_MODES
from tests.synthetic.hermes_rca.scenario_loader import SUITE_DIR, load_all_scenarios


def test_every_failure_mode_has_at_least_one_scenario() -> None:
    scenarios = load_all_scenarios(SUITE_DIR)
    covered_modes = {scenario.metadata.failure_mode for scenario in scenarios}

    missing_modes = sorted(VALID_HERMES_FAILURE_MODES - covered_modes)

    assert not missing_modes, (
        "Every Hermes failure mode must have at least one synthetic scenario. "
        f"Missing: {missing_modes}"
    )
