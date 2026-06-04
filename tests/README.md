# Cross-package integration tests

Tests in this directory exercise behaviour that spans more than one Python
package — for example the WU1.7 multi-user isolation gate, which needs the
API, the ingestion worker, and the shared `horizons-core` repository layer
working together against a real Postgres.

Per-package unit tests live under each member's own `tests/` tree
(`packages/horizons-*/tests/`). Anything that mocks Postgres or hits only a
single package belongs there, not here.
