"""Tests for the AGENTS.md grounding helpers used by the interactive shell."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.agent_harness.grounding import agents_md_reference
from core.agent_harness.grounding._cache import excerpt
from core.agent_harness.grounding.agents_md_reference import (
    AgentsMdFile,
    AgentsMdReference,
)


def _write(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_agents_md(root: Path) -> None:
    _write(root, "AGENTS.md", "# Repo map\n\nTop-level guidance.\n")
    _write(root, "core/llm/AGENTS.md", "# Hosted LLM Runtime\n\nLLM API clients.\n")
    _write(root, "integrations/llm_cli/AGENTS.md", "# llm_cli\n\nSubprocess CLIs.\n")
    # Files that MUST be skipped — tests, vcs, caches, virtualenv, vendored deps.
    _write(root, "tests/AGENTS.md", "should be skipped")
    _write(root, ".git/AGENTS.md", "should be skipped")
    _write(root, "__pycache__/AGENTS.md", "should be skipped")
    _write(root, "node_modules/some-pkg/AGENTS.md", "should be skipped")
    _write(root, ".venv/lib/python3.13/site-packages/foo/AGENTS.md", "should be skipped")


class TestDiscoverAgentsMdFiles:
    def test_returns_empty_list_when_root_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "no-such-dir"
        assert AgentsMdReference().discover(missing) == []

    def test_walks_root_and_skips_excluded_dirs(self, tmp_path: Path) -> None:
        _seed_agents_md(tmp_path)
        files = AgentsMdReference().discover(tmp_path)
        relpaths = {f.relpath for f in files}
        assert "AGENTS.md" in relpaths
        assert "core/llm/AGENTS.md" in relpaths
        assert "integrations/llm_cli/AGENTS.md" in relpaths
        # None of the skip-dir paths should leak into the index.
        for r in relpaths:
            assert not r.startswith("tests/")
            assert not r.startswith(".git/")
            assert not r.startswith("__pycache__/")
            assert not r.startswith("node_modules/")
            assert not r.startswith(".venv/")

    def test_results_are_sorted_by_relpath(self, tmp_path: Path) -> None:
        _seed_agents_md(tmp_path)
        files = AgentsMdReference().discover(tmp_path)
        relpaths = [f.relpath for f in files]
        assert relpaths == sorted(relpaths)

    def test_body_is_verbatim_no_frontmatter_stripping(self, tmp_path: Path) -> None:
        # Unlike docs_reference, AGENTS.md files are plain Markdown — frontmatter
        # delimiters (if a contributor adds them) must be preserved verbatim.
        body = "---\nfoo: bar\n---\n\n# Title\n\nBody.\n"
        _write(tmp_path, "AGENTS.md", body)
        files = AgentsMdReference().discover(tmp_path)
        assert len(files) == 1
        assert files[0].body == body


class TestBuildAgentsMdReferenceText:
    def test_returns_empty_when_no_files_present(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(agents_md_reference, "REPO_ROOT", tmp_path / "missing")
        assert AgentsMdReference().build_text() == ""

    def test_concatenates_each_file_with_labelled_header(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _seed_agents_md(tmp_path)
        monkeypatch.setattr(agents_md_reference, "REPO_ROOT", tmp_path)
        text = AgentsMdReference().build_text()
        # Root file must be disambiguated from per-package files.
        assert "=== AGENTS.md (root) ===" in text
        assert "=== core/llm/AGENTS.md ===" in text
        assert "=== integrations/llm_cli/AGENTS.md ===" in text
        # Bodies must appear in the concatenated block.
        assert "Top-level guidance." in text
        assert "LLM API clients." in text
        assert "Subprocess CLIs." in text

    def test_caps_total_to_max_chars(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _seed_agents_md(tmp_path)
        monkeypatch.setattr(agents_md_reference, "REPO_ROOT", tmp_path)
        text = AgentsMdReference().build_text(max_chars=100)
        # Allow a small margin for the truncation marker appended at the cap.
        assert len(text) <= 200
        assert "truncated" in text


class TestExcerpt:
    def test_returns_full_body_when_short(self) -> None:
        assert excerpt("short body", max_chars=100) == "short body"

    def test_truncates_long_body_at_paragraph_boundary(self) -> None:
        body = ("paragraph one. " * 10) + "\n\n" + ("paragraph two. " * 10)
        out = excerpt(body, max_chars=80)
        assert "truncated" in out
        # The cut should happen at or before the second paragraph, since the
        # rfind for "\n\n" before max_chars is the preferred cutoff.
        assert "paragraph two" not in out


class TestAgentsMdFileDataclass:
    def test_is_hashable_and_immutable(self) -> None:
        f = AgentsMdFile(relpath="AGENTS.md", body="hello")
        assert f in {f}


class TestAgentsMdGroundingCache:
    def test_cache_maxsize_matches_implementation(self) -> None:
        stats = AgentsMdReference().stats()
        assert stats.maxsize == 32

    def test_repeated_discover_hits_parse_cache(self, tmp_path: Path) -> None:
        _seed_agents_md(tmp_path)
        ref = AgentsMdReference()
        ref.discover(tmp_path)
        info1 = ref.stats()
        ref.discover(tmp_path)
        info2 = ref.stats()
        assert info2.hits == info1.hits + 1
        assert info2.misses == info1.misses

    def test_invalidate_resets_stats(self, tmp_path: Path) -> None:
        _seed_agents_md(tmp_path)
        ref = AgentsMdReference()
        ref.discover(tmp_path)
        ref.discover(tmp_path)
        assert ref.stats().hits >= 1
        ref.invalidate()
        cleared = ref.stats()
        assert cleared.hits == 0
        assert cleared.misses == 0
        assert cleared.currsize == 0

    def test_file_edit_invalidates_and_refreshes_content(self, tmp_path: Path) -> None:
        ref = AgentsMdReference()
        _write(tmp_path, "AGENTS.md", "# Repo map\n\nOld content.\n")
        files1 = ref.discover(tmp_path)
        assert any("Old content" in f.body for f in files1)

        # Bump mtime well beyond filesystem mtime resolution so the
        # fingerprint definitely changes on the next discover call.
        target = tmp_path / "AGENTS.md"
        target.write_text("# Repo map\n\nNew refreshed content.\n", encoding="utf-8")
        st = target.stat()
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))

        files2 = ref.discover(tmp_path)
        assert any("New refreshed content" in f.body for f in files2)
        assert not any("Old content" in f.body for f in files2)
