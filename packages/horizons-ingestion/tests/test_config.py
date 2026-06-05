"""Unit tests for ``horizons_ingestion.config.ClaimLoopConfig``."""

from __future__ import annotations

import pytest
from horizons_ingestion.config import ClaimLoopConfig, asyncpg_dsn


def test_from_env_uses_defaults_when_only_db_url_set() -> None:
    cfg = ClaimLoopConfig.from_env({"HORIZONS_DB_URL": "postgresql://x/y"})

    assert cfg.db_url == "postgresql://x/y"
    assert cfg.tick_interval_s == 0.05
    assert cfg.batch_size == 10
    assert cfg.failure_threshold == 5
    assert cfg.healthz_stale_after_s == 5.0
    assert cfg.healthz_host == "0.0.0.0"  # noqa: S104  # bind-all is intentional in a container
    assert cfg.healthz_port == 8080
    assert cfg.pool_min == 2
    assert cfg.pool_max == 4


def test_from_env_overrides_take_precedence() -> None:
    cfg = ClaimLoopConfig.from_env(
        {
            "HORIZONS_DB_URL": "postgresql://x/y",
            "HORIZONS_INGESTION_TICK_INTERVAL_S": "0.5",
            "HORIZONS_INGESTION_BATCH_SIZE": "32",
            "HORIZONS_INGESTION_FAILURE_THRESHOLD": "7",
            "HORIZONS_INGESTION_HEALTHZ_STALE_AFTER_S": "12.5",
            "HORIZONS_INGESTION_HEALTHZ_HOST": "127.0.0.1",
            "HORIZONS_INGESTION_HEALTHZ_PORT": "9090",
            "HORIZONS_INGESTION_POOL_MIN": "3",
            "HORIZONS_INGESTION_POOL_MAX": "6",
        }
    )

    assert cfg.tick_interval_s == 0.5
    assert cfg.batch_size == 32
    assert cfg.failure_threshold == 7
    assert cfg.healthz_stale_after_s == 12.5
    assert cfg.healthz_host == "127.0.0.1"
    assert cfg.healthz_port == 9090
    assert cfg.pool_min == 3
    assert cfg.pool_max == 6


def test_from_env_raises_without_db_url() -> None:
    with pytest.raises(KeyError, match="HORIZONS_DB_URL"):
        ClaimLoopConfig.from_env({})


def test_from_env_rejects_pool_min_greater_than_pool_max() -> None:
    with pytest.raises(ValueError, match="pool_min"):
        ClaimLoopConfig.from_env(
            {
                "HORIZONS_DB_URL": "postgresql://x/y",
                "HORIZONS_INGESTION_POOL_MIN": "5",
                "HORIZONS_INGESTION_POOL_MAX": "2",
            }
        )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql://u:p@h:5432/d"),
        ("postgresql+asyncpg://u:p@h:5432/d", "postgresql://u:p@h:5432/d"),
        ("postgresql+psycopg2://u:p@h:5432/d", "postgresql://u:p@h:5432/d"),
        ("postgresql+psycopg://u:p@h:5432/d", "postgresql://u:p@h:5432/d"),
    ],
)
def test_asyncpg_dsn_strips_sqlalchemy_driver(url: str, expected: str) -> None:
    assert asyncpg_dsn(url) == expected
