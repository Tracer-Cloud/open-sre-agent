"""Client-side encryption for remote sync.

Objects are sealed on the laptop, so the store holds no readable history and the
party operating it cannot read incident conversations or memory. Server-side
encryption, which the backends already request, defends the provider's disks;
this defends against the provider.

Layering: :mod:`platform.filestorage.engine` depends only on
:class:`~platform.filestorage.encryption.ports.Cipher`, never on ``cryptography`` or
on key material, so the transfer logic stays testable with a stand-in and key
handling stays in one package.

**This package deliberately re-exports nothing.** ``ports`` is imported by the
engine, which every host loads at startup, while the modules beside it pull in
``cryptography`` and the OS keyring. Eager re-exports here would put that cost
on every process, encrypted or not, so callers import the submodule they need:

    from platform.filestorage.encryption.ports import Cipher          # free
    from platform.filestorage.encryption.resolver import resolve_cipher

What encryption does **not** hide: object keys (session ids and memory
filenames), object sizes, modification times, and how often a machine syncs. The
mirrored roots and their filenames are structural — the engine maps an object
back onto a local path by its key.
"""

from __future__ import annotations
