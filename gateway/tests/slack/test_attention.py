"""Unit tests for the thread attention gate (un-tagged reply admission)."""

from __future__ import annotations

from gateway.slack.attention import GateDecision, ThreadAttentionGate, is_addressed_to_bot

_KEY = "T1:C1:100.1"
_BOT = "UBOT"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _gate(clock: _Clock | None = None) -> ThreadAttentionGate:
    return ThreadAttentionGate(clock=clock or _Clock())


def _decide(gate: ThreadAttentionGate, text: str) -> GateDecision:
    return gate.decide(conversation_key=_KEY, text=text, bot_user_id=_BOT)


def test_no_window_without_prior_mention() -> None:
    assert _decide(_gate(), "what about the api?") is GateDecision.PASS


def test_mention_opens_window_for_follow_up_questions() -> None:
    gate = _gate()
    gate.note_addressed_turn(_KEY)

    assert _decide(gate, "what about the api?") is GateDecision.ENGAGE


def test_window_expires_and_requires_fresh_mention() -> None:
    clock = _Clock()
    gate = _gate(clock)
    gate.note_addressed_turn(_KEY)

    clock.now += 31 * 60  # past the 30-minute window

    assert _decide(gate, "still there?") is GateDecision.PASS


def test_engaging_refreshes_the_window() -> None:
    clock = _Clock()
    gate = _gate(clock)
    gate.note_addressed_turn(_KEY)

    clock.now += 20 * 60
    assert _decide(gate, "one more question?") is GateDecision.ENGAGE
    clock.now += 20 * 60  # 40 min after the mention, 20 after the engagement

    assert _decide(gate, "and another?") is GateDecision.ENGAGE


def test_statements_between_humans_pass_through() -> None:
    gate = _gate()
    gate.note_addressed_turn(_KEY)

    assert _decide(gate, "the deploy finished ten minutes ago") is GateDecision.PASS


def test_reply_leading_with_another_users_mention_passes() -> None:
    gate = _gate()
    gate.note_addressed_turn(_KEY)

    # Addressed to a human — even though it is a question.
    assert _decide(gate, "<@U2> can you check the dashboard?") is GateDecision.PASS


def test_affirmative_follow_up_engages() -> None:
    gate = _gate()
    gate.note_addressed_turn(_KEY)

    assert _decide(gate, "yes") is GateDecision.ENGAGE


def test_bot_name_in_prose_engages() -> None:
    gate = _gate()
    gate.note_addressed_turn(_KEY)

    assert _decide(gate, "opensre take a look at the checkout latency") is GateDecision.ENGAGE


def test_unprompted_budget_rate_limits_then_recovers() -> None:
    clock = _Clock()
    gate = _gate(clock)
    gate.note_addressed_turn(_KEY)

    assert _decide(gate, "q one?") is GateDecision.ENGAGE
    assert _decide(gate, "q two?") is GateDecision.ENGAGE
    assert _decide(gate, "q three?") is GateDecision.RATE_LIMITED

    clock.now += 11 * 60  # rate window (10 min) elapses, attention still open?
    gate.note_addressed_turn(_KEY)  # fresh mention re-opens regardless
    assert _decide(gate, "q four?") is GateDecision.ENGAGE


def test_threads_are_independent() -> None:
    gate = _gate()
    gate.note_addressed_turn(_KEY)

    other = gate.decide(conversation_key="T1:C1:999.9", text="what about this?", bot_user_id=_BOT)
    assert other is GateDecision.PASS


def test_is_addressed_to_bot_heuristics() -> None:
    assert is_addressed_to_bot("yes", bot_user_id=_BOT)
    assert is_addressed_to_bot("whats my name?", bot_user_id=_BOT)
    assert is_addressed_to_bot(f"<@{_BOT}> please rerun", bot_user_id=_BOT)
    assert is_addressed_to_bot("OpenSRE can you summarize", bot_user_id=_BOT)
    assert not is_addressed_to_bot("<@U2> your turn?", bot_user_id=_BOT)
    assert not is_addressed_to_bot("pushed the fix to main", bot_user_id=_BOT)
