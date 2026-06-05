"""Password hashing + verification (argon2-cffi).

Thin wrapper around ``argon2.PasswordHasher`` so the rest of the app
imports a stable surface and never sees a third-party verifier
exception leak through. ``users.password_hash`` is opaque to the
database — its format is owned here. argon2id with the library's
default parameters is the WU4.0 choice; rotating to stronger
parameters is a ``needs_rehash`` check at login time, no migration.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plaintext: str) -> str:
    """Hash ``plaintext`` for storage in ``users.password_hash``."""
    return _hasher.hash(plaintext)


def verify_password(*, plaintext: str, password_hash: str) -> bool:
    """Constant-time verify ``plaintext`` against the stored hash.

    Returns ``False`` for a mismatch; ``True`` for a match. Any other
    failure (corrupted hash, unsupported variant, ...) is allowed to
    propagate — those signal a code-or-data bug, not a credential
    rejection, and silently swallowing them would hide it.
    """
    try:
        return _hasher.verify(password_hash, plaintext)
    except VerifyMismatchError:
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when ``password_hash`` was minted with weaker parameters.

    Callers (login path) should re-hash and update the row on the next
    successful login so the upgrade happens lazily without forcing a
    bulk migration.
    """
    return _hasher.check_needs_rehash(password_hash)
