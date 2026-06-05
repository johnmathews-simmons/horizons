"""Pydantic DTOs for Lawstronaut responses.

The model layer is where the four documented quirks (see
``docs/api/operational-notes.md``) are tolerated:

- ``MarkdownDocument`` reads ``content_markdown`` and falls back to
  ``markdown`` (docs say the field is ``markdown``; the live API
  returns ``content_markdown``).
- ``MarkdownDocument.document_id`` is coerced to ``str`` regardless of
  upstream type (sometimes int, sometimes string).
- ``MarkdownDocument.publication_date`` runs through a tolerant parser
  that normalises the malformed ``"T00:00:000Z"`` milliseconds shape
  before ``datetime.fromisoformat``.
- ``Portal.total_links`` accepts either ``int`` or ``str`` and
  normalises to ``int | None``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.types import SecretStr


class Credentials(BaseModel):
    """Email + password used at ``/auth/login``."""

    model_config = ConfigDict(frozen=True)

    email: str
    password: SecretStr

    @field_validator("password", mode="before")
    @classmethod
    def _wrap_password(cls, v: Any) -> Any:
        if isinstance(v, SecretStr):
            return v
        return SecretStr(str(v))


_MALFORMED_MS_RE = re.compile(r"T(\d{2}:\d{2}):0{3}(Z|[+-]\d{2}:?\d{2})$")


def normalise_publication_date(value: Any) -> datetime | None:
    """Tolerate the ``"2026-06-03T00:00:000Z"`` shape.

    Returns ``None`` when the input is missing or unparseable. Downstream
    change-detection treats ``publication_date`` as a hint, not a key,
    so a missing value is preferable to crashing on a malformed one.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # "T00:00:000Z" → "T00:00:00Z". Match the bad triple-zero milliseconds
    # field and drop it; ISO 8601 has no millisecond marker in that slot.
    match = _MALFORMED_MS_RE.search(text)
    if match is not None:
        text = text[: match.start()] + "T" + match.group(1) + match.group(2)
    # ``fromisoformat`` accepts "Z" only on 3.11+, which is fine — we
    # target 3.13. Normalise just in case.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


_OptionalDatetime = Annotated[datetime | None, Field(default=None)]


class MarkdownDocument(BaseModel):
    """One markdown document record from ``/v2/contents/markdown``."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    document_id: str
    markdown: str
    version: int | None = None
    publication_date: _OptionalDatetime = None
    language: str | None = None
    iso: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        source: dict[str, Any] = dict(value)  # type: ignore[arg-type]
        record: dict[str, Any] = dict(source)
        # document_id can come back as int or str — coerce.
        if "document_id" in record and record["document_id"] is not None:
            record["document_id"] = str(record["document_id"])
        # Prefer content_markdown (live), fall back to markdown (docs).
        if "markdown" not in record:
            md = record.get("content_markdown")
            if md is not None:
                record["markdown"] = md
        # Tolerant publication_date.
        if "publication_date" in record:
            record["publication_date"] = normalise_publication_date(record["publication_date"])
        # Keep the raw record around so WU3.4 can compute a hash over the
        # canonical bytes, not just the parsed view.
        record.setdefault("raw", source)
        return record


class Jurisdiction(BaseModel):
    """One row from ``/v2/jurisdictions``."""

    model_config = ConfigDict(frozen=True)

    name: str
    iso: str
    type: str | None = None


class Portal(BaseModel):
    """One row from ``/v2/portals``.

    ``total_links`` comes back as either a string (live API) or a number
    (docs); both normalise to ``int | None``. Missing or empty becomes
    ``None``.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    url: str
    language: str | None = None
    jurisdiction: dict[str, Any] | None = None
    total_links: int | None = None

    @field_validator("total_links", mode="before")
    @classmethod
    def _coerce_total_links(cls, v: Any) -> int | None:
        if v is None:
            return None
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return None
            try:
                return int(stripped)
            except ValueError:
                return None
        if isinstance(v, int):
            return v
        return None
