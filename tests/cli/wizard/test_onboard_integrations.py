"""Tests for onboard integration picker taxonomy."""

from __future__ import annotations

import questionary

from app.cli.wizard._ui import _group_header_label, _grouped_questionary_choices
from app.cli.wizard.onboard_integrations import (
    ONBOARD_INTEGRATION_CHOICES,
    ONBOARD_INTEGRATION_GROUP_ORDER,
    ONBOARD_SKIP_CHOICE,
)
from app.cli.wizard.prompts import _SelectControl


def test_group_header_label_formats_category_title() -> None:
    assert _group_header_label("Observability") == "── Observability ──"


def test_select_control_renders_group_headers_with_highlight_style() -> None:
    ic = _SelectControl(
        [
            questionary.Separator(_group_header_label("Observability")),
            questionary.Choice("Datadog", value="datadog"),
        ],
        None,
        pointer="❯",
        initial_choice="datadog",
        show_description=False,
    )

    rendered = "".join(text for _style, text in ic._get_choice_tokens())
    assert "── Observability ──" in rendered
    assert any(style == "class:group-header" for style, _text in ic._get_choice_tokens())


def test_onboard_integration_choices_have_unique_values_and_valid_groups() -> None:
    values = [choice.value for choice in ONBOARD_INTEGRATION_CHOICES]
    assert len(values) == len(set(values))
    assert ONBOARD_SKIP_CHOICE.value not in values

    for choice in ONBOARD_INTEGRATION_CHOICES:
        assert choice.group in ONBOARD_INTEGRATION_GROUP_ORDER


def test_grouped_questionary_choices_renders_category_separators() -> None:
    rendered = _grouped_questionary_choices(
        list(ONBOARD_INTEGRATION_CHOICES),
        group_order=ONBOARD_INTEGRATION_GROUP_ORDER,
        trailing_choices=[ONBOARD_SKIP_CHOICE],
    )

    separator_titles = [item.title for item in rendered if isinstance(item, questionary.Separator)]
    assert separator_titles[: len(ONBOARD_INTEGRATION_GROUP_ORDER)] == [
        _group_header_label(group) for group in ONBOARD_INTEGRATION_GROUP_ORDER
    ]
    assert len(separator_titles) == len(ONBOARD_INTEGRATION_GROUP_ORDER) + 1

    selectable_values = [
        item.value
        for item in rendered
        if isinstance(item, questionary.Choice) and not isinstance(item, questionary.Separator)
    ]
    assert selectable_values == [
        *[choice.value for choice in ONBOARD_INTEGRATION_CHOICES],
        ONBOARD_SKIP_CHOICE.value,
    ]
