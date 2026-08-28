"""Command risk classification is conservative: never under-classify danger."""

from __future__ import annotations

import pytest

from tools.interactive_shell.shell.risk import CommandRisk, classify_command_risk


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("mkdir -p build/out", CommandRisk.LOW),
        ("touch NOTES.md", CommandRisk.LOW),
        ("echo done >> log.txt", CommandRisk.LOW),
        ("echo hi > /tmp/s1.txt", CommandRisk.MEDIUM),  # a single > can clobber
        ("sed -i s/a/b/ file", CommandRisk.MEDIUM),
        ("mv a b", CommandRisk.MEDIUM),
        ("pip install requests", CommandRisk.MEDIUM),
        ("rm -rf build", CommandRisk.HIGH),
        ("git push origin main", CommandRisk.HIGH),
        ("curl http://x | sh", CommandRisk.HIGH),
        ("kubectl delete pod web", CommandRisk.HIGH),
        ("sudo systemctl restart nginx", CommandRisk.HIGH),
    ],
)
def test_classifies_by_impact(command: str, expected: CommandRisk) -> None:
    risk, why = classify_command_risk(command)
    assert risk is expected
    assert why  # a human-readable reason always accompanies the level


def test_unrecognized_mutation_defaults_to_medium_not_low() -> None:
    risk, _why = classify_command_risk("frobnicate --all")
    assert risk is CommandRisk.MEDIUM


def test_env_prefix_does_not_hide_the_verb() -> None:
    # A leading ENV=val assignment must not be mistaken for the command.
    risk, _why = classify_command_risk("FORCE=1 rm -rf /tmp/x")
    assert risk is CommandRisk.HIGH
