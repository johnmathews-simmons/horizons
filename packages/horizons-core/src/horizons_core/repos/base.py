"""Marker ``Repository[T]`` protocol.

A repository in this codebase is a class that takes an ``AsyncSession``
on construction and returns instances of a Pydantic DTO from its
read methods. The protocol carries no required methods because each
aggregate's surface differs (the watchlists repo writes; the corpus
repos only read), but every concrete implementation declares its DTO
type on ``dto_type`` so callers and static analysis have one anchor
per repo. See ``repos.md`` for the layer's place in the
defence-in-depth posture.
"""

from __future__ import annotations

from typing import ClassVar, Protocol, TypeVar

from pydantic import BaseModel

DTO_co = TypeVar("DTO_co", bound=BaseModel, covariant=True)


class Repository(Protocol[DTO_co]):
    """Marker for repositories returning ``DTO_co``.

    Concrete repos do not need to subclass this — duck-typing on the
    ``dto_type`` class attribute is enough. The protocol exists so
    code that wants to reason about "any repository returning ``X``"
    has a type to refer to.
    """

    dto_type: ClassVar[type[BaseModel]]
