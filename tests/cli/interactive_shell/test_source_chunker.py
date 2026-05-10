"""Tests for ``app.cli.interactive_shell.source_chunker``."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.cli.interactive_shell.source_chunker import (
    SourceChunk,
    chunk_markdown_file,
    chunk_path,
    chunk_python_file,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    target = tmp_path / name
    target.write_text(content, encoding="utf-8")
    return target


def test_python_file_with_multiple_funcs_and_class(tmp_path: Path) -> None:
    source = '''"""Module docstring."""

import os
from pathlib import Path


def alpha():
    return 1


async def beta(x: int) -> int:
    return x + 1


class Gamma:
    def method(self) -> None:
        pass
'''
    path = _write(tmp_path, "sample.py", source)
    chunks = chunk_python_file(path, repo_root=tmp_path)

    kinds = [c.kind for c in chunks]
    assert kinds == ["py_module", "py_func", "py_func", "py_class"]
    assert [c.symbol for c in chunks] == ["sample", "alpha", "beta", "Gamma"]
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 4

    by_symbol = {c.symbol: c for c in chunks}
    assert by_symbol["alpha"].start_line == 7 and by_symbol["alpha"].end_line == 8
    assert by_symbol["beta"].start_line == 11 and by_symbol["beta"].end_line == 12
    assert by_symbol["Gamma"].start_line == 15 and by_symbol["Gamma"].end_line == 17

    assert "def alpha" in by_symbol["alpha"].content
    assert "class Gamma" in by_symbol["Gamma"].content
    assert by_symbol["Gamma"].relpath == "sample.py"


def test_python_file_module_docstring_only(tmp_path: Path) -> None:
    path = _write(tmp_path, "doc_only.py", '"""Just a docstring."""\n')
    chunks = chunk_python_file(path, repo_root=tmp_path)

    assert len(chunks) == 1
    assert chunks[0].kind == "py_module"
    assert chunks[0].symbol == "doc_only"
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 1


def test_empty_python_file_returns_empty(tmp_path: Path) -> None:
    path = _write(tmp_path, "empty.py", "")
    assert chunk_python_file(path, repo_root=tmp_path) == []


def test_python_file_skips_private_module(tmp_path: Path) -> None:
    private = _write(tmp_path, "_private.py", "def helper():\n    return 1\n")
    init = _write(tmp_path, "__init__.py", "from .public import thing\n")
    assert chunk_python_file(private, repo_root=tmp_path) == []
    assert chunk_python_file(init, repo_root=tmp_path) == []


def test_python_file_skips_pycache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    cached = cache_dir / "x.py"
    cached.write_text("def f():\n    return 1\n", encoding="utf-8")
    assert chunk_python_file(cached, repo_root=tmp_path) == []


def test_python_file_with_syntax_error_returns_empty(tmp_path: Path) -> None:
    path = _write(tmp_path, "broken.py", "def oops(:\n    pass\n")
    assert chunk_python_file(path, repo_root=tmp_path) == []


def test_python_function_truncation_marker(tmp_path: Path) -> None:
    body_lines = [f"    x_{i} = {i}" for i in range(120)]
    source = "def big():\n" + "\n".join(body_lines) + "\n"
    path = _write(tmp_path, "big.py", source)
    chunks = chunk_python_file(path, repo_root=tmp_path)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.symbol == "big"
    assert chunk.start_line == 1
    # end_line preserves the real end of the function (signature + 120 body lines)
    assert chunk.end_line == 121
    assert chunk.content.rstrip().endswith("# … truncated, real end line: 121 …")


def test_python_decorator_extends_start_line(tmp_path: Path) -> None:
    source = "import functools\n\n\n@functools.cache\ndef cached(x):\n    return x\n"
    path = _write(tmp_path, "deco.py", source)
    chunks = chunk_python_file(path, repo_root=tmp_path)

    func = next(c for c in chunks if c.kind == "py_func")
    assert func.start_line == 4  # decorator line, not the def line
    assert func.end_line == 6


def test_markdown_with_frontmatter_and_h2_sections(tmp_path: Path) -> None:
    source = """---
title: Example
description: test fixture
---

Intro paragraph.

## First Section

First body.

## Second Section

Second body.
"""
    path = _write(tmp_path, "doc.md", source)
    chunks = chunk_markdown_file(path, repo_root=tmp_path)

    assert [c.symbol for c in chunks] == ["First Section", "Second Section"]
    assert all(c.kind == "md_section" for c in chunks)
    assert "title: Example" not in chunks[0].content
    assert chunks[0].content.startswith("## First Section")
    assert chunks[1].content.startswith("## Second Section")


def test_markdown_no_h2_emits_single_chunk(tmp_path: Path) -> None:
    source = "# Title\n\nJust some intro text.\n"
    path = _write(tmp_path, "no_h2.md", source)
    chunks = chunk_markdown_file(path, repo_root=tmp_path)

    assert len(chunks) == 1
    assert chunks[0].kind == "md_section"
    assert chunks[0].symbol == "no_h2"
    assert chunks[0].start_line == 1


def test_markdown_h2_inside_fenced_code_block_does_not_split(tmp_path: Path) -> None:
    source = """## Real Section

```markdown
## Not a real heading — it's inside a fence
```

Trailing text.
"""
    path = _write(tmp_path, "fenced.md", source)
    chunks = chunk_markdown_file(path, repo_root=tmp_path)

    assert len(chunks) == 1
    assert chunks[0].symbol == "Real Section"


def test_markdown_truncation_uses_paragraph_boundary(tmp_path: Path) -> None:
    paragraphs = ["paragraph " + ("x" * 200) for _ in range(40)]
    source = "## Big\n\n" + "\n\n".join(paragraphs) + "\n"
    path = _write(tmp_path, "big.md", source)
    chunks = chunk_markdown_file(path, repo_root=tmp_path)

    assert len(chunks) == 1
    assert len(chunks[0].content) <= 6500
    assert "[... excerpt truncated ...]" in chunks[0].content


def test_markdown_empty_body_returns_empty(tmp_path: Path) -> None:
    path = _write(tmp_path, "blank.md", "---\ntitle: x\n---\n\n")
    assert chunk_markdown_file(path, repo_root=tmp_path) == []


def test_chunk_path_dispatches_by_suffix(tmp_path: Path) -> None:
    py = _write(tmp_path, "x.py", "def f():\n    return 1\n")
    md = _write(tmp_path, "y.md", "## H\n\nbody\n")
    mdx = _write(tmp_path, "z.mdx", "## H\n\nbody\n")
    other = _write(tmp_path, "ignored.txt", "plain text\n")

    py_chunks = chunk_path(py, repo_root=tmp_path)
    md_chunks = chunk_path(md, repo_root=tmp_path)
    mdx_chunks = chunk_path(mdx, repo_root=tmp_path)
    other_chunks = chunk_path(other, repo_root=tmp_path)

    assert py_chunks and py_chunks[0].kind == "py_func"
    assert md_chunks and md_chunks[0].kind == "md_section"
    assert mdx_chunks and mdx_chunks[0].kind == "md_section"
    assert other_chunks == []


def test_chunk_path_uppercase_suffix(tmp_path: Path) -> None:
    md = _write(tmp_path, "Y.MD", "## H\n\nbody\n")
    chunks = chunk_path(md, repo_root=tmp_path)
    assert chunks and chunks[0].kind == "md_section"


def test_relpath_uses_forward_slashes(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    path = nested / "thing.py"
    path.write_text("def f():\n    return 1\n", encoding="utf-8")

    chunks = chunk_python_file(path, repo_root=tmp_path)
    assert chunks
    assert chunks[0].relpath == "a/b/thing.py"


def test_python_class_with_decorator(tmp_path: Path) -> None:
    source = (
        "from dataclasses import dataclass\n\n\n@dataclass\nclass Point:\n    x: int\n    y: int\n"
    )
    path = _write(tmp_path, "deco_class.py", source)
    chunks = chunk_python_file(path, repo_root=tmp_path)
    cls = next(c for c in chunks if c.kind == "py_class")
    assert cls.symbol == "Point"
    assert cls.start_line == 4  # decorator line, not the class line
    assert cls.end_line == 7


def test_python_nested_def_inside_class_is_not_top_level(tmp_path: Path) -> None:
    source = (
        "class Foo:\n    def bar(self):\n        return 1\n\n    def baz(self):\n        return 2\n"
    )
    path = _write(tmp_path, "nested.py", source)
    chunks = chunk_python_file(path, repo_root=tmp_path)
    # Only Foo should appear; bar/baz are nested and not top-level
    symbols = [c.symbol for c in chunks]
    assert symbols == ["Foo"]
    assert chunks[0].kind == "py_class"


def test_python_file_outside_repo_root_returns_empty(tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("def f():\n    return 1\n", encoding="utf-8")
    other_root = tmp_path / "other_root"
    other_root.mkdir()
    assert chunk_python_file(outside, repo_root=other_root) == []
    assert chunk_path(outside, repo_root=other_root) == []


def test_markdown_file_outside_repo_root_returns_empty(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("## H\n\nbody\n", encoding="utf-8")
    other_root = tmp_path / "other_root"
    other_root.mkdir()
    assert chunk_markdown_file(outside, repo_root=other_root) == []


def test_markdown_with_utf8_bom_is_handled(tmp_path: Path) -> None:
    path = tmp_path / "bom.md"
    # Write a UTF-8 BOM followed by an H2 heading and body
    path.write_bytes(b"\xef\xbb\xbf## Section\n\nbody\n")
    chunks = chunk_markdown_file(path, repo_root=tmp_path)
    assert len(chunks) == 1
    assert chunks[0].symbol == "Section"
    assert chunks[0].content.startswith("## Section")


def test_python_with_utf8_bom_is_handled(tmp_path: Path) -> None:
    path = tmp_path / "bom.py"
    path.write_bytes(b"\xef\xbb\xbfdef hello():\n    return 1\n")
    chunks = chunk_python_file(path, repo_root=tmp_path)
    func = next(c for c in chunks if c.kind == "py_func")
    assert func.symbol == "hello"


def test_markdown_mismatched_fence_types_do_not_swap_state(tmp_path: Path) -> None:
    # Open with ``` then a ~~~ line appears; the ~~~ should NOT close the
    # backtick fence. Then ``` actually closes it.
    source = """## Real Section A

```python
~~~ this is content, not a fence close ~~~
## also content, not a heading
```

## Real Section B

body
"""
    path = _write(tmp_path, "mixed.md", source)
    chunks = chunk_markdown_file(path, repo_root=tmp_path)
    assert [c.symbol for c in chunks] == ["Real Section A", "Real Section B"]


def test_markdown_line_numbers_are_file_relative_not_body_relative(tmp_path: Path) -> None:
    """Locks in the fix for Greptile finding: line numbers must include
    frontmatter lines so consumers can navigate to the original source."""
    source = """---
title: Example
description: test
---

Intro paragraph on file line 6.

## First Section

First body.
"""
    path = _write(tmp_path, "doc.md", source)
    chunks = chunk_markdown_file(path, repo_root=tmp_path)

    assert len(chunks) == 1
    section = chunks[0]
    assert section.symbol == "First Section"
    # File layout: line 1 `---`, 2 `title: Example`, 3 `description: test`,
    # 4 `---`, 5 blank, 6 intro, 7 blank, 8 `## First Section`, 9 blank,
    # 10 `First body.`. The frontmatter regex's trailing `\s*\n` consumes the
    # blank line after the closing `---` too, so fm_line_offset = 5.
    assert section.start_line == 8
    assert section.end_line == 10


def test_markdown_no_h2_with_frontmatter_offsets_line_numbers(tmp_path: Path) -> None:
    source = """---
title: Plain
---

Just body text, no headings.
"""
    path = _write(tmp_path, "plain.md", source)
    chunks = chunk_markdown_file(path, repo_root=tmp_path)

    assert len(chunks) == 1
    # File layout: 1 `---`, 2 `title: Plain`, 3 `---`, 4 blank, 5 body. The
    # frontmatter regex's trailing `\s*\n` also consumes the blank line, so
    # the body's first line ("Just body text...") is file line 5.
    assert chunks[0].start_line == 5
    assert chunks[0].end_line == 5


def test_python_file_with_null_bytes_returns_empty(tmp_path: Path) -> None:
    """ast.parse raises ValueError (not SyntaxError) on null bytes;
    the chunker must catch that too."""
    path = tmp_path / "null.py"
    path.write_bytes(b"def f():\n    return 1\n\x00\n")
    assert chunk_python_file(path, repo_root=tmp_path) == []


def test_python_file_with_undecodable_bytes_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "binary.py"
    # Bytes that are not valid UTF-8
    path.write_bytes(b"\xff\xfe\x00\x01garbage")
    assert chunk_python_file(path, repo_root=tmp_path) == []


def test_markdown_file_with_undecodable_bytes_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "binary.md"
    path.write_bytes(b"\xff\xfe\x00\x01garbage")
    assert chunk_markdown_file(path, repo_root=tmp_path) == []


def test_source_chunk_is_frozen() -> None:
    chunk = SourceChunk(
        relpath="x.py",
        kind="py_func",
        symbol="f",
        start_line=1,
        end_line=2,
        content="def f(): ...",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        chunk.symbol = "g"  # type: ignore[misc]
