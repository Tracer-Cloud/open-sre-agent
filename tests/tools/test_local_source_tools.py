from __future__ import annotations

from pathlib import Path

from tools.system.local_source import (
    list_local_source_tree,
    read_local_source_file,
    search_local_source,
)
from tools.system.local_source.repository import MAX_READ_BYTES, READ_CHUNK_CHARS


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src" / "checkout").mkdir(parents=True)
    (root / "src" / "checkout" / "main.go").write_text(
        "package checkout\n\nfunc chargeCard() error {\n    return payment.Charge()\n}\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("OpenTelemetry Demo\n", encoding="utf-8")
    return root


def test_local_source_tools_are_available_only_for_a_real_scoped_root(
    tmp_path: Path,
) -> None:
    root = _source(tmp_path)
    registered = list_local_source_tree.__opensre_registered_tool__

    assert registered.is_available(
        {"local_source": {"connection_verified": True, "root_path": str(root)}}
    )
    assert not registered.is_available(
        {"local_source": {"connection_verified": True, "root_path": str(root / "missing")}}
    )


def test_list_search_and_read_return_only_relative_bounded_source_evidence(
    tmp_path: Path,
) -> None:
    root = _source(tmp_path)

    tree = list_local_source_tree(path="src", depth=3, limit=20, root_path=str(root))
    search = search_local_source(
        query="payment.Charge", path="src", file_glob="*.go", limit=10, root_path=str(root)
    )
    read = read_local_source_file(
        path="src/checkout/main.go", start_line=3, end_line=4, root_path=str(root)
    )

    assert tree["available"] is True
    assert tree["entries"] == [
        {"path": "src/checkout", "type": "directory"},
        {"path": "src/checkout/main.go", "type": "file"},
    ]
    assert search["matches"] == [
        {
            "path": "src/checkout/main.go",
            "line": 4,
            "text": "    return payment.Charge()",
        }
    ]
    assert read["content"] == "func chargeCard() error {\n    return payment.Charge()"
    assert read["start_line"] == 3
    assert read["end_line"] == 4
    assert read["truncated"] is False
    assert read["truncated_by"] is None


def test_local_source_rejects_parent_and_symlink_escape(tmp_path: Path) -> None:
    root = _source(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (root / "escape.txt").symlink_to(outside)

    parent = read_local_source_file(path="../outside.txt", root_path=str(root))
    symlink = read_local_source_file(path="escape.txt", root_path=str(root))

    assert parent["available"] is False
    assert parent["error"] == "path_outside_source_root"
    assert symlink["available"] is False
    assert symlink["error"] == "path_outside_source_root"


def test_local_source_read_reports_truncation_at_native_boundary(tmp_path: Path) -> None:
    root = _source(tmp_path)
    path = root / "many.txt"
    path.write_text("\n".join(f"line {line}" for line in range(1, 406)), encoding="utf-8")

    result = read_local_source_file(path="many.txt", start_line=1, root_path=str(root))

    assert result["start_line"] == 1
    assert result["end_line"] == 400
    assert result["truncated"] is True
    assert result["truncated_by"] == "line_limit"


def test_local_source_read_does_not_report_line_limit_at_exact_boundary(
    tmp_path: Path,
) -> None:
    root = _source(tmp_path)
    path = root / "exact.txt"
    path.write_text("\n".join(f"line {line}" for line in range(1, 401)), encoding="utf-8")

    result = read_local_source_file(path="exact.txt", start_line=1, root_path=str(root))

    assert result["end_line"] == 400
    assert result["truncated"] is False
    assert result["truncated_by"] is None


def test_local_source_read_streams_without_materializing_entire_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _source(tmp_path)
    path = root / "large.txt"
    path.write_text("\n".join(f"line {line}" for line in range(1, 1000)), encoding="utf-8")

    def fail_read_text(*_args, **_kwargs):
        raise AssertionError("bounded source reads must stream instead of read_text")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    result = read_local_source_file(
        path="large.txt",
        start_line=10,
        end_line=12,
        root_path=str(root),
    )

    assert result["content"] == "line 10\nline 11\nline 12"
    assert result["truncated"] is False
    assert result["truncated_by"] is None


def test_local_source_read_reports_byte_limit_for_huge_line(tmp_path: Path) -> None:
    root = _source(tmp_path)
    path = root / "huge.txt"
    path.write_text("x" * (MAX_READ_BYTES + 100), encoding="utf-8")

    result = read_local_source_file(path="huge.txt", start_line=1, root_path=str(root))

    assert result["start_line"] == 1
    assert result["end_line"] == 1
    assert len(result["content"].encode("utf-8")) == MAX_READ_BYTES
    assert result["truncated"] is True
    assert result["truncated_by"] == "byte_limit"


def test_local_source_read_uses_bounded_readline_chunks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _source(tmp_path)
    target = root / "huge.txt"
    target.write_text("x" * (MAX_READ_BYTES + 100), encoding="utf-8")
    real_open = Path.open
    read_sizes: list[int] = []

    class GuardedReader:
        def __init__(self, source):
            self._source = source

        def __enter__(self):
            self._source.__enter__()
            return self

        def __exit__(self, *args):
            return self._source.__exit__(*args)

        def __iter__(self):
            raise AssertionError("bounded source reads must not iterate full lines")

        def readline(self, size: int = -1) -> str:
            assert 0 < size <= READ_CHUNK_CHARS
            read_sizes.append(size)
            return self._source.readline(size)

    def guarded_open(self: Path, *args, **kwargs):
        opened = real_open(self, *args, **kwargs)
        if self == target:
            return GuardedReader(opened)
        return opened

    monkeypatch.setattr(Path, "open", guarded_open)

    result = read_local_source_file(path="huge.txt", root_path=str(root))

    assert read_sizes
    assert len(result["content"].encode("utf-8")) == MAX_READ_BYTES
    assert result["truncated_by"] == "byte_limit"


def test_local_source_read_discards_huge_skipped_lines_without_byte_truncation(
    tmp_path: Path,
) -> None:
    root = _source(tmp_path)
    path = root / "huge-prefix.txt"
    path.write_text(
        "x" * (MAX_READ_BYTES + 100) + "\nselected\n",
        encoding="utf-8",
    )

    result = read_local_source_file(
        path="huge-prefix.txt",
        start_line=2,
        end_line=2,
        root_path=str(root),
    )

    assert result["content"] == "selected"
    assert result["start_line"] == 2
    assert result["end_line"] == 2
    assert result["truncated"] is False
    assert result["truncated_by"] is None


def test_local_source_read_strips_crlf_split_across_bounded_chunks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _source(tmp_path)
    path = root / "crlf.txt"
    path.write_text("abc\r\nnext\r\n", encoding="utf-8")
    monkeypatch.setattr(
        "tools.system.local_source.repository.READ_CHUNK_CHARS",
        4,
    )

    result = read_local_source_file(
        path="crlf.txt",
        start_line=1,
        end_line=2,
        root_path=str(root),
    )

    assert result["content"] == "abc\nnext"
