"""Documentation-grounding helpers for OpenSRE interactive-shell answers.

The interactive shell is documentation-aware: when a user asks a procedural
question (e.g. "how do I configure Datadog?", "how do I deploy this?"), we
retrieve the most relevant pages from the project ``docs/`` directory and
include their content in the LLM grounding context so answers reflect the
current docs instead of model memory.

Source of truth
---------------
The local ``docs/`` directory at the repository root (the same Mintlify
content published to ``https://www.opensre.com/docs``). It contains MDX
pages such as ``datadog.mdx``, ``deployment.mdx``, ``quickstart.mdx``,
plus subdirectories like ``tutorials/`` and ``use-cases/``.

How docs stay fresh
-------------------
Pages are parsed lazily on first use and memoized for the lifetime of the
process via an :func:`functools.lru_cache` on :func:`_discover_docs_cached`.
That means there is no build step and no on-disk cache file: a fresh
``opensre`` invocation always reads the current ``docs/`` tree. Edits made
to ``docs/*.mdx`` while a long-running shell is open are NOT picked up
until the next process restart. To extend coverage, drop a new ``.mdx``
file under ``docs/`` and it will be discovered automatically the next time
the shell starts.

When docs are missing
---------------------
For non-editable installs that do not ship the ``docs/`` directory the
discovery returns an empty list and :func:`build_docs_reference_text`
returns an empty string. Callers must tell the LLM to fall back to the
CLI reference and avoid inventing setup steps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Docs live at the repository root, three levels above this file
# (.../app/cli/interactive_shell/docs_reference.py -> repo root).
_DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs"

# Extensions we read for grounding. Mintlify content is .mdx; .md is included
# for any plain-Markdown page the project may add later.
_DOC_EXTENSIONS = (".mdx", ".md")

# Folders inside docs/ that are not user-facing prose (fonts, images, build
# assets) and would only add noise to the retrieval index.
_SKIP_DIRS = frozenset(
    {
        "assets",
        "images",
        "logo",
        "public",
        "styles",
        "snippets",
    }
)

# Cap per-document excerpt and total reference size so the prompt stays
# well within the LLM context window even when several pages match.
_MAX_PER_DOC_CHARS = 4_000
_DEFAULT_MAX_TOTAL_CHARS = 22_000
_DEFAULT_TOP_N = 4

# Stopwords stripped from a user's query before scoring. Without this,
# common verbs and articles ("how", "do", "the") would dominate the match.
_QUERY_STOPWORDS = frozenset(
    {
        "how",
        "do",
        "i",
        "we",
        "to",
        "the",
        "a",
        "an",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "of",
        "in",
        "on",
        "for",
        "with",
        "without",
        "from",
        "by",
        "use",
        "using",
        "used",
        "make",
        "set",
        "setup",
        "up",
        "can",
        "could",
        "would",
        "should",
        "will",
        "shall",
        "may",
        "might",
        "what",
        "which",
        "where",
        "when",
        "why",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "my",
        "me",
        "you",
        "your",
        "our",
        "us",
        "they",
        "them",
        "please",
        "thanks",
        "thank",
        "help",
        "tell",
        "show",
        "opensre",
        "tracer",
    }
)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TITLE_RE = re.compile(r"^title\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class DocPage:
    """A single Markdown / MDX page available for grounding."""

    slug: str
    """Filename without extension (e.g. ``"datadog"``)."""

    relpath: str
    """Path relative to the docs root, with forward slashes (e.g. ``"datadog.mdx"``)."""

    title: str
    """Display title from frontmatter ``title:`` or first H1, falling back to slug."""

    body: str
    """File body with the YAML frontmatter stripped."""


def _strip_frontmatter(text: str) -> tuple[str, str | None]:
    """Return ``(body, frontmatter)`` where frontmatter may be ``None``."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return text, None
    return text[match.end() :], match.group(1)


def _extract_title(slug: str, body: str, frontmatter: str | None) -> str:
    if frontmatter:
        title_match = _TITLE_RE.search(frontmatter)
        if title_match:
            value = title_match.group("value").strip()
            # Strip surrounding quotes the YAML often carries.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if value:
                return value
    heading_match = _HEADING_RE.search(body)
    if heading_match:
        return heading_match.group(2).strip()
    return slug.replace("-", " ").replace("_", " ").title()


def _iter_doc_files(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _DOC_EXTENSIONS:
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        files.append(path)
    return sorted(files)


@lru_cache(maxsize=1)
def _discover_docs_cached(root_str: str) -> tuple[DocPage, ...]:
    """Cached version of :func:`discover_docs` keyed on the resolved root.

    The cache is process-local; re-launching the interactive shell picks up
    any docs edits made since startup. Within one shell session, repeated
    docs queries reuse the parsed pages.
    """
    pages: list[DocPage] = []
    root = Path(root_str)
    for path in _iter_doc_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        body, frontmatter = _strip_frontmatter(text)
        slug = path.stem
        relpath = path.relative_to(root).as_posix()
        pages.append(
            DocPage(
                slug=slug,
                relpath=relpath,
                title=_extract_title(slug, body, frontmatter),
                body=body,
            )
        )
    return tuple(pages)


def discover_docs(root: Path | None = None) -> list[DocPage]:
    """Walk the docs root, parse each MDX page, return them as :class:`DocPage` records."""
    target = root if root is not None else _DOCS_ROOT
    return list(_discover_docs_cached(str(target.resolve() if target.exists() else target)))


def _tokenize(text: str) -> set[str]:
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) >= 3}


def _query_tokens(query: str) -> set[str]:
    return _tokenize(query) - _QUERY_STOPWORDS


def _score(query_tokens: set[str], page: DocPage) -> int:
    """Rank pages by overlap with the query, weighting slug/title heavily.

    Title and slug hits weigh more than body hits because docs are organized
    by topic and the slug usually IS the integration / feature name. A page
    whose slug matches the query exactly (e.g. ``datadog.mdx`` for "configure
    Datadog") is boosted further so canonical setup pages outrank tangentially
    related comparison or tutorial pages.
    """
    if not query_tokens:
        return 0
    slug_normalized = page.slug.lower().replace("-", " ").replace("_", " ")
    slug_tokens = _tokenize(slug_normalized)
    title_tokens = _tokenize(page.title)
    headings_text = "\n".join(m.group(2) for m in _HEADING_RE.finditer(page.body))
    heading_tokens = _tokenize(headings_text)
    body_tokens = _tokenize(page.body)

    match_score = 0
    match_score += 8 * len(query_tokens & slug_tokens)
    match_score += 5 * len(query_tokens & title_tokens)
    match_score += 2 * len(query_tokens & heading_tokens)
    match_score += len(query_tokens & body_tokens)
    # Exact slug match (e.g. slug "datadog" for query token "datadog") signals
    # this is the canonical page for the topic.
    if page.slug.lower() in query_tokens:
        match_score += 12
    if match_score == 0:
        return 0
    # Slight penalty for nested subdirectories so root-level integration / setup
    # pages outrank tangential pages with the same keyword. Clamped to a floor
    # of 1 so a legitimate match is never zeroed out by depth alone — pages
    # under tutorials/ or use-cases/ should still surface as lower-ranked
    # results, not be dropped entirely.
    depth = page.relpath.count("/")
    return max(1, match_score - depth)


def find_relevant_docs(
    query: str,
    pages: list[DocPage] | None = None,
    *,
    top_n: int = _DEFAULT_TOP_N,
) -> list[DocPage]:
    """Return up to ``top_n`` docs most relevant to ``query``, ranked by overlap.

    Returns an empty list if the query has no useful tokens or no pages match.
    """
    qt = _query_tokens(query)
    if not qt:
        return []
    candidates = pages if pages is not None else discover_docs()
    scored = [(s, p) for p in candidates for s in [_score(qt, p)] if s > 0]
    scored.sort(key=lambda item: (-item[0], item[1].relpath))
    return [page for _, page in scored[:top_n]]


def _excerpt(body: str, max_chars: int = _MAX_PER_DOC_CHARS) -> str:
    """Trim a doc body to ``max_chars``, preferring to cut at a paragraph boundary."""
    body = body.strip()
    if len(body) <= max_chars:
        return body
    cutoff = body.rfind("\n\n", 0, max_chars)
    if cutoff < max_chars // 2:
        cutoff = max_chars
    return body[:cutoff].rstrip() + "\n\n[... excerpt truncated ...]\n"


def build_docs_index(pages: list[DocPage] | None = None, *, max_entries: int = 80) -> str:
    """Return a compact ``slug — title`` index of available pages.

    Always included so the LLM knows what topics docs cover even when
    nothing scored against the query.
    """
    candidates = pages if pages is not None else discover_docs()
    if not candidates:
        return ""
    lines = ["docs index (all available pages):"]
    for page in candidates[:max_entries]:
        lines.append(f"  - {page.relpath}: {page.title}")
    if len(candidates) > max_entries:
        lines.append(f"  ... and {len(candidates) - max_entries} more pages")
    return "\n".join(lines)


def build_docs_reference_text(
    query: str | None,
    *,
    top_n: int = _DEFAULT_TOP_N,
    max_chars: int = _DEFAULT_MAX_TOTAL_CHARS,
) -> str:
    """Assemble a docs reference block for LLM grounding.

    Includes the top-N most relevant pages (with body excerpts) followed by
    a compact index of all discovered pages. Returns ``""`` when no docs
    are available so callers can detect that and adjust the prompt.
    """
    pages = discover_docs()
    if not pages:
        return ""

    parts: list[str] = []
    if query:
        relevant = find_relevant_docs(query, pages, top_n=top_n)
    else:
        relevant = []

    for page in relevant:
        parts.append(f"=== docs/{page.relpath} (title: {page.title}) ===\n")
        parts.append(_excerpt(page.body))
        parts.append("\n\n")

    index = build_docs_index(pages)
    if index:
        parts.append(index)
        parts.append("\n")

    text = "".join(parts).rstrip() + "\n"
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[... docs reference truncated ...]\n"
    return text


__all__ = [
    "DocPage",
    "build_docs_index",
    "build_docs_reference_text",
    "discover_docs",
    "find_relevant_docs",
]
