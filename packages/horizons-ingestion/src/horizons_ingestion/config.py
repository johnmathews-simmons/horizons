"""Environment-driven configuration for the ingestion worker.

Loaded once at process start by ``__main__.py`` and passed to the
``ClaimLoop`` constructor. Every knob is overridable so the demo can
re-tune live without a redeploy (CLAUDE.md §"Configuration over code").

See ``loop.md`` §"Configuration" for the env-var table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

_SQLALCHEMY_DRIVER_PREFIXES: Final = (
    "postgresql+asyncpg://",
    "postgresql+psycopg2://",
    "postgresql+psycopg://",
)


def asyncpg_dsn(url: str) -> str:
    """Strip SQLAlchemy ``+driver`` so ``asyncpg.connect`` accepts the URL.

    SQLAlchemy and the testcontainers Postgres image both hand out URLs
    like ``postgresql+asyncpg://...``; asyncpg's native client rejects
    that prefix. Idempotent: a bare ``postgresql://`` URL passes through.
    """
    for prefix in _SQLALCHEMY_DRIVER_PREFIXES:
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


@dataclass(frozen=True, slots=True)
class ClaimLoopConfig:
    db_url: str
    tick_interval_s: float = 0.05
    batch_size: int = 10
    failure_threshold: int = 5
    healthz_stale_after_s: float = 5.0
    healthz_host: str = "0.0.0.0"  # noqa: S104  # bind-all is intentional inside a container
    healthz_port: int = 8080
    pool_min: int = 2
    pool_max: int = 4

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ClaimLoopConfig:
        """Build a config from a mapping. Raises ``KeyError`` if ``HORIZONS_DB_URL`` is missing.

        ``os.environ`` satisfies ``Mapping[str, str]`` directly.
        """
        db_url = env.get("HORIZONS_DB_URL")
        if db_url is None:
            raise KeyError("HORIZONS_DB_URL")

        cfg = cls(
            db_url=db_url,
            tick_interval_s=_float(env.get("HORIZONS_INGESTION_TICK_INTERVAL_S"), 0.05),
            batch_size=_int(env.get("HORIZONS_INGESTION_BATCH_SIZE"), 10),
            failure_threshold=_int(env.get("HORIZONS_INGESTION_FAILURE_THRESHOLD"), 5),
            healthz_stale_after_s=_float(env.get("HORIZONS_INGESTION_HEALTHZ_STALE_AFTER_S"), 5.0),
            healthz_host=env.get("HORIZONS_INGESTION_HEALTHZ_HOST") or "0.0.0.0",  # noqa: S104
            healthz_port=_int(env.get("HORIZONS_INGESTION_HEALTHZ_PORT"), 8080),
            pool_min=_int(env.get("HORIZONS_INGESTION_POOL_MIN"), 2),
            pool_max=_int(env.get("HORIZONS_INGESTION_POOL_MAX"), 4),
        )
        if cfg.pool_min > cfg.pool_max:
            raise ValueError(f"pool_min ({cfg.pool_min}) must be <= pool_max ({cfg.pool_max})")
        return cfg


def _int(value: str | None, default: int) -> int:
    return int(value) if value is not None and value != "" else default


def _float(value: str | None, default: float) -> float:
    return float(value) if value is not None and value != "" else default
