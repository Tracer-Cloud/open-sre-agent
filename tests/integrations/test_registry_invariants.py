"""Registry self-consistency invariants.

These tests don't probe any integration. They enforce the static contract
between :mod:`app.integrations.registry` and the CLI dispatch in
:mod:`app.integrations.cli`. Each invariant prevents a class of drift that has
already shipped to ``main`` at least once: duplicate orders, orphan handlers
(handler without a registry spec), verifier without a positional route.
Failing here on PR catches the regression before users hit it via
``opensre integrations setup/verify <svc>``.

The forward direction (registry → handler, "missing handler") is covered by
``test_every_setup_spec_has_handler`` in ``tests/integrations/test_registry.py``.
"""

from __future__ import annotations

from collections import Counter

from app.integrations.cli import _HANDLERS
from app.integrations.registry import (
    INTEGRATION_SPECS,
    SUPPORTED_SETUP_SERVICES,
)


def _duplicates(values: list[int]) -> set[int]:
    return {value for value, count in Counter(values).items() if count > 1}


def test_setup_orders_are_unique() -> None:
    orders = [s.setup_order for s in INTEGRATION_SPECS if s.setup_order is not None]
    duplicates = _duplicates(orders)
    if duplicates:
        offenders = {
            order: sorted(s.service for s in INTEGRATION_SPECS if s.setup_order == order)
            for order in duplicates
        }
        raise AssertionError(
            f"Duplicate setup_order values in INTEGRATION_SPECS: {offenders}. "
            "Pick a unique order per service so the wizard sort is deterministic."
        )


def test_verify_orders_are_unique() -> None:
    orders = [s.verify_order for s in INTEGRATION_SPECS if s.verify_order is not None]
    duplicates = _duplicates(orders)
    if duplicates:
        offenders = {
            order: sorted(s.service for s in INTEGRATION_SPECS if s.verify_order == order)
            for order in duplicates
        }
        raise AssertionError(
            f"Duplicate verify_order values in INTEGRATION_SPECS: {offenders}. "
            "Pick a unique order per service so `integrations verify` ordering is deterministic."
        )


def test_every_cli_handler_is_registered_in_registry() -> None:
    orphans = [svc for svc in _HANDLERS if svc not in SUPPORTED_SETUP_SERVICES]
    if orphans:
        raise AssertionError(
            f"app/integrations/cli.py defines _HANDLERS for {orphans} but registry "
            "has no setup_order for them. These handlers are unreachable: "
            "_SETUP_SERVICES only includes services that appear in both "
            "SUPPORTED_SETUP_SERVICES and _HANDLERS, so the wizard will never offer them."
        )


def test_every_verifier_has_a_verify_order() -> None:
    orphans = [s.service for s in INTEGRATION_SPECS if s.has_verifier and s.verify_order is None]
    if orphans:
        raise AssertionError(
            f"Registry defines verifier but no verify_order for {orphans}. "
            "These run on the no-arg `integrations verify` path but are rejected positionally."
        )
