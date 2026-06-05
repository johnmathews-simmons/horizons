"""YAML-backed per-portal :class:`ParserConfig` loader.

Bundled portal configs live as ``parser_configs/<slug>.yaml`` inside the
package data. Loaded via :mod:`importlib.resources` so they ship inside
the wheel without depending on the source tree layout. See
``docs/5. clause-tree-parser.md`` for the override conventions.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

import yaml

from horizons_core.core.alignment.config import ParserConfig

_CONFIG_PACKAGE = "horizons_core.core.alignment.parser_configs"
_SUFFIX = ".yaml"


def load_portal_config(slug: str) -> ParserConfig:
    """Return the :class:`ParserConfig` bundled at ``parser_configs/<slug>.yaml``.

    Raises :class:`KeyError` if no such config is bundled. The slug
    ``_default`` resolves to a YAML snapshot of
    :func:`horizons_core.core.alignment.config.default_parser_config` —
    test asserts the two stay in sync.
    """
    files = resources.files(_CONFIG_PACKAGE)
    resource = files.joinpath(f"{slug}{_SUFFIX}")
    if not resource.is_file():
        raise KeyError(f"no bundled parser config for portal slug {slug!r}")
    raw: Any = yaml.safe_load(resource.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    return ParserConfig.model_validate(raw)


def list_portal_slugs() -> list[str]:
    """Return the bundled portal slugs, sorted, including ``_default``."""
    files = resources.files(_CONFIG_PACKAGE)
    slugs: list[str] = []
    for entry in files.iterdir():
        name = entry.name
        if entry.is_file() and name.endswith(_SUFFIX):
            slugs.append(name.removesuffix(_SUFFIX))
    return sorted(slugs)
