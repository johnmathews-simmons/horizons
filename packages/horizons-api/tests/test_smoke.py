"""Smoke test: the package imports and can reach horizons-core."""

import horizons_api
import horizons_core


def test_package_imports() -> None:
    assert horizons_api.__version__ == "0.0.0"
    assert horizons_core.__version__ == "0.0.0"
