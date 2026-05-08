"""Direct unit tests for app/agents/conflicts.py."""

from __future__ import annotations

from app.agents.conflicts import FileWriteConflict, WriteEvent, detect_conflicts

OPENSRE_ID = "opensre:1"


class TestEmptyAndTrivialInputs:
    def test_empty_events_returns_empty_list(self) -> None:
        assert detect_conflicts([], window_seconds=60.0, opensre_agent_id=OPENSRE_ID) == []

    def test_only_opensre_events_returns_empty_list(self) -> None:
        events = [
            WriteEvent(agent=OPENSRE_ID, path="/a", timestamp=10.0),
            WriteEvent(agent=OPENSRE_ID, path="/a", timestamp=20.0),
        ]
        assert detect_conflicts(events, window_seconds=60.0, opensre_agent_id=OPENSRE_ID) == []

    def test_single_agent_repeated_writes_no_conflict(self) -> None:
        events = [
            WriteEvent(agent="claude-code:1", path="/a", timestamp=10.0),
            WriteEvent(agent="claude-code:1", path="/a", timestamp=20.0),
            WriteEvent(agent="claude-code:1", path="/a", timestamp=30.0),
        ]
        assert detect_conflicts(events, window_seconds=60.0, opensre_agent_id=OPENSRE_ID) == []


class TestWindowBoundaries:
    def test_two_agents_same_path_inside_window(self) -> None:
        events = [
            WriteEvent(agent="claude-code:1", path="/a", timestamp=100.0),
            WriteEvent(agent="cursor:2", path="/a", timestamp=110.0),
        ]
        result = detect_conflicts(events, window_seconds=60.0, opensre_agent_id=OPENSRE_ID)
        assert result == [
            FileWriteConflict(
                path="/a",
                agents=("claude-code:1", "cursor:2"),
                first_seen=100.0,
                last_seen=110.0,
            )
        ]

    def test_two_agents_same_path_outside_window(self) -> None:
        events = [
            WriteEvent(agent="claude-code:1", path="/a", timestamp=10.0),
            WriteEvent(agent="cursor:2", path="/a", timestamp=200.0),
        ]
        # claude's write is 190s before cursor's; window 60s drops it.
        assert detect_conflicts(events, window_seconds=60.0, opensre_agent_id=OPENSRE_ID) == []

    def test_window_boundary_is_inclusive(self) -> None:
        events = [
            WriteEvent(agent="claude-code:1", path="/a", timestamp=40.0),
            WriteEvent(agent="cursor:2", path="/a", timestamp=100.0),
        ]
        # 100 - 40 == 60, exactly at the boundary; must be included.
        result = detect_conflicts(events, window_seconds=60.0, opensre_agent_id=OPENSRE_ID)
        assert len(result) == 1
        assert result[0].agents == ("claude-code:1", "cursor:2")


class TestMultiAgentAndFiltering:
    def test_three_agents_same_path_inside_window(self) -> None:
        events = [
            WriteEvent(agent="claude-code:1", path="/a", timestamp=100.0),
            WriteEvent(agent="cursor:2", path="/a", timestamp=105.0),
            WriteEvent(agent="aider:3", path="/a", timestamp=110.0),
        ]
        result = detect_conflicts(events, window_seconds=60.0, opensre_agent_id=OPENSRE_ID)
        assert result == [
            FileWriteConflict(
                path="/a",
                agents=("aider:3", "claude-code:1", "cursor:2"),
                first_seen=100.0,
                last_seen=110.0,
            )
        ]

    def test_opensre_event_does_not_create_conflict_with_lone_agent(self) -> None:
        events = [
            WriteEvent(agent="claude-code:1", path="/a", timestamp=100.0),
            WriteEvent(agent=OPENSRE_ID, path="/a", timestamp=105.0),
        ]
        # Only one real agent → no conflict, even though OpenSRE also wrote.
        assert detect_conflicts(events, window_seconds=60.0, opensre_agent_id=OPENSRE_ID) == []

    def test_opensre_filtered_when_other_agents_collide(self) -> None:
        events = [
            WriteEvent(agent="claude-code:1", path="/a", timestamp=100.0),
            WriteEvent(agent="cursor:2", path="/a", timestamp=105.0),
            WriteEvent(agent=OPENSRE_ID, path="/a", timestamp=110.0),
        ]
        # OpenSRE must not appear in agents nor influence first_seen/last_seen.
        result = detect_conflicts(events, window_seconds=60.0, opensre_agent_id=OPENSRE_ID)
        assert result == [
            FileWriteConflict(
                path="/a",
                agents=("claude-code:1", "cursor:2"),
                first_seen=100.0,
                last_seen=105.0,
            )
        ]

    def test_agents_tuple_is_deduplicated_and_sorted(self) -> None:
        events = [
            WriteEvent(agent="cursor:2", path="/a", timestamp=100.0),
            WriteEvent(agent="claude-code:1", path="/a", timestamp=101.0),
            WriteEvent(agent="cursor:2", path="/a", timestamp=102.0),
            WriteEvent(agent="claude-code:1", path="/a", timestamp=103.0),
        ]
        result = detect_conflicts(events, window_seconds=60.0, opensre_agent_id=OPENSRE_ID)
        assert len(result) == 1
        assert result[0].agents == ("claude-code:1", "cursor:2")


class TestMultipleConflicts:
    def test_unrelated_paths_independent_conflicts_sorted_by_last_seen_desc(self) -> None:
        events = [
            WriteEvent(agent="claude-code:1", path="/old", timestamp=100.0),
            WriteEvent(agent="cursor:2", path="/old", timestamp=105.0),
            WriteEvent(agent="claude-code:1", path="/new", timestamp=140.0),
            WriteEvent(agent="cursor:2", path="/new", timestamp=150.0),
        ]
        result = detect_conflicts(events, window_seconds=60.0, opensre_agent_id=OPENSRE_ID)
        assert [c.path for c in result] == ["/new", "/old"]
        assert result[0].last_seen == 150.0
        assert result[1].last_seen == 105.0
