"""Clause-tree parser and (later) alignment pipeline.

The parser is in :mod:`horizons_core.core.alignment.parser`; clause and
config types are re-exported here for convenience.
"""

from horizons_core.core.alignment.clause import Clause
from horizons_core.core.alignment.config import (
    IgnorePattern,
    ParserConfig,
    StructuralPattern,
    default_parser_config,
    default_patterns,
)
from horizons_core.core.alignment.parser import parse
from horizons_core.core.alignment.portal_config import (
    list_portal_slugs,
    load_portal_config,
)

__all__ = [
    "Clause",
    "IgnorePattern",
    "ParserConfig",
    "StructuralPattern",
    "default_parser_config",
    "default_patterns",
    "list_portal_slugs",
    "load_portal_config",
    "parse",
]
