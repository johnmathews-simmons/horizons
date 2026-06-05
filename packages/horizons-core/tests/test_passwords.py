"""Unit tests for the argon2 password helpers.

The hashes themselves are opaque — the assertions are that round-trip
matches succeed, wrong passwords are rejected, the hash is salted
(equal plaintexts produce different hashes), and ``needs_rehash`` is
reachable.
"""

from __future__ import annotations

from horizons_core.core.auth.passwords import (
    hash_password,
    needs_rehash,
    verify_password,
)


def test_hash_then_verify_succeeds() -> None:
    h = hash_password("correct horse battery staple")
    assert verify_password(plaintext="correct horse battery staple", password_hash=h)


def test_wrong_password_is_rejected() -> None:
    h = hash_password("correct horse battery staple")
    assert not verify_password(plaintext="hunter2", password_hash=h)


def test_hash_is_salted_per_call() -> None:
    """Identical plaintexts hash to different strings — proves the salt."""
    a = hash_password("same")
    b = hash_password("same")
    assert a != b
    assert verify_password(plaintext="same", password_hash=a)
    assert verify_password(plaintext="same", password_hash=b)


def test_needs_rehash_default_params_is_false() -> None:
    """A fresh hash with library defaults must not need rehashing."""
    h = hash_password("anything")
    assert needs_rehash(h) is False
