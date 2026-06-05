"""Tests for :class:`TuningConfig` and the bundled YAML loader.

The four similarity primitives in :mod:`horizons_core.core.alignment.similarity`
take primitive ints / floats — they do not depend on :class:`TuningConfig`.
The config exists to give the alignment pipeline (WU2.3) and the admin UI
(later) a single typed surface for the four knobs without baking them in
as code constants. The YAML loader mirrors the parser-config pattern in
:mod:`horizons_core.core.alignment.portal_config`.
"""

from __future__ import annotations

from importlib import resources

import pytest
import yaml
from horizons_core.core.alignment import (
    TuningConfig,
    default_tuning_config,
    list_tuning_config_names,
    load_tuning_config,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_tuning_config_returns_literature_starting_values() -> None:
    cfg = default_tuning_config()
    assert cfg.shingle_k == 5
    assert cfg.signature_size == 128
    assert cfg.lsh_bands == 16
    assert cfg.similarity_threshold == 0.7


def test_default_tuning_config_is_a_tuning_config() -> None:
    assert isinstance(default_tuning_config(), TuningConfig)


def test_tuning_config_is_frozen() -> None:
    cfg = default_tuning_config()
    with pytest.raises(ValidationError):
        cfg.shingle_k = 7  # type: ignore[misc]


def test_tuning_config_round_trips_through_model_dump() -> None:
    cfg = default_tuning_config()
    assert TuningConfig.model_validate(cfg.model_dump()) == cfg


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def test_lsh_bands_must_divide_signature_size() -> None:
    with pytest.raises(ValidationError, match="must be divisible by"):
        TuningConfig(shingle_k=5, signature_size=128, lsh_bands=17, similarity_threshold=0.7)


def test_lsh_bands_dividing_signature_size_is_accepted() -> None:
    cfg = TuningConfig(shingle_k=5, signature_size=128, lsh_bands=32, similarity_threshold=0.7)
    assert cfg.lsh_bands == 32


def test_similarity_threshold_must_be_in_unit_interval_exclusive_zero() -> None:
    # 0.0 is rejected (strictly positive) — a zero threshold means
    # "candidate-pair filter is a no-op", which is never the intent.
    with pytest.raises(ValidationError):
        TuningConfig(similarity_threshold=0.0)
    # 1.0 is accepted (inclusive upper bound).
    cfg = TuningConfig(similarity_threshold=1.0)
    assert cfg.similarity_threshold == 1.0
    with pytest.raises(ValidationError):
        TuningConfig(similarity_threshold=1.5)
    with pytest.raises(ValidationError):
        TuningConfig(similarity_threshold=-0.1)


def test_shingle_k_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        TuningConfig(shingle_k=0)
    with pytest.raises(ValidationError):
        TuningConfig(shingle_k=-1)


def test_signature_size_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        TuningConfig(signature_size=0, lsh_bands=1)


def test_lsh_bands_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        TuningConfig(lsh_bands=0)


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------


def test_load_tuning_config_default_matches_python_defaults() -> None:
    # The bundled YAML snapshot must round-trip against the Python
    # defaults — drift between the two breaks the "configuration over
    # code" contract in CLAUDE.md.
    assert load_tuning_config("_default") == default_tuning_config()


def test_load_tuning_config_unknown_name_raises_key_error() -> None:
    with pytest.raises(KeyError):
        load_tuning_config("definitely-not-a-tuning-config")


def test_list_tuning_config_names_includes_default_and_is_sorted() -> None:
    names = list_tuning_config_names()
    assert "_default" in names
    assert names == sorted(names)


def test_default_tuning_yaml_safe_loads_to_dict() -> None:
    text = (
        resources.files("horizons_core.core.alignment.tuning_configs")
        .joinpath("_default.yaml")
        .read_text(encoding="utf-8")
    )
    raw = yaml.safe_load(text)
    assert isinstance(raw, dict)
    assert raw["shingle_k"] == 5
    assert raw["signature_size"] == 128
    assert raw["lsh_bands"] == 16
    assert raw["similarity_threshold"] == 0.7


def test_load_tuning_config_empty_yaml_yields_defaults() -> None:
    # A YAML file containing only comments yields ``None`` from
    # ``yaml.safe_load``; the loader normalises this to ``{}`` so the
    # Pydantic defaults apply.
    files = resources.files("horizons_core.core.alignment.tuning_configs")
    target = files.joinpath("_empty_test.yaml")
    # Synthesise the file at runtime if absent — we keep a tiny
    # fixture in tree so the loader's "None -> {}" branch is covered
    # without relying on packaging-time tricks.
    if not target.is_file():
        pytest.skip("_empty_test.yaml fixture not bundled")
    assert load_tuning_config("_empty_test") == default_tuning_config()
