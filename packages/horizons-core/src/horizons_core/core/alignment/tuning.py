"""Runtime-tunable knobs for the similarity stack.

:class:`TuningConfig` is the codebase's first piece of "runtime-tunable
config" (per CLAUDE.md's "Configuration over code … live as
runtime-tunable config, surfaced in the UI"). WU2.2 ships the model and
a YAML loader mirroring :mod:`horizons_core.core.alignment.portal_config`;
admin-UI surfacing arrives with the rest of WU3.x.

The four primitives in :mod:`horizons_core.core.alignment.similarity`
take primitive ints / floats — the alignment pipeline (WU2.3) is the
seam that reads :class:`TuningConfig` and forwards the numbers in.
"""

from __future__ import annotations

from importlib import resources
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_CONFIG_PACKAGE = "horizons_core.core.alignment.tuning_configs"
_SUFFIX = ".yaml"


class TuningConfig(BaseModel):
    """Similarity-stack tuning parameters.

    Defaults are starting values from the MinHash / LSH literature, not
    calibrated against the production corpus — expect to revisit them
    in WU2.4's regression suite and during demo pre-flight tuning. See
    ``docs/2. clause-alignment.md`` for the algorithmic context.
    """

    model_config = ConfigDict(frozen=True)

    shingle_k: int = Field(default=5, ge=1)
    signature_size: int = Field(default=128, ge=1)
    lsh_bands: int = Field(default=16, ge=1)
    similarity_threshold: float = Field(default=0.7, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _bands_divide_signature_size(self) -> Self:
        if self.signature_size % self.lsh_bands != 0:
            raise ValueError(
                f"signature_size ({self.signature_size}) must be divisible by "
                f"lsh_bands ({self.lsh_bands})",
            )
        return self


def default_tuning_config() -> TuningConfig:
    """Return the canonical Python-side starting config."""
    return TuningConfig()


def load_tuning_config(name: str = "_default") -> TuningConfig:
    """Return the :class:`TuningConfig` bundled at ``tuning_configs/<name>.yaml``.

    Raises :class:`KeyError` if no such config is bundled. The ``_default``
    name resolves to a YAML snapshot of :func:`default_tuning_config` —
    tests assert the two stay in sync.
    """
    files = resources.files(_CONFIG_PACKAGE)
    resource = files.joinpath(f"{name}{_SUFFIX}")
    if not resource.is_file():
        raise KeyError(f"no bundled tuning config named {name!r}")
    raw: Any = yaml.safe_load(resource.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    return TuningConfig.model_validate(raw)


def list_tuning_config_names() -> list[str]:
    """Return the bundled tuning config names, sorted, including ``_default``."""
    files = resources.files(_CONFIG_PACKAGE)
    names: list[str] = []
    for entry in files.iterdir():
        n = entry.name
        if entry.is_file() and n.endswith(_SUFFIX):
            names.append(n.removesuffix(_SUFFIX))
    return sorted(names)
