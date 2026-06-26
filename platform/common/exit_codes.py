"""Standard process exit codes for opensre.

Follows the convention from clig.dev and POSIX:
  0 - success
  1 - runtime / general error (retrying may help)
  2 - usage error (user invoked the command incorrectly)

Lives in ``platform/common`` so any layer (CLI, tools, integrations) can
share the same exit-code contract without importing the CLI package.
"""

from __future__ import annotations

SUCCESS: int = 0
ERROR: int = 1
USAGE_ERROR: int = 2
