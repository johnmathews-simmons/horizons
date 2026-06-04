"""Alembic migration environment.

Sources the database URL from the ``HORIZONS_DB_URL`` environment
variable so credentials never live in ``alembic.ini``. The sync driver
``psycopg`` is used here because Alembic is sync-only; the application
uses ``asyncpg`` separately.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_db_url = os.environ.get("HORIZONS_DB_URL")
if _db_url is None:
    raise RuntimeError(
        "HORIZONS_DB_URL must be set before running Alembic. "
        "Example: postgresql+psycopg://user:pw@host:5432/db"
    )
config.set_main_option("sqlalchemy.url", _db_url)

# No declarative metadata yet — schema lands in later work units. Autogenerate
# will be wired up when the first ORM models exist.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
