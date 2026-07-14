"""Unit tests for the long-term memory domain store."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.domain.memory import (
    MAX_BODY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MemoryRecord,
    auto_extract_enabled,
    delete_memory,
    is_valid_slug,
    list_memories,
    load_memory,
    memory_dir,
    memory_enabled,
    render_prompt_index,
    save_memory,
    search_memories,
    slugify,
)
from core.domain.memory.frontmatter import parse_memory_file, serialize_memory


@pytest.fixture(autouse=True)
def _isolated_memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    memory_home = tmp_path / "memory"
    monkeypatch.setenv("OPENSRE_MEMORY_DIR", str(memory_home))
    monkeypatch.delenv("OPENSRE_MEMORY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_MEMORY_AUTOEXTRACT_DISABLED", raising=False)
    return memory_home


def _save(slug: str = "prod-cluster", body: str = "Prod cluster is eks-prod-1.") -> MemoryRecord:
    result = save_memory(
        slug=slug,
        memory_type="infrastructure",
        description=f"Facts about {slug}",
        body=body,
    )
    assert result is not None
    return result[0]


class TestSlugs:
    def test_slugify_normalizes(self) -> None:
        assert slugify("  Prod Cluster / Conventions!  ") == "prod-cluster-conventions"

    @pytest.mark.parametrize("bad", ["", "../evil", "UPPER", "a b", "memory", "-lead", "trail-"])
    def test_invalid_slugs_rejected(self, bad: str) -> None:
        assert not is_valid_slug(bad)

    def test_valid_slug(self) -> None:
        assert is_valid_slug("user-profile-2")


class TestSaveAndLoad:
    def test_create_then_load_roundtrip(self) -> None:
        record = _save()
        loaded = load_memory("prod-cluster")
        assert loaded == record
        assert loaded is not None and loaded.memory_type == "infrastructure"

    def test_update_preserves_created_at_and_single_file(self) -> None:
        first = _save()
        result = save_memory(
            slug="prod-cluster",
            memory_type="infrastructure",
            description="updated description",
            body="Now eks-prod-2.",
        )
        assert result is not None
        updated, created = result
        assert created is False
        assert updated.created_at == first.created_at
        files = [p.name for p in memory_dir().glob("*.md") if p.name != "MEMORY.md"]
        assert files == ["prod-cluster.md"]

    def test_invalid_slug_raises(self) -> None:
        with pytest.raises(ValueError):
            save_memory(slug="../evil", memory_type="user", description="x", body="y")

    def test_description_flattened_and_capped(self) -> None:
        result = save_memory(
            slug="caps",
            memory_type="user",
            description="line one\nline two" + "x" * 500,
            body="body",
        )
        assert result is not None
        record = result[0]
        assert "\n" not in record.description
        assert len(record.description) <= MAX_DESCRIPTION_CHARS

    def test_body_truncated_at_cap(self) -> None:
        record = _save(body="z" * (MAX_BODY_CHARS + 1_000))
        assert len(record.body) <= MAX_BODY_CHARS
        assert record.body.endswith("...[truncated]")


class TestListDeleteSearch:
    def test_list_orders_by_updated_desc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stamps = iter(["2026-07-01T00:00:00+00:00", "2026-07-02T00:00:00+00:00"])
        monkeypatch.setattr("core.domain.memory.store._now_iso", lambda: next(stamps))
        _save("older")
        _save("newer")
        assert [r.slug for r in list_memories()] == ["newer", "older"]

    def test_list_skips_malformed_and_index(self) -> None:
        _save()
        (memory_dir() / "broken.md").write_text("not frontmatter", encoding="utf-8")
        assert [r.slug for r in list_memories()] == ["prod-cluster"]

    def test_list_does_not_create_dir(self) -> None:
        assert list_memories() == []
        assert not memory_dir().exists()

    def test_delete_existing_and_missing(self) -> None:
        _save()
        assert delete_memory("prod-cluster") is True
        assert delete_memory("prod-cluster") is False
        assert load_memory("prod-cluster") is None

    def test_search_matches_slug_description_body(self) -> None:
        _save(body="The flaky service is checkout-api.")
        assert [r.slug for r in search_memories("checkout-API")] == ["prod-cluster"]
        assert search_memories("nomatch") == []


class TestIndex:
    def test_memory_md_rebuilt_on_save_and_delete(self) -> None:
        _save()
        index = (memory_dir() / "MEMORY.md").read_text(encoding="utf-8")
        assert "prod-cluster" in index
        delete_memory("prod-cluster")
        index = (memory_dir() / "MEMORY.md").read_text(encoding="utf-8")
        assert "prod-cluster" not in index

    def test_render_prompt_index_empty_and_populated(self) -> None:
        assert render_prompt_index() == ""
        _save()
        rendered = render_prompt_index()
        assert "[infrastructure] prod-cluster" in rendered

    def test_render_prompt_index_respects_char_cap(self) -> None:
        for i in range(10):
            _save(f"mem-{i}")
        rendered = render_prompt_index(max_chars=120)
        assert len(rendered.splitlines()) < 10
        assert "more memories (use memory_recall)" in rendered


class TestFrontmatter:
    def test_roundtrip_with_fence_in_body(self) -> None:
        record = _save(body="intro\n---\nafter a fence")
        loaded = load_memory("prod-cluster")
        assert loaded is not None and loaded.body == record.body

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "no fences at all",
            "---\nname: x\n",  # unclosed
            "---\nname: bad slug!\ntype: user\ndescription: d\ncreated: c\nupdated: u\n---\n",
            "---\nname: ok\ntype: not-a-type\ndescription: d\ncreated: c\nupdated: u\n---\n",
            "---\nnamewithoutcolon\n---\n",
        ],
    )
    def test_malformed_returns_none(self, text: str) -> None:
        assert parse_memory_file(text) is None

    def test_serialize_parse_identity(self) -> None:
        record = MemoryRecord(
            slug="a-slug",
            memory_type="preference",
            description="desc",
            created_at="2026-07-09T00:00:00+00:00",
            updated_at="2026-07-09T00:00:00+00:00",
            body="body text",
        )
        assert parse_memory_file(serialize_memory(record)) == record


class TestSettings:
    def test_gate_matrix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert memory_enabled() is True
        assert auto_extract_enabled() is True
        monkeypatch.setenv("OPENSRE_MEMORY_AUTOEXTRACT_DISABLED", "1")
        assert memory_enabled() is True
        assert auto_extract_enabled() is False
        monkeypatch.setenv("OPENSRE_MEMORY_DISABLED", "true")
        assert memory_enabled() is False
        assert auto_extract_enabled() is False
