"""Pin the bench Dockerfile's COPY sources to paths Docker can actually resolve.

``Dockerfile.bench`` copies hand-listed source trees ("only what the bench
needs") rather than the whole context, so a package rename elsewhere in the
repo silently leaves a dangling ``COPY``. Nothing in PR CI builds this image;
the failure only surfaces post-merge in the benchmark-image workflow, which
turns ``main`` red. Precedent: the ``platform`` -> ``infrastructure`` rename
(#5295) left ``COPY platform/`` behind.

Two things decide whether a ``COPY`` resolves, and checking either alone gives
a false pass:

* The **tracked tree**, not the working copy. A rename leaves untracked
  ``__pycache__`` under the old name, so an ``exists()`` check on disk still
  passes locally while the build fails in CI.
* The **dockerignore**, because a tracked path excluded from the context is
  just as absent to Docker as one that was deleted.

So this reconstructs the context the same way BuildKit does and asserts every
COPY source resolves to something inside it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DOCKERFILE = _REPO_ROOT / "tests" / "benchmarks" / "cloudopsbench" / "infra" / "Dockerfile.bench"
# BuildKit prefers a per-Dockerfile ignore file over the one at the context root.
_DOCKERIGNORE = _DOCKERFILE.with_name(f"{_DOCKERFILE.name}.dockerignore")


def _ancestors(path: str) -> list[str]:
    """Return ``path`` followed by each of its parent directories."""
    parts = path.split("/")
    return ["/".join(parts[:index]) for index in range(len(parts), 0, -1)]


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """Compile one dockerignore pattern to a regex matching a whole path."""
    if pattern.startswith("**/"):
        # ``**/x`` matches ``x`` at any depth, including the context root.
        prefix, pattern = "(?:.*/)?", pattern[3:]
    else:
        prefix = ""

    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile(prefix + "".join(out))


def _ignore_rules() -> tuple[tuple[re.Pattern[str], bool], ...]:
    """Return ``(regex, negated)`` per dockerignore line, in file order."""
    if not _DOCKERIGNORE.is_file():
        return ()

    rules: list[tuple[re.Pattern[str], bool]] = []
    for raw in _DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line[1:].strip() if negated else line
        pattern = pattern.strip("/")
        if pattern:
            rules.append((_pattern_regex(pattern), negated))
    return tuple(rules)


def _tracked_files() -> list[str]:
    """Return every file path git tracks, relative to the repo root."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("git ls-files unavailable")
    return [entry for entry in result.stdout.split("\0") if entry]


def _context_paths() -> frozenset[str]:
    """Return every path Docker can see: surviving files plus their directories.

    A pattern that matches a directory excludes everything beneath it, so each
    file is tested against its own path and every ancestor. Later rules win,
    which is what lets ``!tests/benchmarks`` re-admit a subtree that an earlier
    ``tests/*`` dropped.
    """
    rules = _ignore_rules()
    paths: set[str] = set()
    for file_path in _tracked_files():
        candidates = _ancestors(file_path)
        included = True
        for regex, negated in rules:
            if any(regex.fullmatch(candidate) for candidate in candidates):
                included = negated
        if included:
            paths.update(candidates)
    return frozenset(paths)


def _context_relative_sources() -> list[tuple[int, str]]:
    """Return ``(lineno, source)`` for COPY sources read from the build context.

    ``--from=`` copies read from another stage or image, not the build context,
    so they are skipped. The final token of a COPY is the destination.
    """
    sources: list[tuple[int, str]] = []
    for lineno, raw in enumerate(_DOCKERFILE.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line.startswith("COPY "):
            continue
        tokens = line.split()[1:]
        if any(token.startswith("--from=") for token in tokens):
            continue
        operands = [token for token in tokens if not token.startswith("--")]
        sources.extend((lineno, source) for source in operands[:-1])
    return sources


def test_bench_dockerfile_copies_only_paths_in_the_build_context() -> None:
    # The benchmark-image workflow builds with ``context: .`` (the repo root).
    context = _context_paths()
    missing = [
        f"{_DOCKERFILE.name}:{lineno}: COPY {source} -> untracked, or excluded by "
        f"{_DOCKERIGNORE.name}"
        for lineno, source in _context_relative_sources()
        if source.rstrip("/") not in context
    ]

    assert missing == [], (
        "Dockerfile.bench copies paths Docker cannot resolve. The bench image "
        "build fails post-merge on main, not in PR CI:\n" + "\n".join(missing)
    )
