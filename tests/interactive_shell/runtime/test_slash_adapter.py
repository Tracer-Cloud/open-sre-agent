from surfaces.interactive_shell.runtime.slash_adapter import headless_slash_ports


def test_headless_slash_messages_do_not_contain_rich_markup() -> None:
    ports = headless_slash_ports()

    messages = [
        ports.launching_message("/health"),
        ports.format_turn_outcome("/health", ok=True),
        ports.format_turn_outcome("/health", ok=False),
    ]

    for message in messages:
        assert "[" not in message
        assert "]" not in message
