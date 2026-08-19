"""Client-side encryption for remote sync: objects are sealed before upload.

**Re-exports nothing on purpose.** ``ports`` is imported by the engine, which
every host loads at startup, while its siblings pull in ``cryptography`` and the
OS keyring. Callers import the submodule they need, so an unencrypted process
pays nothing.
"""

from __future__ import annotations
