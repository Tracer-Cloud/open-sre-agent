"""Read-only shell commands run without approval; mutating ones still ask."""

from __future__ import annotations

import pytest

from config.constants.repl_autonomy import AutoLevel
from tools.interactive_shell.shared import apply_auto_level, apply_plan_only_gate
from tools.interactive_shell.shell.policy import evaluate_shell_command
from tools.interactive_shell.shell.read_only import is_read_only_shell_command

_READ_ONLY = [
    'find /Users/x -maxdepth 4 -iname "*opensre*" 2>/dev/null | head -50',
    'ls -la /Users/x 2>/dev/null | head -60; echo "---"; ls /tmp',
    "git status",
    "git log --oneline | head",
    "cat file | grep needle | wc -l",
    "sort file | uniq",
    'cd /Users/x/repo && git status --short --branch | head -5; echo "==="; grep -Ei foo Makefile',
    "cd /tmp && ls",
]

_MUTATING = [
    "rm -rf /tmp/x",
    "ls > out.txt",
    "cat a | tee b",
    "git commit -m x",
    "find . -delete",
    "find . -exec rm {} ;",
    "sort -o out.txt file",
    "sudo ls",
    "ls | xargs rm",
    "yq -i .x f.yaml",
    "sed -i s/a/b/ f",
    "python script.py",
    "cat $(rm x)",
    "cd /tmp && rm -rf x",
]


@pytest.mark.parametrize("command", _READ_ONLY)
def test_read_only_commands_are_classified_read_only(command: str) -> None:
    assert is_read_only_shell_command(command) is True


@pytest.mark.parametrize("command", _MUTATING)
def test_mutating_commands_are_not_read_only(command: str) -> None:
    assert is_read_only_shell_command(command) is False


def test_read_only_shell_runs_without_approval_at_every_level_and_under_plan_only() -> None:
    result = evaluate_shell_command("find . -iname x | head")
    assert result.shell_classification == "read_only"
    # Neither the /auto gate nor the plan-only gate promotes it to ask.
    assert apply_auto_level(result, AutoLevel.MED).verdict == "allow"
    assert apply_auto_level(result, AutoLevel.LOW).verdict == "allow"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "allow"


def test_mutating_shell_still_asks_when_gated() -> None:
    result = evaluate_shell_command("rm -rf /tmp/x")
    assert result.shell_classification == "unrestricted"
    assert apply_auto_level(result, AutoLevel.MED).verdict == "ask"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "ask"
