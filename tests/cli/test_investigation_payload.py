from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from cli.interactive_shell.data_store.constants import SAMPLE_ALERT_OPTIONS
from cli.investigation.payload import load_payload


def test_load_payload_tty_guided_menu_template_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cli.investigation.payload.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cli.investigation.payload.sys.stdout.isatty", lambda: False)
    answers = iter(["2"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    payload = load_payload(input_path=None, input_json=None, interactive=False)

    assert payload["alert_source"] == "generic"
    assert payload["alert_name"]


def test_load_payload_tty_guided_menu_custom_file_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cli.investigation.payload.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cli.investigation.payload.sys.stdout.isatty", lambda: False)
    custom_file_index = str(1 + len(SAMPLE_ALERT_OPTIONS) + 1)
    answers = iter([custom_file_index, "alerts/custom.json"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(
        "cli.investigation.payload.load_file",
        lambda path: {"loaded_from": path},
    )

    payload = load_payload(input_path=None, input_json=None, interactive=False)

    assert payload == {"loaded_from": "alerts/custom.json"}


def test_load_payload_without_tty_uses_stdin_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cli.investigation.payload.sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "cli.investigation.payload.load_stdin",
        lambda: {"alert_name": "from-stdin"},
    )

    payload = load_payload(input_path=None, input_json=None, interactive=False)

    assert payload == {"alert_name": "from-stdin"}


def test_load_payload_tty_guided_menu_cancel_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cli.investigation.payload.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cli.investigation.payload.sys.stdout.isatty", lambda: False)
    cancel_index = str(1 + len(SAMPLE_ALERT_OPTIONS) + 3)
    answers = iter([cancel_index])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    with pytest.raises(SystemExit) as exc_info:
        load_payload(input_path=None, input_json=None, interactive=False)

    assert exc_info.value.code == 0


def test_load_payload_tty_guided_menu_inline_picker_template_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cli.investigation.payload.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cli.investigation.payload.sys.stdout.isatty", lambda: True)

    class _FakeQuestionary:
        class Choice:
            def __init__(self, _label: str, value: str) -> None:
                self.value = value

        @staticmethod
        def select(_prompt: str, *, choices: list[object]) -> SimpleNamespace:
            assert choices
            return SimpleNamespace(ask=lambda: "template:generic")

    monkeypatch.setitem(sys.modules, "questionary", _FakeQuestionary())

    payload = load_payload(input_path=None, input_json=None, interactive=False)

    assert payload["alert_source"] == "generic"


def test_load_payload_input_json_empty_object_raises() -> None:
    with pytest.raises(SystemExit, match="non-empty JSON object"):
        load_payload(input_path=None, input_json="{}", interactive=False)


def test_load_payload_interactive_tty_empty_payload_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cli.investigation.payload.sys.stdin.isatty", lambda: True)
    answers = iter([""])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    with pytest.raises(SystemExit, match="No alert JSON was provided in interactive mode"):
        load_payload(input_path=None, input_json=None, interactive=True)


def test_load_payload_interactive_tty_multiline_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cli.investigation.payload.sys.stdin.isatty", lambda: True)
    answers = iter(['{"alert_name":"x",', '"severity":"critical"}'])

    def _fake_input(_prompt: str = "") -> str:
        try:
            return next(answers)
        except StopIteration as exc:  # pragma: no cover - defensive guard
            raise AssertionError("interactive parser requested an unexpected extra line") from exc

    monkeypatch.setattr("builtins.input", _fake_input)

    payload = load_payload(input_path=None, input_json=None, interactive=True)

    assert payload == {"alert_name": "x", "severity": "critical"}


def test_load_payload_interactive_tty_empty_object_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cli.investigation.payload.sys.stdin.isatty", lambda: True)
    answers = iter(["{}"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    with pytest.raises(SystemExit, match="non-empty JSON object"):
        load_payload(input_path=None, input_json=None, interactive=True)
