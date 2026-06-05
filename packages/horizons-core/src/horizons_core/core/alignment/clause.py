"""Immutable clause-tree node produced by :mod:`horizons_core.core.alignment.parser`.

A ``Clause`` is one heading-anchored fragment of a parsed document. The tree
shape carries structural locality across alignment; the ``path`` field on each
node is the human-readable address used in change-event reporting. See
``docs/5. clause-tree-parser.md`` for the design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True, slots=True)
class Clause:
    path: tuple[str, ...]
    heading_text: str | None
    body_text: str
    numbering_label: str | None
    children: tuple[Clause, ...] = ()

    def walk(self) -> Iterator[Clause]:
        """DFS pre-order traversal yielding this node then its descendants."""
        yield self
        for child in self.children:
            yield from child.walk()
