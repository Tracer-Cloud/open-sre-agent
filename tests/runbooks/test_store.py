from __future__ import annotations

from pathlib import Path

import pytest

from app.runbooks import store


def _patch_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "opensre_home"
    monkeypatch.setattr("app.constants.OPENSRE_HOME_DIR", home)
    return home


def _write_runbook(path: Path, frontmatter: str, body: str = "# Body") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")


def test_load_all_returns_empty_when_dir_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_home(monkeypatch, tmp_path)

    assert store.load_all() == []


def test_load_all_parses_valid_runbook(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    _write_runbook(
        home / "runbooks" / "payments-oom.md",
        frontmatter=(
            "service: payments-api\n"
            "triggers:\n  - oom\n  - memory\n"
            "category: resource_exhaustion\n"
            "title: Payments OOM"
        ),
        body="Bump JVM heap to 2G.",
    )

    runbooks = store.load_all()

    assert len(runbooks) == 1
    rb = runbooks[0]
    assert rb.slug == "payments-oom"
    assert rb.title == "Payments OOM"
    assert rb.service == "payments-api"
    assert rb.category == "resource_exhaustion"
    assert rb.triggers == ("oom", "memory")
    assert "Bump JVM heap" in rb.body


def test_load_all_skips_runbook_without_triggers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    _write_runbook(
        home / "runbooks" / "invalid.md",
        frontmatter="service: foo",
    )
    _write_runbook(
        home / "runbooks" / "valid.md",
        frontmatter="triggers:\n  - oom",
    )

    runbooks = store.load_all()

    assert [rb.slug for rb in runbooks] == ["valid"]


def test_save_validates_triggers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_home(monkeypatch, tmp_path)
    source = tmp_path / "input.md"
    source.write_text("---\nservice: foo\n---\nbody", encoding="utf-8")

    with pytest.raises(store.RunbookValidationError):
        store.save(source)


def test_save_copies_into_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    source = tmp_path / "payments-oom.md"
    source.write_text(
        "---\nservice: payments-api\ntriggers:\n  - oom\n---\nbody",
        encoding="utf-8",
    )

    saved = store.save(source)

    assert saved.slug == "payments-oom"
    assert saved.path == home / "runbooks" / "payments-oom.md"
    assert saved.path.exists()


def test_remove_returns_true_when_present_false_otherwise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    _write_runbook(home / "runbooks" / "x.md", frontmatter="triggers:\n  - a")

    assert store.remove("x") is True
    assert not (home / "runbooks" / "x.md").exists()
    assert store.remove("x") is False


def test_to_dict_round_trips_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    _write_runbook(
        home / "runbooks" / "x.md",
        frontmatter="service: s\ntriggers:\n  - t1",
        body="content",
    )

    rb = store.load_all()[0]
    payload = rb.to_dict()

    assert payload["slug"] == "x"
    assert payload["service"] == "s"
    assert payload["triggers"] == ["t1"]
    assert payload["body"] == "content"
    assert payload["path"] == str(home / "runbooks" / "x.md")
