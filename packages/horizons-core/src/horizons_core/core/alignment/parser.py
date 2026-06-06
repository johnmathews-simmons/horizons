"""Markdown → clause tree.

Public entry point: :func:`parse`. See ``docs/5. clause-tree-parser.md``
for the algorithm and pattern model. Pure function — no I/O, no DB.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from markdown_it import MarkdownIt

from horizons_core.core.alignment.clause import Clause
from horizons_core.core.alignment.config import (
    ParserConfig,
    StructuralPattern,
    default_parser_config,
)
from horizons_core.core.alignment.portal_config import load_portal_config

if TYPE_CHECKING:
    from markdown_it.token import Token


_TERMINATORS = frozenset('.!?;:)"“”„—')


def parse(
    markdown_text: str,
    *,
    config: ParserConfig | None = None,
    portal_slug: str | None = None,
) -> Clause:
    """Parse markdown into a :class:`Clause` tree.

    The returned root clause has empty ``path`` and no body; its
    ``children`` are the document's top-level structural clauses.

    If ``config`` is given it is used directly. Otherwise, when
    ``portal_slug`` is supplied the matching bundled config is loaded
    (see :func:`portal_config.load_portal_config`); the default config
    is used when neither is supplied.
    """
    if config is not None:
        cfg = config
    elif portal_slug is not None:
        cfg = load_portal_config(portal_slug)
    else:
        cfg = default_parser_config()
    builder = _TreeBuilder(cfg)
    md = MarkdownIt("commonmark")
    tokens = md.parse(markdown_text)

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "paragraph_open":
            inline = tokens[i + 1]
            text, bold_ranges = _extract_inline(inline)
            builder.consume_paragraph(text, bold_ranges)
            i += 3
        elif tok.type == "heading_open":
            inline = tokens[i + 1]
            text, _ = _extract_inline(inline)
            level = int(tok.tag[1:])
            builder.consume_heading(level, text)
            i += 3
        elif tok.type in {"html_block", "code_block", "fence"}:
            builder.consume_loose_text(tok.content)
            i += 1
        else:
            i += 1

    return builder.finalize()


def _extract_inline(inline: Token) -> tuple[str, list[tuple[int, int]]]:
    """Render an inline token to plain text with bold-span offsets.

    markdown-it emits matched ``strong_open`` / ``strong_close`` pairs; a
    stray close without a prior open would just emit a zero-prefix range
    that doesn't change bold-only detection.
    """
    parts: list[str] = []
    bold_ranges: list[tuple[int, int]] = []
    cur_strong_start = 0
    pos = 0
    for child in inline.children or []:
        if child.type == "strong_open":
            cur_strong_start = pos
        elif child.type == "strong_close":
            bold_ranges.append((cur_strong_start, pos))
        elif child.type == "text":
            parts.append(child.content)
            pos += len(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
            pos += 1
        elif child.type == "code_inline":
            parts.append(child.content)
            pos += len(child.content)
        # link_open/close, em_open/close, image and friends contribute no
        # plain text directly — their inner text tokens are emitted as
        # siblings and already counted above.
    return "".join(parts), bold_ranges


def _is_bold_only(text: str, bold_ranges: list[tuple[int, int]]) -> bool:
    """True iff at least one non-whitespace char exists and all lie in bold."""
    inside = [False] * len(text)
    for start, end in bold_ranges:
        for j in range(start, min(end, len(text))):
            inside[j] = True
    saw_non_ws = False
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        if not inside[i]:
            return False
        saw_non_ws = True
    return saw_non_ws


def _has_boundary_before(text: str, pos: int) -> bool:
    """True if position ``pos`` is at start, or preceded by terminator + ws."""
    if pos == 0:
        return True
    if not text[pos - 1].isspace():
        return False
    j = pos - 2
    while j >= 0 and text[j].isspace():
        j -= 1
    if j < 0:
        return True
    return text[j] in _TERMINATORS


def _find_matches(
    text: str, compiled: list[tuple[StructuralPattern, re.Pattern[str]]]
) -> list[tuple[StructuralPattern, int, int]]:
    """Left-to-right non-overlapping scan. Returns (pattern, start, end)."""
    results: list[tuple[StructuralPattern, int, int]] = []
    pos = 0
    n = len(text)
    while pos < n:
        hit: tuple[StructuralPattern, int, int] | None = None
        for pattern, regex in compiled:
            m = regex.match(text, pos)
            if m is None:
                continue
            if pattern.requires_boundary and not _has_boundary_before(text, pos):
                continue
            hit = (pattern, pos, m.end())
            break
        if hit is not None:
            results.append(hit)
            pos = hit[2]
        else:
            pos += 1
    return results


@dataclass
class _MutClause:
    depth: int
    path: tuple[str, ...]
    heading_text: str | None
    body_parts: list[str]
    numbering_label: str | None
    children: list[_MutClause]

    def freeze(self) -> Clause:
        return Clause(
            path=self.path,
            heading_text=self.heading_text,
            body_text=" ".join(p.strip() for p in self.body_parts if p.strip()),
            numbering_label=self.numbering_label,
            children=tuple(c.freeze() for c in self.children),
        )


_SLUG_RE = re.compile(r"[^\w]+", re.UNICODE)


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.strip().lower()).strip("-")[:64]


def _disambiguate_segment(segment: str, siblings: list[_MutClause]) -> str:
    """Return ``segment`` rewritten so its full string is unique among siblings.

    Only the leaf (last) segment matters for uniqueness inside a parent —
    each ``_MutClause`` in ``siblings`` carries ``path[-1]`` as its
    contribution to its parent's child segments. The first occurrence
    keeps the base ``segment``; subsequent duplicates get ``-2``, ``-3``,
    etc. Stable: deterministic for any fixed input order, which is what
    the alignment pipeline relies on to pair v1 and v2 clauses across
    versions with the same duplicate-heading structure.
    """
    taken = {sib.path[-1] for sib in siblings if sib.path}
    if segment not in taken:
        return segment
    suffix = 2
    candidate = f"{segment}-{suffix}"
    while candidate in taken:
        suffix += 1
        candidate = f"{segment}-{suffix}"
    return candidate


class _TreeBuilder:
    def __init__(self, cfg: ParserConfig) -> None:
        self._cfg = cfg
        self._compiled: list[tuple[StructuralPattern, re.Pattern[str]]] = [
            (p, re.compile(p.regex)) for p in cfg.patterns
        ]
        self._compiled_ignore: list[re.Pattern[str]] = [
            re.compile(p.regex) for p in cfg.ignore_patterns
        ]
        self._root = _MutClause(
            depth=0,
            path=(),
            heading_text=None,
            body_parts=[],
            numbering_label=None,
            children=[],
        )
        self._stack: list[_MutClause] = [self._root]
        self._pending_heading: str | None = None

    def consume_paragraph(self, text: str, bold_ranges: list[tuple[int, int]]) -> None:
        stripped = text.strip()
        if any(rx.fullmatch(stripped) for rx in self._compiled_ignore):
            return
        matches = _find_matches(text, self._compiled)
        if not matches:
            if self._cfg.treat_unmatched_bold_as_heading and _is_bold_only(text, bold_ranges):
                self._absorb_bold_heading(stripped)
            else:
                self._add_leaf(stripped)
            return
        first_start = matches[0][1]
        if first_start > 0:
            prefix = text[:first_start].strip()
            if prefix:
                self._add_leaf(prefix)
        for idx, (pattern, _start, end) in enumerate(matches):
            label = text[_start:end].strip()
            next_start = matches[idx + 1][1] if idx + 1 < len(matches) else len(text)
            body = text[end:next_start].strip()
            self._open_clause(pattern.depth, label, body)

    def consume_heading(self, level: int, text: str) -> None:
        text = text.strip()
        if not text:
            return
        depth = max(1, level + self._cfg.heading_depth_offset)
        self._open_clause(depth, label=None, body="", explicit_heading=text)

    def consume_loose_text(self, text: str) -> None:
        stripped = text.strip()
        if stripped:
            self._stack[-1].body_parts.append(stripped)

    def finalize(self) -> Clause:
        return self._root.freeze()

    def _absorb_bold_heading(self, text: str) -> None:
        top = self._stack[-1]
        if top is self._root or top.heading_text is not None:
            self._pending_heading = text
        else:
            top.heading_text = text

    def _open_clause(
        self,
        depth: int,
        label: str | None,
        body: str,
        *,
        explicit_heading: str | None = None,
    ) -> None:
        while len(self._stack) > 1 and self._stack[-1].depth >= depth:
            self._stack.pop()
        parent = self._stack[-1]
        heading = explicit_heading
        if heading is None and self._pending_heading is not None:
            heading = self._pending_heading
            self._pending_heading = None
        ord_idx = len(parent.children) + 1
        if label:
            segment = label
        else:
            # consume_heading guards against empty heading text, and
            # consume_paragraph never reaches here without a label, so
            # ``heading`` is always non-empty at this point.
            assert heading is not None
            segment = _slugify(heading) or f"#{ord_idx}"
        # Disambiguate against existing siblings. Two structural patterns
        # can produce the same `label` (e.g. an errata-style re-issue of
        # "Article 5"), and a slugified heading can collide when a
        # document has multiple sub-sections sharing a title (e.g. three
        # "Position de la Commission" headings under different Griefs in
        # the ACPR FR fixture). The clauses table enforces
        # UNIQUE(document_version_id, clause_path), so the segment must
        # be unique among ``parent``'s children. Apply a ``-2`` / ``-3``
        # suffix to the later occurrences; the first occurrence keeps
        # the base slug so existing fixtures with no duplicates are
        # unaffected.
        segment = _disambiguate_segment(segment, parent.children)
        node = _MutClause(
            depth=depth,
            path=parent.path + (segment,),
            heading_text=heading,
            body_parts=[body] if body else [],
            numbering_label=label,
            children=[],
        )
        parent.children.append(node)
        self._stack.append(node)

    def _add_leaf(self, body: str) -> None:
        parent = self._stack[-1]
        ord_idx = len(parent.children) + 1
        node = _MutClause(
            depth=parent.depth + 1,
            path=parent.path + (f"#{ord_idx}",),
            heading_text=None,
            body_parts=[body],
            numbering_label=None,
            children=[],
        )
        parent.children.append(node)
