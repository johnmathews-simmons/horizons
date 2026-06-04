"""Smoke test: the package imports and exposes a version marker."""

import horizons_core


def test_package_imports() -> None:
    assert horizons_core.__version__ == "0.0.0"
