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


def test_opaque_json_object_is_hidden() -> None:
    # A raw JSON payload with no user-facing field (e.g. a gh-api response) is
    # for the model, not the transcript — hide it; the reply summarizes it.
    payload = {"ok": True, "available": True, "repository": {"full_name": "acme/svc"}}
    assert _format_generic_tool_payload(_call("get_github_repository"), _result(payload)) == ""


def test_opaque_json_list_is_hidden() -> None:
    assert _format_generic_tool_payload(_call("list_runs"), _result([{"id": 1}, {"id": 2}])) == ""


def test_truncated_json_blob_is_hidden_not_dumped_raw() -> None:
    # A capped gh-api response arrives as invalid (cut-off) JSON — it must not
    # slip past the JSON check and dump raw. Detection is by shape, not parse.
    trunc = '{"full_name":"Tracer-Cloud/opensre","followers_url":"https://api.github.com/users/Tr'
    assert _format_generic_tool_payload(_call("github_cli"), _result({"ok": True, "stdout": trunc})) == ""
    assert _format_generic_tool_payload(_call("github_cli"), _result(trunc)) == ""
    # A mid-object fragment (starts on a value, not ``{``) is still a data blob:
    # key-density detection catches it where a first-char check would not.
    frag = (
        'https://github.com/x","followers_url":"https://api.github.com/u",'
        '"following_url":"https://api.github.com/f","gists_url":"https://api.github.com/g"'
    )
    assert _format_generic_tool_payload(_call("github_cli"), _result({"ok": True, "stdout": frag})) == ""


def test_a_real_summary_is_still_shown() -> None:
    payload = {"ok": True, "summary": "3 workflows, 12 runs"}
    shown = _format_generic_tool_payload(_call("list_runs"), _result(payload))
    assert shown == "3 workflows, 12 runs"


def test_plain_text_output_is_still_shown() -> None:
    shown = _format_generic_tool_payload(_call("shell"), _result("build succeeded"))
    assert "build succeeded" in shown


def test_json_stdout_is_hidden_and_plain_stdout_is_shown() -> None:
    # gh-api JSON stdout is data the reply already summarizes — hide it rather
    # than dump a wall unattached to the command. Plain-text output (ls, git) is
    # the user's real result and shows as-is.
    json_stdout = _result({"ok": True, "stdout": '{"id": 18260225, "name": "main"}'})
    assert _format_generic_tool_payload(_call("github_cli"), json_stdout) == ""

    plain_stdout = _result({"ok": True, "stdout": "total 56\ndrwxr-xr-x  15 user"})
    assert "drwxr-xr-x" in _format_generic_tool_payload(_call("shell"), plain_stdout)


def test_verbose_output_is_capped_for_display_only() -> None:
    from core.agent_harness.turns.action_driver import _cap_for_display

    big = "\n".join(f"run {i} failure 2026-08-01T09:11:00Z" for i in range(50))
    capped = _cap_for_display(big)

    # Display is a short head + peek marker; the full text is untouched.
    assert capped.count("\n") + 1 <= 6
    assert "… 46 more lines" in capped
    assert big.count("\n") + 1 == 50  # source unchanged


def test_short_output_is_not_truncated() -> None:
    from core.agent_harness.turns.action_driver import _cap_for_display

    text = "total 56\ndrwxr-xr-x  15 user"
    assert _cap_for_display(text) == text
