"""``GET /healthz`` — liveness probe.

Returns 200 with a static body. Deliberately does not touch Postgres
(or any other dependency) because the purpose is "the process is up
and the HTTP server is serving" — a liveness probe that depends on
the database tears down the pod the moment Postgres hiccups, which is
the opposite of what an orchestrator's liveness probe is for.

Readiness checks that *do* roundtrip the DB land in a later WU; they
go on a different path so the two probes can be scaled independently
in ACA's probe configuration.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}
