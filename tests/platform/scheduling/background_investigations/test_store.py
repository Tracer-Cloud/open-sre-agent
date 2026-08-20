"""Durability contract for the background investigation store."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from platform.scheduling.background_investigations.store import (
    BackgroundInvestigationStore,
    UnreadableBackgroundInvestigationsError,
)
from platform.scheduling.background_investigations.types import BackgroundInvestigationRecord


def _store(tmp_path: Path, **kwargs: Any) -> BackgroundInvestigationStore:
    return BackgroundInvestigationStore(tmp_path / "background" / "investigations.json", **kwargs)


def _record(task_id: str = "bg-1", status: str = "completed") -> BackgroundInvestigationRecord:
    return BackgroundInvestigationRecord(
        task_id=task_id,
        status=status,
        command="/investigate checkout-latency",
        root_cause="pool saturation",
        top_analysis=("rds cpu spike",),
        next_steps=("raise pool size",),
        stats={"tool_call_count": 4},
        notification_results={"telegram": "sent"},
    )


_WRITER = (
    "from platform.scheduling.background_investigations.store import "
    "background_investigation_store as s;"
    "from platform.scheduling.background_investigations.types import "
    "BackgroundInvestigationRecord as R;"
    "s().save(R(task_id='bg-xproc', status='completed', command='/investigate generic',"
    "root_cause='pool saturation'));"
    "print('WROTE')"
)
_READER = (
    "from platform.scheduling.background_investigations.store import "
    "background_investigation_store as s;"
    "r = s().get('bg-xproc');"
    "assert r is not None, 'record not visible from a second process';"
    "assert r.status == 'completed', r.status;"
    "assert r.root_cause == 'pool saturation', r.root_cause;"
    "print('READ')"
)


def _run(script: str, home: Path) -> subprocess.CompletedProcess[str]:
    """Run ``script`` in a fresh interpreter rooted at ``home``.

    ``cwd`` is the repo so the local ``platform`` package wins over the stdlib
    module of the same name.
    """
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=Path(__file__).resolve().parents[4],
        env={**os.environ, "OPENSRE_HOME": str(home)},
    )


def test_a_record_written_by_one_process_is_readable_by_another(tmp_path: Path) -> None:
    """The requirement is cross-process readability, and a second store instance
    inside one interpreter cannot show that: it shares the same page cache and
    the same import of ``opensre_home``. Two interpreters can."""
    wrote = _run(_WRITER, tmp_path)
    assert wrote.returncode == 0, wrote.stderr
    assert "WROTE" in wrote.stdout

    read = _run(_READER, tmp_path)

    assert read.returncode == 0, read.stderr
    assert "READ" in read.stdout


def test_save_then_get_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(_record())

    loaded = store.get("bg-1")

    assert loaded is not None
    assert loaded.task_id == "bg-1"
    assert loaded.root_cause == "pool saturation"
    assert loaded.top_analysis == ("rds cpu spike",)
    assert loaded.notification_results == {"telegram": "sent"}


def test_get_returns_none_for_an_unknown_task(tmp_path: Path) -> None:
    assert _store(tmp_path).get("nope") is None


def test_missing_file_reads_as_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.list_recent() == []
    assert store.get("bg-1") is None


def test_save_stamps_timestamps_and_preserves_created_at_on_update(tmp_path: Path) -> None:
    """Ordering must stay stable once a record exists, so an update moves
    updated_at but must not reset created_at."""
    store = _store(tmp_path)
    record = _record()
    store.save(record)
    first = store.get("bg-1")
    assert first is not None
    assert first.created_at > 0
    assert first.updated_at > 0

    record.status = "failed"
    store.save(record)
    second = store.get("bg-1")

    assert second is not None
    assert second.status == "failed"
    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at


def test_save_updates_in_place_rather_than_duplicating(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(_record(status="running"))
    store.save(_record(status="completed"))

    recent = store.list_recent()

    assert len(recent) == 1
    assert recent[0].status == "completed"


def test_list_recent_returns_newest_first_and_honours_the_limit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for index in range(5):
        store.save(_record(task_id=f"bg-{index}"))

    recent = store.list_recent(limit=3)

    assert [r.task_id for r in recent] == ["bg-4", "bg-3", "bg-2"]


def test_records_are_bounded_and_rotation_keeps_the_newest(tmp_path: Path) -> None:
    """Frozen clock, because rotation and ordering read the same comparison and
    the tie is the harder case: distinct timestamps cannot distinguish a correct
    implementation from one that evicts the wrong end."""
    store = _store(tmp_path, max_records=3, clock=lambda: 1000.0)
    for index in range(6):
        store.save(_record(task_id=f"bg-{index}"))

    recent = store.list_recent(limit=100)

    assert len(recent) == 3
    assert [r.task_id for r in recent] == ["bg-5", "bg-4", "bg-3"]


def test_same_tick_records_still_list_newest_first(tmp_path: Path) -> None:
    """Several completions can land inside one clock tick, and a coarse platform
    clock makes that the normal case rather than the rare one. ``sorted`` is
    stable, so sorting with ``reverse=True`` keeps equal timestamps in document
    order and hands them back oldest first, which inverts what list_recent
    promises. The real-clock tests above cannot catch it, because each write
    costs more than one tick."""
    store = _store(tmp_path, clock=lambda: 1000.0)
    for index in range(4):
        store.save(_record(task_id=f"bg-{index}"))

    recent = store.list_recent()

    assert [r.task_id for r in recent] == ["bg-3", "bg-2", "bg-1", "bg-0"]


def test_document_is_written_atomically_with_owner_only_permissions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(_record())

    path = tmp_path / "background" / "investigations.json"
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # No temp file survives a successful write.
    assert [p.name for p in path.parent.iterdir() if p.name.startswith(".investigations")] == []

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert isinstance(payload["records"], list)


def test_unreadable_document_raises_rather_than_reading_as_empty(tmp_path: Path) -> None:
    """Returning empty would let the next write replace a damaged history with a
    fresh one and lose every record in it."""
    path = tmp_path / "background" / "investigations.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    store = _store(tmp_path)

    with pytest.raises(UnreadableBackgroundInvestigationsError):
        store.list_recent()


def test_document_with_an_unexpected_shape_raises(tmp_path: Path) -> None:
    path = tmp_path / "background" / "investigations.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "records": "nope"}), encoding="utf-8")

    with pytest.raises(UnreadableBackgroundInvestigationsError):
        _store(tmp_path).list_recent()


def test_a_corrupt_row_is_skipped_not_fatal(tmp_path: Path) -> None:
    """Row-level validation, matching TaskRecord.from_dict: one bad entry must
    not cost the reader the whole history."""
    path = tmp_path / "background" / "investigations.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {"task_id": "good", "status": "completed", "command": "/x", "updated_at": 2.0},
                    {"status": "completed", "command": "/y"},
                    {"task_id": 42, "status": "completed", "command": "/z"},
                ],
            }
        ),
        encoding="utf-8",
    )

    recent = _store(tmp_path).list_recent()

    assert [r.task_id for r in recent] == ["good"]


def test_a_non_numeric_timestamp_does_not_kill_the_sort_key(tmp_path: Path) -> None:
    """``from_dict`` coerces timestamps but the ordering comparison used to call
    ``float()`` raw, so one hand-edited row raised ``ValueError`` out of every
    later save. The runner swallows that at debug level, so persistence would
    have died silently and permanently."""
    path = tmp_path / "background" / "investigations.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "task_id": "poisoned",
                        "status": "completed",
                        "command": "/x",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = _store(tmp_path)

    store.save(_record(task_id="fresh"))

    assert [r.task_id for r in store.list_recent()] == ["fresh", "poisoned"]


def test_the_home_directory_is_not_resolved_at_construction(monkeypatch) -> None:
    """opensre_home() raises on an org-bound turn whose mount belongs to someone
    else, so it must be reached per call and never at import or construction."""
    calls: list[int] = []

    def _explode() -> Path:
        calls.append(1)
        raise RuntimeError("opensre_home() must not be called at construction")

    monkeypatch.setattr(
        "platform.scheduling.background_investigations.store._default_path", _explode
    )

    store = BackgroundInvestigationStore()

    assert calls == []
    with pytest.raises(RuntimeError):
        _ = store.path


def test_nothing_is_evicted_below_capacity(tmp_path: Path) -> None:
    """Regression guard. Rotation slices the stored rows, and an off-by-one in that
    slice deletes rows long before the bound is reached, where no ordering test
    would notice."""
    store = _store(tmp_path, max_records=4)
    for index in range(3):
        store.save(_record(task_id=f"bg-{index}"))

    assert sorted(r.task_id for r in store.list_recent(99)) == ["bg-0", "bg-1", "bg-2"]


def test_the_default_store_retains_its_full_bound(tmp_path: Path) -> None:
    """Regression guard at the production bound. A slice that under-retains settles
    at a fixed point well below _MAX_RECORDS, which every small-bound test misses."""
    store = _store(tmp_path)
    for index in range(120):
        store.save(_record(task_id=f"bg-{index}"))

    assert len(store.list_recent(999)) == 100


def test_a_record_survives_rotation_when_its_timestamp_sorts_lowest(tmp_path: Path) -> None:
    """Rotation used to rank the handed record against the stored ones, so a clock
    that stepped back at capacity evicted the write it had just been given and
    still returned normally."""
    clock = {"now": 2000.0}
    store = _store(tmp_path, max_records=3, clock=lambda: clock["now"])
    for index in range(3):
        store.save(_record(task_id=f"old-{index}"))
    clock["now"] = 1400.0

    store.save(_record(task_id="fresh"))

    assert store.get("fresh") is not None


def test_an_infinite_stored_timestamp_cannot_evict_the_handed_record(tmp_path: Path) -> None:
    """``json.loads("1e999")`` is ``inf`` from ordinary JSON, and no clock ever
    outranks it, so unlike a stepped-back clock this never heals on its own."""
    path = tmp_path / "background" / "investigations.json"
    path.parent.mkdir(parents=True)
    rows = [
        {"task_id": f"poison-{i}", "status": "completed", "command": "/x", "updated_at": 1e999}
        for i in range(3)
    ]
    path.write_text(json.dumps({"version": 1, "records": rows}), encoding="utf-8")
    store = _store(tmp_path, max_records=3)

    store.save(_record(task_id="fresh"))

    assert store.get("fresh") is not None


def test_a_degenerate_bound_still_keeps_the_handed_record(tmp_path: Path) -> None:
    """``max_records`` is a public parameter, and 0 or a negative used to invert the
    slice: 0 kept everything, -2 destroyed everything on every write."""
    for bound in (0, -2):
        store = _store(tmp_path / str(bound), max_records=bound)
        for index in range(3):
            store.save(_record(task_id=f"bg-{index}"))

        assert [r.task_id for r in store.list_recent(99)] == ["bg-2"], bound


def test_out_of_range_and_infinite_timestamps_do_not_raise(tmp_path: Path) -> None:
    """A 400-digit integer is what ``json.loads`` yields for a large literal, and
    ``float()`` raises ``OverflowError`` on it. Unguarded, that escaped save, get
    and list_recent alike."""
    path = tmp_path / "background" / "investigations.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"version": 1, "records": ['
        '{"task_id": "huge", "status": "completed", "command": "/x", "updated_at": '
        + "9"
        * 400
        + "},"
        '{"task_id": "inf", "status": "completed", "command": "/y", "updated_at": 1e999}]}',
        encoding="utf-8",
    )
    store = _store(tmp_path)

    store.save(_record(task_id="fresh"))

    assert store.get("huge") is not None
    assert store.get("fresh") is not None
    assert len(store.list_recent(99)) == 3


def test_a_same_id_update_survives_a_poisoned_created_at(tmp_path: Path) -> None:
    """The created_at carry-over read the stored value with a raw ``float()``, which
    the sort-key guard does not cover: only a save whose task_id already exists
    reaches that line."""
    path = tmp_path / "background" / "investigations.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"version": 1, "records": [{"task_id": "bg-1", "status": "running",'
        ' "command": "/x", "created_at": ' + "9" * 400 + ', "updated_at": 1.0}]}',
        encoding="utf-8",
    )
    store = _store(tmp_path)

    store.save(_record(task_id="bg-1", status="completed"))

    stored = store.get("bg-1")
    assert stored is not None
    assert stored.status == "completed"


def test_list_recent_is_newest_first_when_the_document_is_not_ascending(tmp_path: Path) -> None:
    """Regression guard. Retaining the handed record outside the sort means the
    document is no longer ascending, so ordering must come from the timestamps
    rather than from document position."""
    clock = {"now": 2000.0}
    store = _store(tmp_path, clock=lambda: clock["now"])
    store.save(_record(task_id="older"))
    store.save(_record(task_id="newer"))
    clock["now"] = 1400.0
    store.save(_record(task_id="stepped-back"))

    assert [r.task_id for r in store.list_recent()] == ["newer", "older", "stepped-back"]


def test_the_shell_and_a_chat_turn_share_one_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shell binds no storage scope and a chat transport binds the deployment's
    organization, so the two otherwise write and read different files and a chat
    lookup finds nothing. Both must land on the org root."""
    from config.constants import paths
    from config.constants.billing import ORGANIZATION_ID_ENV
    from config.principal import Actor, Principal, StorageScope
    from config.scope_context import bound_storage_scope

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_acme")

    # The shell writes with nothing bound.
    BackgroundInvestigationStore().save(_record(task_id="bg-shell"))

    # A Telegram turn reads with the organization bound.
    scope = StorageScope(principal=Principal.org("org_acme"), actor=Actor(id="U_ALICE"))
    with bound_storage_scope(scope):
        found = BackgroundInvestigationStore().get("bg-shell")

    assert found is not None
    assert found.root_cause == "pool saturation"
    assert (tmp_path / "orgs" / "org_acme" / "background" / "investigations.json").is_file()


def test_a_machine_with_no_organization_keeps_the_plain_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A laptop names no organization and has no second surface to share with."""
    from config.constants import paths
    from config.constants.billing import ORGANIZATION_ID_ENV

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)

    BackgroundInvestigationStore().save(_record(task_id="bg-laptop"))

    assert (tmp_path / "background" / "investigations.json").is_file()
    assert not (tmp_path / "orgs").exists()


def test_notify_channels_survive_a_record_save(tmp_path: Path) -> None:
    """Records and preferences share one document, so each write must carry the
    other through. save() re-reads channels inside the lock it already holds."""
    store = _store(tmp_path)
    store.set_notify_channels(("telegram", "email"))

    store.save(_record(task_id="bg-after-prefs"))

    assert store.notify_channels() == ("telegram", "email")
    assert store.get("bg-after-prefs") is not None


def test_notify_channels_survive_rotation(tmp_path: Path) -> None:
    """Rotation rewrites the whole document. Preferences must not age out with the
    records they sit beside."""
    store = _store(tmp_path, max_records=3)
    store.set_notify_channels(("rocketchat",))
    for index in range(6):
        store.save(_record(task_id=f"bg-{index}"))

    assert store.notify_channels() == ("rocketchat",)
    assert len(store.list_recent(limit=100)) == 3


def test_setting_channels_preserves_stored_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(_record(task_id="bg-keep"))

    store.set_notify_channels(("buzz",))

    assert store.get("bg-keep") is not None
    assert store.notify_channels() == ("buzz",)


def test_an_explicit_clear_is_not_read_as_unset(tmp_path: Path) -> None:
    """Clearing channels must persist as an empty list rather than falling back to
    a default, or turning notifications off would silently not stick."""
    store = _store(tmp_path)
    store.set_notify_channels(("telegram",))

    store.set_notify_channels(())

    assert store.notify_channels() == ()
