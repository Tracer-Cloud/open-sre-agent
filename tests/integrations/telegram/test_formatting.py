"""Tests for Markdown -> Telegram HTML conversion."""

from __future__ import annotations

from integrations.telegram.formatting import markdown_to_telegram_html


def test_bold_becomes_b_tag() -> None:
    assert (
        markdown_to_telegram_html("- **CLI / shell** questions") == "- <b>CLI / shell</b> questions"
    )


def test_double_underscore_bold() -> None:
    assert markdown_to_telegram_html("__strong__") == "<b>strong</b>"


def test_inline_code_becomes_code_tag() -> None:
    assert markdown_to_telegram_html("you have `github` connected") == (
        "you have <code>github</code> connected"
    )


def test_italic_star_and_underscore() -> None:
    assert markdown_to_telegram_html("*em* and _also_") == "<i>em</i> and <i>also</i>"


def test_link_becomes_anchor() -> None:
    assert markdown_to_telegram_html("[docs](https://x.com/a)") == (
        '<a href="https://x.com/a">docs</a>'
    )


def test_html_special_chars_are_escaped() -> None:
    assert markdown_to_telegram_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_code_content_is_escaped_and_not_reformatted() -> None:
    # ** and < inside code must stay literal (escaped), not become tags.
    assert markdown_to_telegram_html("`x = a ** b < c`") == "<code>x = a ** b &lt; c</code>"


def test_fenced_code_block_becomes_pre() -> None:
    out = markdown_to_telegram_html("```py\nx = 1 < 2\n```")
    assert out == "<pre>x = 1 &lt; 2\n</pre>"


def test_unbalanced_markdown_left_as_text() -> None:
    # A dangling ** must not produce an unclosed tag (which Telegram would reject).
    assert markdown_to_telegram_html("**oops no close") == "**oops no close"


def test_plain_text_passthrough() -> None:
    assert markdown_to_telegram_html("just plain words") == "just plain words"
