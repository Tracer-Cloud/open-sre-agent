"""Structured prompt blocks rendered at the provider boundary."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

type PromptBlockKind = Literal["system", "rule", "context", "conversation", "tool", "user"]


@dataclass(frozen=True)
class PromptBlock:
    """One model-visible prompt block with optional provenance metadata."""

    id: str
    content: str
    kind: PromptBlockKind = "context"
    title: str | None = None
    priority: int = 0
    provenance: str | None = None
    token_estimate: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    include_title: bool = False

    def render(self) -> str:
        """Render this block without changing its body text."""
        if not self.content:
            return ""
        if self.include_title and self.title:
            return f"--- {self.title} ---\n{self.content}"
        return self.content


@dataclass(frozen=True)
class PromptEnvelope:
    """Ordered collection of structured prompt blocks.

    The envelope keeps block identity, kind, priority, provenance, and token
    estimates available to tests and future trimming policy while still
    rendering to the same prompt strings current providers accept.
    """

    blocks: tuple[PromptBlock, ...] = ()
    separator: str = "\n\n"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_text(
        cls,
        content: str,
        *,
        block_id: str = "prompt",
        kind: PromptBlockKind = "system",
        metadata: Mapping[str, Any] | None = None,
    ) -> PromptEnvelope:
        """Wrap an existing string prompt in a single structured block."""
        return cls(
            blocks=(PromptBlock(id=block_id, content=content, kind=kind),),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_blocks(
        cls,
        blocks: Iterable[PromptBlock],
        *,
        separator: str = "\n\n",
        metadata: Mapping[str, Any] | None = None,
    ) -> PromptEnvelope:
        return cls(
            blocks=tuple(blocks),
            separator=separator,
            metadata=dict(metadata or {}),
        )

    def with_block(self, block: PromptBlock) -> PromptEnvelope:
        """Return a copy with ``block`` appended."""
        return replace(self, blocks=(*self.blocks, block))

    def block(self, block_id: str) -> PromptBlock | None:
        """Return the block with ``block_id`` if present."""
        return next((block for block in self.blocks if block.id == block_id), None)

    def require_block(self, block_id: str) -> PromptBlock:
        """Return a block by id, raising a clear error when it is absent."""
        block = self.block(block_id)
        if block is None:
            raise KeyError(f"PromptEnvelope block not found: {block_id}")
        return block

    def render(self) -> str:
        """Render all non-empty blocks in order."""
        rendered = [block.render() for block in self.blocks]
        return self.separator.join(text for text in rendered if text)


__all__ = ["PromptBlock", "PromptBlockKind", "PromptEnvelope"]
