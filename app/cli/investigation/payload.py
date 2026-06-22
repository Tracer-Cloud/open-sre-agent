"""Helpers for loading alert payloads from various input sources."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from app.cli.interactive_shell.data_store.constants import SAMPLE_ALERT_OPTIONS

_DEMO_ALERT_FILENAME = "alert.json"


def bundled_demo_alert_path() -> Path | None:
    """Return the packaged demo alert used by ``opensre investigate -i alert.json``."""
    candidate = Path(__file__).resolve().parents[1] / "fixtures" / _DEMO_ALERT_FILENAME
    if candidate.is_file():
        return candidate
    return None


def resolve_alert_path(path_str: str) -> Path:
    """Resolve an alert path, using the bundled demo when ``alert.json`` is missing locally."""
    path = Path(path_str)
    if path.is_file():
        return path
    if path.name == _DEMO_ALERT_FILENAME and not path.is_absolute() and path.parent == Path("."):
        bundled = bundled_demo_alert_path()
        if bundled is not None:
            return bundled
    return path


def parse_payload_text(raw_text: str, source_label: str) -> dict[str, Any]:
    """Parse and validate a JSON object payload."""
    try:
        data: Any = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Invalid alert JSON from {source_label}: {exc.msg} at line {exc.lineno}, column {exc.colno}."
        ) from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Alert payload from {source_label} must be a JSON object.")
    if not data:
        raise SystemExit(f"Alert payload from {source_label} must be a non-empty JSON object.")
    return data


def load_file(path_str: str) -> dict[str, Any]:
    """Load an alert payload from any text file.

    - ``.json`` — parsed directly as JSON.
    - ``.md`` / ``.txt`` / other — first ```json``` block is extracted and parsed;
      if none is found, raw content is passed as ``{"raw_text": ...}`` for the agent.
    """
    path = resolve_alert_path(path_str)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"Alert file not found: {path_str}") from exc
    except UnicodeDecodeError as exc:
        raise SystemExit(f"Alert file must be UTF-8 text: {path_str}") from exc
    except OSError as exc:
        raise SystemExit(f"Could not read alert file {path_str}: {exc}") from exc

    if path.suffix.lower() == ".json":
        return parse_payload_text(raw_text, path_str)

    # For .md, .txt, and everything else: try to pull a fenced JSON block
    match = re.search(r"```json\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if match:
        return parse_payload_text(match.group(1), path_str)

    # No structured JSON — let the agent interpret the raw content
    return {"raw_text": raw_text}


def load_stdin() -> dict[str, Any]:
    """Read a JSON payload from stdin."""
    if sys.stdin.isatty():
        raise SystemExit(
            "No alert input provided on stdin. Use --interactive, --input <file>, or --input-json."
        )
    return parse_payload_text(sys.stdin.read(), "stdin")


def load_interactive() -> dict[str, Any]:
    """Prompt the user to paste an alert payload."""
    if not sys.stdin.isatty():
        print("Paste the alert JSON payload, then press Ctrl-D when finished.", file=sys.stderr)
        raw_text = sys.stdin.read()
    else:
        print(
            "Paste the alert JSON payload. It auto-submits once valid JSON is complete.",
            file=sys.stderr,
        )
        lines: list[str] = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            except KeyboardInterrupt as exc:
                raise SystemExit(0) from exc
            if not line.strip():
                break
            lines.append(line)
            candidate = "\n".join(lines)
            try:
                parsed_candidate: Any = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed_candidate, dict):
                raise SystemExit("Alert payload from interactive input must be a JSON object.")
            if not parsed_candidate:
                raise SystemExit(
                    "Alert payload from interactive input must be a non-empty JSON object."
                )
            return parsed_candidate
        raw_text = "\n".join(lines)
    if not raw_text.strip():
        raise SystemExit("No alert JSON was provided in interactive mode.")
    return parse_payload_text(raw_text, "interactive input")


def _render_guided_menu() -> list[tuple[int, str]]:
    """Render the bare investigate guided menu and return option mapping."""
    options: list[tuple[int, str]] = [(1, f"demo:{_DEMO_ALERT_FILENAME}")]
    print("No alert input provided. Choose an investigation input source:", file=sys.stderr)
    print(f"  1) {_DEMO_ALERT_FILENAME} (bundled demo alert file)", file=sys.stderr)

    next_index = 2
    for template_name, label in SAMPLE_ALERT_OPTIONS:
        options.append((next_index, f"template:{template_name}"))
        print(f"  {next_index}) {label}", file=sys.stderr)
        next_index += 1

    options.append((next_index, "custom_file"))
    print(f"  {next_index}) Custom file path", file=sys.stderr)
    next_index += 1

    options.append((next_index, "paste_json"))
    print(f"  {next_index}) Paste JSON now", file=sys.stderr)
    next_index += 1

    options.append((next_index, "cancel"))
    print(f"  {next_index}) Cancel", file=sys.stderr)
    return options


def _guided_menu_choices() -> list[tuple[str, str]]:
    """Return guided menu targets and labels for inline picker UIs."""
    choices: list[tuple[str, str]] = [
        (f"demo:{_DEMO_ALERT_FILENAME}", f"{_DEMO_ALERT_FILENAME} (bundled demo alert file)"),
    ]
    for template_name, label in SAMPLE_ALERT_OPTIONS:
        choices.append((f"template:{template_name}", label))
    choices.extend(
        [
            ("custom_file", "Custom file path"),
            ("paste_json", "Paste JSON now"),
            ("cancel", "Cancel"),
        ]
    )
    return choices


def _supports_inline_picker() -> bool:
    """Return whether we can safely render an inline terminal picker."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _choose_guided_target() -> str:
    """Choose a guided menu target using inline picker when available."""
    choices = _guided_menu_choices()
    if _supports_inline_picker():
        import questionary

        selected = questionary.select(
            "Choose an investigation input source:",
            choices=[questionary.Choice(label, value=target) for target, label in choices],
        ).ask()
        if selected is None:
            raise SystemExit(0)
        return str(selected)

    while True:
        options = _render_guided_menu()
        valid_choices = {str(index): target for index, target in options}
        try:
            choice = input("Select an option: ").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise SystemExit(0) from exc
        target = valid_choices.get(choice)
        if target is None:
            print("Invalid selection. Enter one of the menu numbers.", file=sys.stderr)
            continue
        return target


def _choose_guided_payload() -> dict[str, Any]:
    from app.cli.investigation.alert_templates import build_alert_template

    while True:
        target = _choose_guided_target()
        if target.startswith("demo:"):
            return load_file(target.split(":", maxsplit=1)[1])
        if target.startswith("template:"):
            return build_alert_template(target.split(":", maxsplit=1)[1])
        if target == "custom_file":
            try:
                custom_path = input("Alert file path: ").strip()
            except (EOFError, KeyboardInterrupt) as exc:
                raise SystemExit(0) from exc
            if not custom_path:
                print("Alert file path cannot be empty.", file=sys.stderr)
                continue
            return load_file(custom_path)
        if target == "paste_json":
            return load_interactive()
        if target == "cancel":
            raise SystemExit(0)
        raise SystemExit("No alert input selected.")


def load_payload(
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
) -> dict[str, Any]:
    """Dispatch to the right loader based on what the user passed."""
    if input_json:
        return parse_payload_text(input_json, "--input-json")
    if interactive:
        return load_interactive()
    if input_path == "-":
        return load_stdin()
    if input_path:
        return load_file(input_path)
    if sys.stdin.isatty():
        return _choose_guided_payload()
    return load_stdin()
