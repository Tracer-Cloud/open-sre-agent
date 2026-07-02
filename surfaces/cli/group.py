"""Root Click group: lazy command registration and Rich help rendering.

Kept separate from ``surfaces.cli.__main__`` so the entrypoint stays a thin
wiring module. Command modules are only imported on first access to keep CLI
startup (and especially the "just launch the shell" path) fast.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, TypeVar, overload

import click

_GetDefault = TypeVar("_GetDefault")


class ThemeParamType(click.ParamType):
    """Validate theme names without importing terminal UI dependencies at startup."""

    name = "theme"

    def _choices(self) -> tuple[str, ...]:
        from platform.terminal.theme import list_theme_names

        return list_theme_names()

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str:
        normalized = str(value).strip().lower()
        choices = self._choices()
        if normalized in choices:
            return normalized
        return self.fail(
            f"{value!r} is not one of: {', '.join(choices)}.",
            param,
            ctx,
        )


class LazyCommandsDict(dict[str, click.Command]):
    """Click command mapping that loads the command tree on first read."""

    def __init__(self, owner: LazyRichGroup, initial: Mapping[str, click.Command]) -> None:
        super().__init__(initial)
        self._owner = owner

    def _ensure(self) -> None:
        self._owner.ensure_commands_registered()

    def __contains__(self, key: object) -> bool:
        self._ensure()
        return super().__contains__(key)

    def __iter__(self) -> Iterator[str]:
        self._ensure()
        return super().__iter__()

    def __len__(self) -> int:
        self._ensure()
        return super().__len__()

    def __getitem__(self, key: str) -> click.Command:
        self._ensure()
        return super().__getitem__(key)

    @overload
    def get(self, key: str, default: None = None, /) -> click.Command | None:
        pass

    @overload
    def get(self, key: str, default: click.Command, /) -> click.Command:
        pass

    @overload
    def get(self, key: str, default: _GetDefault, /) -> click.Command | _GetDefault:
        pass

    def get(self, key: str, default: object = None, /) -> object:
        self._ensure()
        return super().get(key, default)

    def keys(self) -> Any:
        self._ensure()
        return super().keys()

    def values(self) -> Any:
        self._ensure()
        return super().values()

    def items(self) -> Any:
        self._ensure()
        return super().items()


class LazyRichGroup(click.Group):
    """Root CLI group with lazy command registration and Rich help rendering."""

    _commands_registered: bool

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._commands_registered = False
        self.commands = LazyCommandsDict(self, self.commands)

    def ensure_commands_registered(self) -> None:
        if self._commands_registered:
            return
        self._commands_registered = True
        from surfaces.cli.commands import register_commands

        register_commands(self)

    def list_commands(self, ctx: click.Context) -> list[str]:
        self.ensure_commands_registered()
        return super().list_commands(ctx)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        self.ensure_commands_registered()
        return super().get_command(ctx, cmd_name)

    def format_help(self, ctx: click.Context, _formatter: click.HelpFormatter) -> None:
        assert isinstance(ctx.command, click.Group)
        from surfaces.interactive_shell.ui.layout import render_help

        render_help(ctx.command)
