"""Cross-package smoke test: all three Python members are importable together.

The full integration tests (multi-user isolation, end-to-end ingestion → API
flows) land in later work units. This single test exists so the cross-package
tests/ dir is non-empty and pytest's collection picks it up.
"""

import horizons_api
import horizons_core
import horizons_ingestion


def test_all_members_import() -> None:
    assert horizons_core.__version__ == "0.0.0"
    assert horizons_ingestion.__version__ == "0.0.0"
    assert horizons_api.__version__ == "0.0.0"
