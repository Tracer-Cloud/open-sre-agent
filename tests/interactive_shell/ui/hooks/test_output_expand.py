"""Ctrl+O pages the last collapsed tool result only while one is stashed."""

from __future__ import annotations

from surfaces.interactive_shell.ui.hooks import install_output_expand_key_bindings
from surfaces.interactive_shell.ui.hooks.output_expand import page_collapsed_output


def _binding_keys(kb) -> set[tuple[str, ...]]:
    return {tuple(str(key) for key in binding.keys) for binding in kb.bindings}


def test_expand_binding_is_ctrl_o() -> None:
    kb = install_output_expand_key_bindings(lambda: True, lambda: "body", lambda _text: None)
    assert ("Keys.ControlO",) in _binding_keys(kb)


def test_ctrl_o_pages_the_stashed_body() -> None:
    paged: list[str] = []
    kb = install_output_expand_key_bindings(lambda: True, lambda: "full output", paged.append)
    handler = next(
        binding.handler
        for binding in kb.bindings
        if tuple(str(k) for k in binding.keys) == ("Keys.ControlO",)
    )

    handler(None)

    assert paged == ["full output"]


def test_binding_is_gated_on_collapsed_output_being_present() -> None:
    kb = install_output_expand_key_bindings(lambda: False, lambda: "body", lambda _text: None)

    assert all(binding.filter() is False for binding in kb.bindings)


def test_page_collapsed_output_prints_when_no_pager(monkeypatch) -> None:
    written: list[str] = []
    monkeypatch.delenv("PAGER", raising=False)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.hooks.output_expand.shutil.which",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.hooks.output_expand.sys.stdout.write",
        written.append,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.hooks.output_expand.sys.stdout.flush", lambda: None
    )

    page_collapsed_output("hello")

    assert written == ["hello\n"]
