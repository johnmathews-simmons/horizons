"""Clause-tree parser and alignment-pipeline primitives.

The parser is in :mod:`horizons_core.core.alignment.parser`; similarity
primitives are in :mod:`horizons_core.core.alignment.similarity`; the
alignment pipeline itself is in :mod:`horizons_core.core.alignment.align`.
Clause, config, and tuning types are re-exported here for convenience.
"""

from horizons_core.core.alignment.align import ChangeEvent, ChangeType, align
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
from horizons_core.core.alignment.similarity import (
    MINHASH_SEED,
    jaccard,
    lsh_candidates,
    minhash,
    shingle,
)
from horizons_core.core.alignment.tuning import (
    TuningConfig,
    default_tuning_config,
    list_tuning_config_names,
    load_tuning_config,
)

__all__ = [
    "MINHASH_SEED",
    "ChangeEvent",
    "ChangeType",
    "Clause",
    "IgnorePattern",
    "ParserConfig",
    "StructuralPattern",
    "TuningConfig",
    "align",
    "default_parser_config",
    "default_patterns",
    "default_tuning_config",
    "jaccard",
    "list_portal_slugs",
    "list_tuning_config_names",
    "load_portal_config",
    "load_tuning_config",
    "lsh_candidates",
    "minhash",
    "parse",
    "shingle",
]
