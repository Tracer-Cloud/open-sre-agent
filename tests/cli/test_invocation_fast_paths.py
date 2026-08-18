"""Fast CLI argv paths that must answer before process boot."""

from __future__ import annotations

from surfaces.cli.invocation import (
    parse_investigate_print_template_argv,
    try_fast_investigate_print_template,
)


def test_parse_print_template_accepts_equals_and_output(tmp_path) -> None:
    assert parse_investigate_print_template_argv(
        ["investigate", "--print-template", "generic"]
    ) == ("generic", None)
    assert parse_investigate_print_template_argv(
        ["investigate", "--print-template=new_relic", "-o", str(tmp_path / "a.json")]
    ) == ("new_relic", str(tmp_path / "a.json"))


def test_parse_print_template_rejects_other_investigate_flags() -> None:
    assert (
        parse_investigate_print_template_argv(
            ["investigate", "--print-template", "generic", "--evaluate"]
        )
        is None
    )
    assert parse_investigate_print_template_argv(["investigate", "alert.json"]) is None
    assert parse_investigate_print_template_argv(["investigate"]) is None


def test_try_fast_print_template_writes_json(tmp_path) -> None:
    out = tmp_path / "tpl.json"
    code = try_fast_investigate_print_template(
        ["investigate", "--print-template", "generic", "--output", str(out)]
    )
    assert code == 0
    assert '"alert_source": "generic"' in out.read_text(encoding="utf-8")


def test_try_fast_print_template_rejects_unknown_name() -> None:
    assert (
        try_fast_investigate_print_template(["investigate", "--print-template", "not-a-template"])
        == 2
    )
