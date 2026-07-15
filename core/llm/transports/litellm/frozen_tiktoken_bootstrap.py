"""Bootstrap tiktoken plugin discovery for PyInstaller-frozen builds.

``litellm`` imports ``tiktoken`` at module load time and immediately calls
``tiktoken.get_encoding("cl100k_base")``. ``tiktoken`` finds that encoding by
walking the ``tiktoken_ext`` namespace package with ``pkgutil.iter_modules``
and importing whatever submodules it finds there. That directory walk only
sees loose files on disk, so it comes back empty inside a frozen binary even
when ``tiktoken_ext.openai_public`` (tiktoken's one shipped plugin) is bundled
into the archive as a hidden import — the frozen ``from litellm import
completion`` then raises ``ValueError: Unknown encoding cl100k_base`` with
``Plugins found: []`` (see issue #3631). Importing the plugin module directly
sidesteps the broken directory walk while still going through tiktoken's own
registration path.
"""

from __future__ import annotations

import sys


def ensure_tiktoken_encodings_discoverable() -> None:
    """Make tiktoken find its bundled plugin when running as a frozen binary."""
    if not getattr(sys, "frozen", False):
        return

    import tiktoken.registry as registry

    def _direct_plugin_modules() -> tuple[str, ...]:
        return ("tiktoken_ext.openai_public",)

    registry._available_plugin_modules = _direct_plugin_modules  # type: ignore[assignment]
