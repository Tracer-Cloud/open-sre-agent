"""Tests for ``app.fleet_monitoring.providers.provider_for`` canonical resolution."""

from __future__ import annotations

from app.fleet_monitoring.providers import provider_for, provider_from_command
from app.fleet_monitoring.registry import AgentRecord


def _record(
    name: str,
    provider: str | None = None,
    command: str = "placeholder",
) -> AgentRecord:
    return AgentRecord(name=name, pid=8421, command=command, provider=provider)


class TestPersistedProviderWins:
    """``record.provider`` takes precedence over any name-based inference."""

    def test_persisted_provider_used_verbatim(self) -> None:
        record = _record(name="manual-bot-xyz", provider="claude-code")
        assert provider_for(record) == "claude-code"

    def test_persisted_provider_overrides_misleading_name(self) -> None:
        # User registered a process under a name that looks like ``codex-1234``
        # but explicitly tagged it as ``claude-code``. The explicit tag wins.
        record = _record(name="codex-1234", provider="claude-code")
        assert provider_for(record) == "claude-code"


class TestNameSuffixHeuristic:
    """Fallback when ``record.provider`` is ``None`` — discovery-style suffix stripping."""

    def test_claude_code_with_pid_suffix(self) -> None:
        assert provider_for(_record(name="claude-code-8421")) == "claude-code"

    def test_codex_with_pid_suffix(self) -> None:
        assert provider_for(_record(name="codex-9999")) == "codex"

    def test_gemini_cli_with_pid_suffix(self) -> None:
        assert provider_for(_record(name="gemini-cli-101")) == "gemini-cli"

    def test_antigravity_cli_with_pid_suffix(self) -> None:
        assert provider_for(_record(name="antigravity-cli-202")) == "antigravity-cli"

    def test_bare_canonical_name(self) -> None:
        # Discovery emits bare names from cursor-terminal metadata.
        assert provider_for(_record(name="claude-code")) == "claude-code"

    def test_user_named_agent_returns_none(self) -> None:
        assert provider_for(_record(name="manual-bot")) is None

    def test_empty_name_returns_none(self) -> None:
        assert provider_for(_record(name="")) is None

    def test_non_digit_suffix_is_not_stripped(self) -> None:
        # ``cursor-agent`` ends in ``-agent`` (not digits), so the
        # rsplit-and-isdigit guard leaves it alone and the cursor-family
        # map catches it. Regression for the
        # ``rsplit(name, 1) -> ("cursor", "agent")`` pitfall.
        assert provider_for(_record(name="cursor-agent")) == "cursor"


class TestCursorFamilyMapping:
    """The Cursor flavors map to canonical meter providers."""

    def test_cursor_claude_code_rolls_up_to_claude_code(self) -> None:
        # Cursor's Anthropic extension wraps the real ``claude`` binary
        # with ``--output-format stream-json``; same NDJSON, same meter.
        assert provider_for(_record(name="cursor-claude-code-80435")) == "claude-code"

    def test_cursor_agent_exec_rolls_up_to_cursor(self) -> None:
        assert provider_for(_record(name="cursor-agent-exec-23995")) == "cursor"

    def test_cursor_agent_rolls_up_to_cursor(self) -> None:
        assert provider_for(_record(name="cursor-agent-12345")) == "cursor"

    def test_bare_cursor_family_names_resolve_without_pid_suffix(self) -> None:
        assert provider_for(_record(name="cursor-claude-code")) == "claude-code"
        assert provider_for(_record(name="cursor-agent-exec")) == "cursor"
        assert provider_for(_record(name="cursor-agent")) == "cursor"


class TestCommandHeuristic:
    """Registered custom-name agents fall back to their stored command line."""

    def test_custom_name_infers_codex_from_command(self) -> None:
        assert provider_for(_record(name="agent-1234", command="/opt/bin/codex exec")) == "codex"

    def test_custom_name_infers_claude_code_from_command(self) -> None:
        assert (
            provider_for(
                _record(name="agent-1234", command="claude --dangerously-skip-permissions")
            )
            == "claude-code"
        )

    def test_custom_name_infers_cursor_claude_code_from_command(self) -> None:
        assert (
            provider_for(
                _record(
                    name="agent-1234",
                    command=(
                        "/Users/me/.cursor/extensions/anthropic.claude-code/resources/"
                        "native-binary/claude --output-format stream-json"
                    ),
                )
            )
            == "claude-code"
        )

    def test_name_heuristic_still_wins_over_command_fallback(self) -> None:
        assert provider_for(_record(name="codex-1234", command="claude")) == "codex"

    def test_command_helper_returns_none_for_unknown_command(self) -> None:
        assert provider_from_command("python -m app.worker") is None

    def test_loose_claude_code_argv_does_not_false_positive(self) -> None:
        # A command that happens to pass ``claude`` and ``code`` as
        # arbitrary argv tokens (model flag + format flag) must NOT
        # be classified as claude-code — the matcher only fires on
        # ``argv[0]``. Regression for the Greptile finding that the
        # earlier ``"claude" in tokens and "code" in tokens`` check
        # would wire unrelated processes to ``ClaudeCodeJsonlSource``.
        assert provider_from_command("run-tests --model claude --format code --verbose") is None

    def test_loose_codex_argv_does_not_false_positive(self) -> None:
        # Same regression for the codex/aider/gemini families: the
        # matcher must be argv[0]-only, never a global token scan.
        assert provider_from_command("python build.py --module codex") is None
        assert provider_from_command("npm run --script aider-fixture") is None
        assert provider_from_command("node --inspect gemini-mock.js") is None

    def test_node_launched_codex_classifies_via_provider_for(self) -> None:
        # Manual register path: custom name + no stored provider +
        # ``node codex.js`` command. The on-read backfill in
        # ``provider_for`` must route through the strict classifier
        # that supports the Node-Codex shape.
        assert (
            provider_for(_record(name="my-bot", command="node /usr/local/bin/codex.js exec"))
            == "codex"
        )

    def test_node_launched_codex_with_intermediate_flag(self) -> None:
        # ``node --inspect /usr/local/bin/codex.js exec`` is a real
        # shape (debug-port flag before the script). ``argv[1]`` is a
        # flag, not the codex script.
        assert (
            provider_from_command("node --inspect /usr/local/bin/codex.js exec --model gpt-5")
            == "codex"
        )

    def test_node_with_unrelated_script_is_not_codex(self) -> None:
        # Negative guard: only an exact ``codex.{js,mjs,cjs}`` filename
        # counts; a substring like ``codex-utils/main.js`` must not
        # match.
        assert provider_from_command("node /opt/codex-utils/main.js") is None
        assert provider_from_command("node /opt/app/server.js") is None


class TestUnknownProviders:
    """Unrecognized names fall through to ``None`` rather than raising."""

    def test_unknown_provider_with_pid_suffix(self) -> None:
        assert provider_for(_record(name="my-custom-cli-1234")) is None

    def test_truly_arbitrary_name(self) -> None:
        assert provider_for(_record(name="something-random")) is None
