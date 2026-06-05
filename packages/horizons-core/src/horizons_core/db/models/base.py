"""Declarative ``Base`` shared by every ORM model in this package."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Root of the declarative class hierarchy.

    Subclassing ``DeclarativeBase`` (SQLAlchemy 2.x style) gives every
    model a shared ``metadata`` object that Alembic's autogenerate
    consumes via ``migrations/env.py``.
    """
