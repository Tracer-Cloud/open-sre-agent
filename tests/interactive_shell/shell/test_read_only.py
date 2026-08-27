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
    'grep -E "(foo|bar)" file | head',  # quoted regex parens are safe
    'cd /Users/x/repo && git status --short --branch | head -5; echo "==="; grep -Ei foo Makefile',
    "cd /tmp && ls",
    "git remote -v",
    "git branch -a",
    "cd /x && git status | head; git remote -v; git branch -a",
    "git symbolic-ref HEAD",
    "git symbolic-ref --short HEAD",
    "git reflog",
    "git reflog show",
    "git reflog list",
    "git reflog exists HEAD",
    "git reflog HEAD",
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
    "git remote add origin url",
    "git branch -d feature",
    "git branch --set-upstream-to=origin/main",
    "git branch -u origin/main",
    "git diff --output=/tmp/patch.diff",
    "git diff -o/tmp/patch.diff",
    "git log --output=/tmp/history.txt",
    "git show --output=/tmp/commit.patch HEAD",
    "git show -o/tmp/commit.patch HEAD",
    "git config user.name Bob",
    "git symbolic-ref HEAD refs/heads/foo",
    "git symbolic-ref --delete HEAD",
    "git symbolic-ref -d HEAD",
    "git symbolic-ref -mreason HEAD refs/heads/foo",
    "git reflog expire --all",
    "git reflog delete HEAD@{1}",
    "git reflog drop --all",
    "git reflog write HEAD old new msg",
    "git --exec-path=/tmp/evil status",
    "git --exec-path /tmp/evil status",
    "ls -la\nrm -rf /tmp/x",  # newline separates a mutation — must gate
    'date -s "2020-01-01"',  # sets system clock
    "date -s2026-08-27",  # attached short-option value also sets the clock
    "date 12121212",  # legacy positional MMDDhhmm form sets the clock
    "date 010203042025.00",
    "hostname newname",  # sets kernel hostname
    "hostname -F/tmp/x",  # Linux attached -F writes hostname from a file
    "hostname -F /tmp/x",
    "LD_PRELOAD=/evil.so ls",  # env prefix can inject code
    "git ls-remote ext::evil",  # ext:: transport runs a helper
    "git ls-remote EXT::evil",  # protocol match is case-insensitive
    "git ls-remote --upload-pack=/tmp/evil origin",  # helper-path override
    "git ls-remote --upload-pack /tmp/evil origin",
    "git -c protocol.ext.allow=always ls-remote origin",  # process-local config
    "git -cprotocol.ext.allow=always ls-remote origin",
    "./ls",  # path-qualified executable is not the allowlisted command
    "/tmp/git status",  # attacker-controlled path
    "diff <(rm /tmp/x) file",  # process substitution runs a nested command
    "(rm /tmp/x) && true",  # bare subshell
    'echo "$(rm /tmp/x)"',  # $() expands inside double quotes
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


@pytest.mark.parametrize(
    "command",
    [
        "git diff --output=/tmp/patch.diff",
        "git log --output=/tmp/history.txt",
        "git log --output /tmp/history.txt",
        "git show --output=/tmp/commit.patch HEAD",
        "git show -o/tmp/commit.patch HEAD",
    ],
)
def test_git_output_write_bypasses_neither_auto_nor_plan_only_gates(command: str) -> None:
    """``--output`` / ``-o`` write a file for diff-family porcelain, not only ``diff``.

    Those commands must stay ``unrestricted`` so low-auto and plan-only still ask.
    """
    result = evaluate_shell_command(command)
    assert result.shell_classification == "unrestricted"
    assert apply_auto_level(result, AutoLevel.LOW).verdict == "ask"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "date 12121212",
        "hostname -F/tmp/x",
        "git ls-remote --upload-pack=/tmp/evil origin",
        "git -cprotocol.ext.allow=always ls-remote origin",
        "git ls-remote ext::evil",
    ],
)
def test_mutation_and_helper_forms_bypass_neither_auto_nor_plan_only_gates(
    command: str,
) -> None:
    """Clock/hostname setters and git helper/config injectors must still ask."""
    result = evaluate_shell_command(command)
    assert result.shell_classification == "unrestricted"
    assert apply_auto_level(result, AutoLevel.LOW).verdict == "ask"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "git symbolic-ref HEAD refs/heads/foo",
        "git symbolic-ref --delete HEAD",
        "git reflog expire --all",
        "git reflog delete HEAD@{1}",
        "git reflog drop --all",
        "git reflog write HEAD old new msg",
        "git --exec-path=/tmp/evil status",
    ],
)
def test_git_ref_mutations_bypass_neither_auto_nor_plan_only_gates(command: str) -> None:
    """``symbolic-ref`` set/delete, ``reflog`` write verbs, and ``--exec-path`` mutate.

    They must stay ``unrestricted`` so low-auto and plan-only still ask.
    """
    result = evaluate_shell_command(command)
    assert result.shell_classification == "unrestricted"
    assert apply_auto_level(result, AutoLevel.LOW).verdict == "ask"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "ask"
