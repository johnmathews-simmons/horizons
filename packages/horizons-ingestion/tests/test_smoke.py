"""Smoke test: the package imports and can reach horizons-core."""

import horizons_core
import horizons_ingestion


def test_package_imports() -> None:
    assert horizons_ingestion.__version__ == "0.0.0"
    assert horizons_core.__version__ == "0.0.0"
