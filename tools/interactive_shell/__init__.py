"""Interactive-shell tools.

Import tool submodules explicitly (for example
``tools.interactive_shell.actions.slash`` or ``tools.interactive_shell.catalog``)
rather than relying on this package initializer to eagerly import them.

``contracts`` lives in this package and is imported by
``command_registry.slash_catalog`` during early import wiring. Eagerly importing
the tool submodules here (several of which import back into ``command_registry``)
would reintroduce a circular import during interactive-shell startup.
"""

from __future__ import annotations
