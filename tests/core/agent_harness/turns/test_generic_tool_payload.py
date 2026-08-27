"""The generic tool formatter must not flood the transcript with raw JSON.

A tool result with no user-facing field (response_text, summary, stdout, error)
is for the model only — the tool invocation is already shown at tool_start, so
dumping its raw JSON payload would flood the execution transcript. Genuine text
output and real summaries are still shown.
"""

from __future__ import annotations

import json

from core.agent_harness.turns.action_driver import _format_generic_tool_payload
from core.llm.types import ToolCall


def _result(content: object):
    class _R:
        pass

    r = _R()
    r.content = content if isinstance(content, str) else json.dumps(content)
    r.details = content if isinstance(content, dict) else None
    r.is_error = False
    return r


def _call(name: str) -> ToolCall:
    return ToolCall(id="t1", name=name, input={"owner": "acme", "repo": "svc"})


def test_opaque_json_object_is_pretty_printed() -> None:
    # A GitHub-style payload with no summary field is pretty-printed, not a blob.
    payload = {"ok": True, "available": True, "repository": {"full_name": "acme/svc"}}
    shown = _format_generic_tool_payload(_call("get_github_repository"), _result(payload))
    assert '"full_name": "acme/svc"' in shown
    assert "\n" in shown  # indented, multi-line


def test_opaque_json_list_is_pretty_printed() -> None:
    shown = _format_generic_tool_payload(_call("list_runs"), _result([{"id": 1}, {"id": 2}]))
    assert '"id": 1' in shown
    assert "\n" in shown


def test_a_real_summary_is_still_shown() -> None:
    payload = {"ok": True, "summary": "3 workflows, 12 runs"}
    shown = _format_generic_tool_payload(_call("list_runs"), _result(payload))
    assert shown == "3 workflows, 12 runs"


def test_plain_text_output_is_still_shown() -> None:
    shown = _format_generic_tool_payload(_call("shell"), _result("build succeeded"))
    assert "build succeeded" in shown


def test_json_stdout_is_pretty_printed_and_plain_stdout_is_shown() -> None:
    # gh-api JSON stdout is pretty-printed (readable data). Plain-text command
    # output (ls, git) is the user's real result and shows as-is.
    json_stdout = _result({"ok": True, "stdout": '{"id": 18260225, "name": "main"}'})
    shown = _format_generic_tool_payload(_call("github_cli"), json_stdout)
    assert '"name": "main"' in shown
    assert "\n" in shown  # pretty-printed

    plain_stdout = _result({"ok": True, "stdout": "total 56\ndrwxr-xr-x  15 user"})
    assert "drwxr-xr-x" in _format_generic_tool_payload(_call("shell"), plain_stdout)


def test_verbose_output_is_capped_for_display_only() -> None:
    from core.agent_harness.turns.action_driver import _cap_for_display

    big = "\n".join(f"run {i} failure 2026-08-01T09:11:00Z" for i in range(50))
    capped = _cap_for_display(big)

    # Display is bounded with a truncation marker; the full text is untouched.
    assert capped.count("\n") + 1 <= 13
    assert capped.endswith("… (output truncated)")
    assert big.count("\n") + 1 == 50  # source unchanged


def test_short_output_is_not_truncated() -> None:
    from core.agent_harness.turns.action_driver import _cap_for_display

    text = "total 56\ndrwxr-xr-x  15 user"
    assert _cap_for_display(text) == text
