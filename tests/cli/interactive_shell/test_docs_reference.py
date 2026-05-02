"""Tests for the documentation-grounding helpers used by the interactive shell."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.cli.interactive_shell import docs_reference
from app.cli.interactive_shell.docs_reference import (
    DocPage,
    _discover_docs_cached,
    _excerpt,
    _query_tokens,
    build_docs_index,
    build_docs_reference_text,
    discover_docs,
    find_relevant_docs,
)


@pytest.fixture(autouse=True)
def _clear_doc_cache() -> Iterator[None]:
    """Reset the per-process docs cache so each test sees a fresh tree."""
    _discover_docs_cached.cache_clear()
    yield
    _discover_docs_cached.cache_clear()


def _write_doc(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_docs(root: Path) -> None:
    _write_doc(
        root,
        "datadog.mdx",
        '---\ntitle: "Datadog"\n---\n\n'
        "### Step 1: Create API Key\n\n"
        "In Datadog, create an API Key under organizational settings.\n\n"
        "### Step 2: Configure OpenSRE\n\n"
        "Set DD_API_KEY and DD_APP_KEY in your environment.\n",
    )
    _write_doc(
        root,
        "deployment.mdx",
        '---\ntitle: "Deployment"\n---\n\n'
        "OpenSRE can deploy to Railway, EC2, or LangGraph Cloud.\n\n"
        "Use `opensre deploy` to deploy the agent.\n",
    )
    _write_doc(
        root,
        "quickstart.mdx",
        '---\ntitle: "Quickstart"\n---\n\nInstall OpenSRE and run your first investigation.\n',
    )
    _write_doc(
        root,
        "tutorials/investigating-task-failures.mdx",
        "# Investigating task failures\n\n"
        "Walk through how to investigate a failed task using OpenSRE.\n",
    )
    # Asset content under skip dirs MUST be excluded from the index.
    _write_doc(root, "images/datadog.mdx", "should be skipped")
    _write_doc(root, "assets/anything.mdx", "should be skipped")


class TestDiscoverDocs:
    def test_returns_empty_list_when_root_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "no-docs"
        assert discover_docs(missing) == []

    def test_walks_root_and_skips_asset_dirs(self, tmp_path: Path) -> None:
        _seed_docs(tmp_path)
        pages = discover_docs(tmp_path)
        slugs = {p.slug for p in pages}
        assert "datadog" in slugs
        assert "deployment" in slugs
        assert "quickstart" in slugs
        assert "investigating-task-failures" in slugs
        # Anything under images/ or assets/ must be skipped.
        relpaths = {p.relpath for p in pages}
        assert all(not r.startswith("images/") for r in relpaths)
        assert all(not r.startswith("assets/") for r in relpaths)

    def test_extracts_title_from_frontmatter(self, tmp_path: Path) -> None:
        _seed_docs(tmp_path)
        pages = discover_docs(tmp_path)
        by_slug = {p.slug: p for p in pages}
        assert by_slug["datadog"].title == "Datadog"
        assert by_slug["deployment"].title == "Deployment"

    def test_falls_back_to_first_heading_when_no_frontmatter(self, tmp_path: Path) -> None:
        _seed_docs(tmp_path)
        pages = discover_docs(tmp_path)
        by_slug = {p.slug: p for p in pages}
        assert by_slug["investigating-task-failures"].title == "Investigating task failures"

    def test_strips_frontmatter_from_body(self, tmp_path: Path) -> None:
        _seed_docs(tmp_path)
        pages = discover_docs(tmp_path)
        by_slug = {p.slug: p for p in pages}
        # Frontmatter delimiters and the title line must NOT appear in the body
        # (they would otherwise leak into the LLM grounding context).
        assert "title:" not in by_slug["datadog"].body
        assert by_slug["datadog"].body.lstrip().startswith("###")


class TestQueryTokens:
    def test_strips_stopwords_and_short_tokens(self) -> None:
        tokens = _query_tokens("How do I configure Datadog?")
        # Stopwords are removed, 'datadog' / 'configure' remain.
        assert "datadog" in tokens
        assert "configure" in tokens
        assert "how" not in tokens
        assert "do" not in tokens
        assert "i" not in tokens

    def test_drops_opensre_brand_token(self) -> None:
        # Every doc mentions "opensre" so it would otherwise dominate ranking.
        tokens = _query_tokens("how do I install opensre")
        assert "opensre" not in tokens
        assert "install" in tokens


class TestFindRelevantDocs:
    def test_empty_query_returns_empty(self, tmp_path: Path) -> None:
        _seed_docs(tmp_path)
        pages = discover_docs(tmp_path)
        # Query with only stopwords should not match anything.
        assert find_relevant_docs("how do I", pages) == []

    def test_ranks_datadog_page_first_for_datadog_query(self, tmp_path: Path) -> None:
        _seed_docs(tmp_path)
        pages = discover_docs(tmp_path)
        results = find_relevant_docs("how do I configure Datadog?", pages)
        assert results, "expected at least one match"
        assert results[0].slug == "datadog"

    def test_ranks_deployment_page_first_for_deploy_query(self, tmp_path: Path) -> None:
        _seed_docs(tmp_path)
        pages = discover_docs(tmp_path)
        results = find_relevant_docs("how do I deploy this?", pages)
        assert results
        assert results[0].slug == "deployment"

    def test_caps_results_at_top_n(self, tmp_path: Path) -> None:
        _seed_docs(tmp_path)
        pages = discover_docs(tmp_path)
        results = find_relevant_docs("install configure deploy investigate", pages, top_n=2)
        assert len(results) <= 2

    def test_nested_page_with_weak_match_is_not_dropped_by_depth(self, tmp_path: Path) -> None:
        """A page whose only match is a single body token, nested deep enough
        that the depth penalty equals or exceeds its raw score, must still
        surface as a lower-ranked result instead of being excluded entirely.

        Regression: previously the depth penalty was applied unconditionally
        before the score>0 filter, so a page with raw_score=1 at depth=2
        scored -1 and was dropped from results.
        """
        # Page nested 2 levels deep whose only match for "masking" is a single
        # body-token mention. raw_score == 1, depth == 2, so without clamping
        # the final score would be -1 and the page would be filtered out.
        _write_doc(
            tmp_path,
            "tutorials/advanced/notes.mdx",
            "# Notes\n\nWe briefly mention masking in this tutorial.\n",
        )
        pages = discover_docs(tmp_path)
        results = find_relevant_docs("masking", pages)
        slugs = [p.slug for p in results]
        assert "notes" in slugs, (
            "weak nested match must still surface, not be dropped by depth alone"
        )


class TestBuildDocsReferenceText:
    def test_returns_empty_when_no_docs_present(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Point the module at a non-existent docs root.
        monkeypatch.setattr(docs_reference, "_DOCS_ROOT", tmp_path / "missing")
        assert build_docs_reference_text("anything") == ""

    def test_includes_relevant_doc_excerpt_and_index(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _seed_docs(tmp_path)
        monkeypatch.setattr(docs_reference, "_DOCS_ROOT", tmp_path)
        text = build_docs_reference_text("how do I configure Datadog?")
        assert "datadog.mdx" in text
        assert "API Key" in text
        # The compact index of all pages must always be appended so the LLM
        # can suggest other relevant pages even when one ranked highest.
        assert "docs index" in text
        assert "deployment.mdx" in text

    def test_truncates_to_max_chars(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _seed_docs(tmp_path)
        monkeypatch.setattr(docs_reference, "_DOCS_ROOT", tmp_path)
        text = build_docs_reference_text("Datadog", max_chars=120)
        assert len(text) <= 200
        assert "truncated" in text


class TestBuildDocsIndex:
    def test_lists_all_pages_with_titles(self, tmp_path: Path) -> None:
        _seed_docs(tmp_path)
        pages = discover_docs(tmp_path)
        index = build_docs_index(pages)
        assert "datadog.mdx: Datadog" in index
        assert "deployment.mdx: Deployment" in index

    def test_returns_empty_string_for_no_pages(self) -> None:
        assert build_docs_index([]) == ""


class TestExcerpt:
    def test_returns_full_body_when_short(self) -> None:
        body = "Short body."
        assert _excerpt(body, max_chars=100) == "Short body."

    def test_truncates_long_body_with_marker(self) -> None:
        body = ("paragraph one. " * 10) + "\n\n" + ("paragraph two. " * 10)
        out = _excerpt(body, max_chars=80)
        assert "truncated" in out


class TestDocPageDataclass:
    def test_is_hashable_and_immutable(self) -> None:
        page = DocPage(slug="x", relpath="x.mdx", title="X", body="hello")
        # frozen dataclasses are hashable, so they can be stored in sets.
        assert page in {page}
