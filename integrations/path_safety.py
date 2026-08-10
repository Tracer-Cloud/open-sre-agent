"""One guard for caller-supplied values interpolated into HTTP request paths.

Vendor clients build request paths with f-strings and the resource ids come
from tool arguments, which means an LLM picks them -- and alert titles,
incident summaries and monitor payloads that the model reads first come from
upstream systems and third parties. An id is therefore attacker-influenced
input, not a trusted token.

Two things must not happen. The value must not add path structure: httpx
resolves ``..`` before the request leaves the process, so ``../../v1/users``
in an id reaches a sibling endpoint on the same host. And the value must not
turn a relative path into an absolute URL: ``httpx.Client`` prepends
``base_url`` only to a *relative* URL, while the client-level ``Authorization``
header goes out either way, so a path that parses as ``https://elsewhere/...``
mails the integration's token to whoever owns that host.

No call site can currently reach the second case -- every one of them puts a
literal prefix in front of the interpolation, which keeps the whole string
relative. That is one edit away from being untrue. This guard therefore
rejects before it encodes, so the property survives a refactor that moves the
value to the front of the path. Precedent for the same shape:
``integrations/vercel/client.py``.

``quote(..., safe="")`` is a no-op on every character the pattern admits, so
an accepted id is returned byte-identical and no vendor's routing changes.
"""

from __future__ import annotations

import re
from urllib.parse import quote

MAX_PATH_SEGMENT_LENGTH = 256

# Opaque vendor resource ids: UUIDs, numeric ids, and prefixed tokens
# (``dpl_abc``, ``PIJ1234``, ``PROJ-123``). Every character that could add
# URL structure -- ``/`` ``\`` ``:`` ``%`` ``?`` ``#`` ``@`` and whitespace --
# is excluded, and the leading-alphanumeric anchor makes a bare ``..`` or
# ``.`` segment impossible.
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def safe_path_segment(
    raw: str | None,
    *,
    max_length: int = MAX_PATH_SEGMENT_LENGTH,
) -> str | None:
    """Return *raw* as one URL path segment, or ``None`` when it is not one.

    ``None`` means "do not make the request". Callers return their own
    failure shape rather than raising: a model guessing a malformed id must
    get a tool result, not an exception, and must not file telemetry.
    """
    cleaned = (raw or "").strip()
    if not cleaned or len(cleaned) > max_length:
        return None
    if not _OPAQUE_ID_RE.fullmatch(cleaned):
        return None
    return quote(cleaned, safe="")
