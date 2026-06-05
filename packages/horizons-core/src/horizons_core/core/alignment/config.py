"""Configuration for the clause-tree parser.

Patterns are pure-data so per-portal overrides (WU2.1) can ship as YAML
without touching the parser. The default config covers the IE and CZ
sample fixtures; see ``docs/5. clause-tree-parser.md`` for the pattern
list and rationale.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StructuralPattern(BaseModel):
    """One structural-marker pattern.

    ``regex`` is matched at a scan position (anchored — see
    :func:`re.Pattern.match`). ``depth`` places successful matches in the
    clause tree; same-depth markers become siblings, deeper markers nest.
    ``requires_boundary`` rejects matches mid-prose unless preceded by a
    sentence-terminating punctuation followed by whitespace — this is how
    citation references like "subsection (1)" are kept out of the tree.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    regex: str
    depth: int
    requires_boundary: bool = True


class IgnorePattern(BaseModel):
    """A paragraph-suppression rule.

    A paragraph whose plain text (after stripping) ``re.fullmatch``-es
    ``regex`` is dropped before it enters the tree — no leaf, no pending
    heading. Used to suppress boilerplate (e.g. the Irish enacting
    formula) that would otherwise get absorbed as the next clause's
    heading.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    regex: str


def _empty_ignore_patterns() -> list[IgnorePattern]:
    return []


def default_patterns() -> list[StructuralPattern]:
    return [
        StructuralPattern(name="ie_part", regex=r"PART\s+\d+", depth=1, requires_boundary=False),
        StructuralPattern(
            name="cz_cast",
            regex=r"ČÁST\s+\S+",
            depth=1,
            requires_boundary=False,
        ),
        StructuralPattern(
            name="cz_clanek",
            regex=r"Čl\.\s+[IVXLCDM]+",
            depth=2,
            requires_boundary=False,
        ),
        StructuralPattern(name="section", regex=r"\d+[A-Z]?\.", depth=4),
        StructuralPattern(name="subsection", regex=r"\(\d+\)", depth=5),
        StructuralPattern(name="roman_subpara", regex=r"\((?:i+|[ivxlcdm]{2,})\)", depth=7),
        StructuralPattern(name="letter_para", regex=r"\([a-z]\)", depth=6),
    ]


class ParserConfig(BaseModel):
    """Top-level parser configuration.

    ``patterns`` controls which structural markers the parser recognises.
    ``ignore_patterns`` drops whole paragraphs that fullmatch any rule —
    boilerplate suppression (e.g. Irish enacting formula).
    ``treat_unmatched_bold_as_heading`` turns bold-only paragraphs (with
    no structural match) into headings attached to the next-opened clause;
    disable for portals where bold paragraphs are emphasis, not titles.
    ``heading_depth_offset`` controls where markdown ``#``-style headings
    land in the depth ladder.
    """

    model_config = ConfigDict(frozen=True)

    patterns: list[StructuralPattern] = Field(default_factory=default_patterns)
    ignore_patterns: list[IgnorePattern] = Field(default_factory=_empty_ignore_patterns)
    treat_unmatched_bold_as_heading: bool = True
    heading_depth_offset: int = 0


def default_parser_config() -> ParserConfig:
    return ParserConfig()
