"""The worker must run each record under the scope of the org that asked for it.

A long-lived queue-draining thread cannot inherit a scope at ``start()``: by the
time it drains record two, the turn that started it is gone and its scope belongs
to someone else. Scope is rebuilt per record from what the record stored.
"""

from __future__ import annotations

from typing import Any

import pytest

from config.scope_context import current_scope
from gateway.core.investigations.chat_worker import ChatInvestigationWorker
from gateway.core.storage.investigations.store import InvestigationStatus


def _scope_capturing_runner(seen: list[str | None]) -> Any:
    """A fake pipeline that records the org it was run as."""

    def _run(trigger: dict[str, Any]) -> dict[str, Any]:
        _ = trigger
        scope = current_scope()
        seen.append(scope.principal.id if scope and scope.principal else None)
        return {"report": "done"}

    return _run


class TestPerRecordScope:
    def test_the_runner_sees_the_launching_org(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """Integration resolution is scope-keyed; unbound reads the wrong store."""
        register_notifier(notifier)
        record = make_record(org_id="org-alpha")
        seen: list[str | None] = []

        ChatInvestigationWorker(
            store, runner=_scope_capturing_runner(seen), artifacts_dir=tmp_path
        )._process_investigation(record)

        assert seen == ["org-alpha"]

    def test_the_second_record_does_not_inherit_the_first_org(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """The cross-tenant read this design exists to prevent.

        One worker, two orgs. Binding a scope once at ``start()`` — or reusing a
        single ``copy_context()`` — would run org-beta's investigation against
        org-alpha's integrations and post the result into org-beta's thread.
        """
        register_notifier(notifier)
        first = make_record(org_id="org-alpha")
        second = make_record(org_id="org-beta")
        seen: list[str | None] = []
        worker = ChatInvestigationWorker(
            store, runner=_scope_capturing_runner(seen), artifacts_dir=tmp_path
        )

        worker._process_investigation(first)
        worker._process_investigation(second)

        assert seen == ["org-alpha", "org-beta"]

    def test_the_scope_does_not_outlive_the_record(
        self, store, make_record, notifier, register_notifier, tmp_path
    ):
        """The binding is scoped to the run, so the idle worker holds no authority."""
        register_notifier(notifier)
        record = make_record(org_id="org-alpha")

        ChatInvestigationWorker(
            store, runner=_scope_capturing_runner([]), artifacts_dir=tmp_path
        )._process_investigation(record)

        scope = current_scope()
        assert scope is None or scope.principal is None or scope.principal.id != "org-alpha"

    @pytest.mark.parametrize("org_id", [None, ""], ids=["no-scope-key", "blank-org"])
    def test_a_record_with_no_usable_scope_fails_closed(
        self, org_id, store, make_record, notifier, register_notifier, tmp_path
    ):
        """Running unbound is worse than not running: it reads whatever is default.

        Both spellings are reachable and they are not the same branch. A record
        written before the scope key existed has no ``scope`` at all; a launch from
        an unscoped turn writes ``org_id: ""``, because the launcher seeds the dict
        before it knows whether a principal is bound. ``Principal.org("")`` raises,
        so a guard that only rejects ``None`` turns the second one into a crash
        inside the run rather than a clean refusal the reader is told about.
        """
        register_notifier(notifier)
        record = make_record(org_id=org_id)

        def _must_not_run(trigger: dict[str, Any]) -> dict[str, Any]:
            _ = trigger
            raise AssertionError("the pipeline ran with no scope to bind")

        ChatInvestigationWorker(
            store, runner=_must_not_run, artifacts_dir=tmp_path
        )._process_investigation(record)

        stored = store.get(record.id)
        assert stored.status is InvestigationStatus.FAILED
        assert stored.error == "unbound_scope"
        assert notifier.failures, "the reader was left waiting on a run that never started"
