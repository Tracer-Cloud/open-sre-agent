from __future__ import annotations

from pathlib import Path

from tools.system.local_source import (
    list_local_source_tree,
    read_local_source_file,
    search_local_source,
)


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
