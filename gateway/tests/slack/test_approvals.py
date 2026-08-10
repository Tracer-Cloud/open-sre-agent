"""Block Kit approval gate: broker, prompter, hooks, click routing."""

from __future__ import annotations

import threading
from typing import Any

from core.execution import ToolExecutionRequest, ToolExecutionResult
from core.llm.types import ToolCall
from gateway.core.runtime.approvals import (
    APPROVE_ACTION_ID,
    DENY_ACTION_ID,
    ApprovalBroker,
    approval_tool_hooks,
)
from gateway.transports.slack.approvals import ThreadApprovalPrompter, handle_block_actions_payload


class _FakeMessagingClient:
    def __init__(self, *, post_ok: bool = True) -> None:
        self.post_ok = post_ok
        self.posts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: Any = None,
    ) -> str | None:
        self.posts.append(
            {"channel": channel, "text": text, "thread_ts": thread_ts, "blocks": blocks}
        )
        return f"ts-{len(self.posts)}" if self.post_ok else None

    def update_message(self, *, channel: str, ts: str, text: str, blocks: Any = None) -> bool:
        self.updates.append({"channel": channel, "ts": ts, "text": text, "blocks": blocks})
        return True


class _FakeDisplay:
    """A tool rendering its own prompt, as ``open_github_pull_request`` does."""

    def headline(self, _arguments: Any) -> str:
        return "Create PR — drop the orphan"

    def details(self, _arguments: Any) -> str:
        return "acme/platform\nmain  ←  fix/drop-orphan"

    def receipt(self, _arguments: Any, result: Any) -> str:
        return f"Create PR #{result.get('number', 0)}"


class _FakeTool:
    """Minimal stand-in carrying the approval metadata the hook reads."""

    def __init__(self, *, requires_approval: bool = True, display: Any = None) -> None:
        self.requires_approval = requires_approval
        self.approval_reason = "Sends a message to Slack on your behalf."
        self.approval_expiry_seconds = 60
        self.approval_display = display


def _request(tool: _FakeTool, name: str = "slack_send_message") -> ToolExecutionRequest:
    return ToolExecutionRequest(
        tool_call=ToolCall(id="tc-1", name=name, input={"message": "hi"}),
        tool=tool,  # type: ignore[arg-type]
        arguments={"message": "hi"},
        source="slack",
        resolved_integrations={},
    )


def _approval_id_from_blocks(blocks: list[dict[str, Any]]) -> str:
    actions = next(b for b in blocks if b["type"] == "actions")
    return str(actions["elements"][0]["value"])


def _click_payload(approval_id: str, *, user: str, action_id: str) -> dict[str, Any]:
    return {
        "type": "block_actions",
        "user": {"id": user},
        "actions": [{"action_id": action_id, "value": approval_id}],
    }


def _prompter(client: _FakeMessagingClient, broker: ApprovalBroker) -> ThreadApprovalPrompter:
    return ThreadApprovalPrompter(client=client, broker=broker, channel_id="C1", thread_ts="1700.1")


def _click_later(broker: ApprovalBroker, client: _FakeMessagingClient, *, action_id: str) -> None:
    """Simulate the authorized user clicking once the prompt is on screen."""

    def _click() -> None:
        approval_id = _approval_id_from_blocks(client.posts[-1]["blocks"])
        handle_block_actions_payload(
            _click_payload(approval_id, user="U1", action_id=action_id),
            broker=broker,
            allowed_user_ids=["U1"],
            allow_open_workspace=False,
        )

    threading.Timer(0.05, _click).start()


def test_approved_click_lets_the_tool_run() -> None:
    broker = ApprovalBroker()
    client = _FakeMessagingClient()
    hooks = approval_tool_hooks(_prompter(client, broker))
    _click_later(broker, client, action_id=APPROVE_ACTION_ID)

    result = hooks.before_tool_call(_request(_FakeTool()))

    assert result is not None and result.approved and not result.blocked
    # Prompt carried the reason and both buttons; outcome rewrote the message.
    prompt = client.posts[-1]
    assert "Approval needed" in prompt["blocks"][0]["text"]["text"]
    assert "on your behalf" in prompt["blocks"][0]["text"]["text"]
    assert client.updates[-1]["text"].startswith(":white_check_mark:")
    assert "<@U1>" in client.updates[-1]["text"]


def test_denied_click_blocks_the_tool_with_no_retry_guidance() -> None:
    broker = ApprovalBroker()
    client = _FakeMessagingClient()
    hooks = approval_tool_hooks(_prompter(client, broker))
    _click_later(broker, client, action_id=DENY_ACTION_ID)

    result = hooks.before_tool_call(_request(_FakeTool()))

    assert result is not None and result.blocked
    assert "denied" in result.reason
    assert "Do not retry" in result.reason
    assert client.updates[-1]["text"].startswith(":no_entry:")


def test_prompt_uses_the_tools_own_wording_when_it_supplies_any() -> None:
    """The regression: a field dump told the reviewer nothing about the change."""
    broker = ApprovalBroker()
    client = _FakeMessagingClient()
    hooks = approval_tool_hooks(_prompter(client, broker))
    _click_later(broker, client, action_id=APPROVE_ACTION_ID)

    hooks.before_tool_call(_request(_FakeTool(display=_FakeDisplay())))

    section = client.posts[-1]["blocks"][0]["text"]["text"]
    assert "Create PR — drop the orphan" in section
    assert "main  ←  fix/drop-orphan" in section
    # The outcome names the action, not the tool's internal identifier.
    assert client.updates[-1]["text"] == (
        ":white_check_mark: Create PR — drop the orphan — approved by <@U1>"
    )


def _result(payload: Any, *, is_error: bool = False) -> ToolExecutionResult:
    return ToolExecutionResult(content="ok", details=payload, is_error=is_error)


def test_the_outcome_is_rewritten_with_what_the_call_produced() -> None:
    """`#7` does not exist at click time — only after the tool has run."""
    broker = ApprovalBroker()
    client = _FakeMessagingClient()
    hooks = approval_tool_hooks(_prompter(client, broker))
    _click_later(broker, client, action_id=APPROVE_ACTION_ID)
    request = _request(_FakeTool(display=_FakeDisplay()))
    hooks.before_tool_call(request)

    patch = hooks.after_tool_call(request, _result({"ok": True, "number": 7}))

    # The chat message is edited; the model's transcript is left alone.
    assert patch is None
    assert client.updates[-1]["ts"] == client.updates[-2]["ts"]
    assert client.updates[-1]["text"] == ":white_check_mark: Create PR #7 — approved by <@U1>"


def test_a_failed_call_leaves_the_approval_outcome_alone() -> None:
    broker = ApprovalBroker()
    client = _FakeMessagingClient()
    hooks = approval_tool_hooks(_prompter(client, broker))
    _click_later(broker, client, action_id=APPROVE_ACTION_ID)
    request = _request(_FakeTool(display=_FakeDisplay()))
    hooks.before_tool_call(request)
    before = len(client.updates)

    hooks.after_tool_call(request, _result({"ok": False, "error": "boom"}, is_error=True))

    assert len(client.updates) == before


def test_a_denied_call_has_no_prompt_left_to_rewrite() -> None:
    """after_tool_call cannot normally fire here; the bookkeeping must not misfire."""
    broker = ApprovalBroker()
    client = _FakeMessagingClient()
    hooks = approval_tool_hooks(_prompter(client, broker))
    _click_later(broker, client, action_id=DENY_ACTION_ID)
    request = _request(_FakeTool(display=_FakeDisplay()))
    hooks.before_tool_call(request)
    before = len(client.updates)

    hooks.after_tool_call(request, _result({"ok": True, "number": 7}))

    assert len(client.updates) == before


def test_each_receipt_lands_on_its_own_prompt() -> None:
    """Two approvals in one turn must not have their outcomes crossed."""
    broker = ApprovalBroker()
    client = _FakeMessagingClient()
    prompter = _prompter(client, broker)
    hooks = approval_tool_hooks(prompter)
    requests = []
    for index in (1, 2):
        request = ToolExecutionRequest(
            tool_call=ToolCall(id=f"tc-{index}", name="open_github_pull_request", input={}),
            tool=_FakeTool(display=_FakeDisplay()),  # type: ignore[arg-type]
            arguments={},
            source="github",
            resolved_integrations={},
        )
        _click_later(broker, client, action_id=APPROVE_ACTION_ID)
        hooks.before_tool_call(request)
        requests.append(request)
    first_ts, second_ts = client.updates[0]["ts"], client.updates[1]["ts"]

    hooks.after_tool_call(requests[1], _result({"number": 22}))
    hooks.after_tool_call(requests[0], _result({"number": 11}))

    edits = {update["ts"]: update["text"] for update in client.updates[2:]}
    assert edits[second_ts].startswith(":white_check_mark: Create PR #22")
    assert edits[first_ts].startswith(":white_check_mark: Create PR #11")


def test_a_tool_without_a_display_gets_no_receipt_edit() -> None:
    broker = ApprovalBroker()
    client = _FakeMessagingClient()
    hooks = approval_tool_hooks(_prompter(client, broker))
    _click_later(broker, client, action_id=APPROVE_ACTION_ID)
    request = _request(_FakeTool())
    hooks.before_tool_call(request)
    before = len(client.updates)

    hooks.after_tool_call(request, _result({"ok": True}))

    assert len(client.updates) == before


def test_tools_without_approval_metadata_run_unprompted() -> None:
    broker = ApprovalBroker()
    client = _FakeMessagingClient()
    hooks = approval_tool_hooks(_prompter(client, broker))

    result = hooks.before_tool_call(_request(_FakeTool(requires_approval=False)))

    assert result is None
    assert not client.posts


def test_unanswered_prompt_expires_to_deny() -> None:
    broker = ApprovalBroker()
    client = _FakeMessagingClient()
    prompter = _prompter(client, broker)

    approved, decided_by = prompter.request(
        call_id="tc-1",
        headline="slack_send_message",
        reason="reason",
        details="",
        expiry_seconds=0.05,
    )

    assert approved is False and decided_by == ""
    assert "expired" in client.updates[-1]["text"]


def test_failed_prompt_post_fails_closed() -> None:
    broker = ApprovalBroker()
    client = _FakeMessagingClient(post_ok=False)
    hooks = approval_tool_hooks(_prompter(client, broker))

    result = hooks.before_tool_call(_request(_FakeTool()))

    assert result is not None and result.blocked


def test_click_from_unauthorized_user_is_ignored() -> None:
    broker = ApprovalBroker()
    approval_id = broker.create()

    resolved = handle_block_actions_payload(
        _click_payload(approval_id, user="U_INTRUDER", action_id=APPROVE_ACTION_ID),
        broker=broker,
        allowed_user_ids=["U1"],
        allow_open_workspace=False,
    )

    assert resolved is False
    # The pending request is still undecided (a later authorized click wins).
    assert broker.resolve(approval_id, approved=True, decided_by="U1") is True


def test_second_click_on_same_prompt_is_ignored() -> None:
    broker = ApprovalBroker()
    approval_id = broker.create()

    assert broker.resolve(approval_id, approved=False, decided_by="U1") is True
    assert broker.resolve(approval_id, approved=True, decided_by="U2") is False


def test_non_block_actions_payloads_are_ignored() -> None:
    broker = ApprovalBroker()

    assert (
        handle_block_actions_payload(
            {"type": "view_submission"},
            broker=broker,
            allowed_user_ids=["U1"],
            allow_open_workspace=False,
        )
        is False
    )
