"""Clause-tree parser and (later) alignment pipeline.

The parser is in :mod:`horizons_core.core.alignment.parser`; clause and
config types are re-exported here for convenience.
"""

from horizons_core.core.alignment.clause import Clause
from horizons_core.core.alignment.config import (
    ParserConfig,
    StructuralPattern,
    default_parser_config,
    default_patterns,
)
from horizons_core.core.alignment.parser import parse

__all__ = [
    "Clause",
    "ParserConfig",
    "StructuralPattern",
    "default_parser_config",
    "default_patterns",
    "parse",
]
