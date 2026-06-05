"""Unit tests for the sweep-key recognition helper.

The full :class:`SweepLoop` is exercised by an integration test in
``tests/integration/test_blob_sweep.py``. Here we cover the pure
helper that decides whether a blob name matches our content-addressed
convention.
"""

from __future__ import annotations

from horizons_ingestion.sweep import _is_content_addressed_key

SHA = "a" * 64


def test_recognises_canonical_shape() -> None:
    assert _is_content_addressed_key(f"{SHA}.md") is True


def test_rejects_wrong_extension() -> None:
    assert _is_content_addressed_key(f"{SHA}.txt") is False
    assert _is_content_addressed_key(SHA) is False


def test_rejects_wrong_length() -> None:
    assert _is_content_addressed_key("a" * 63 + ".md") is False
    assert _is_content_addressed_key("a" * 65 + ".md") is False


def test_rejects_non_hex() -> None:
    assert _is_content_addressed_key("z" * 64 + ".md") is False


def test_rejects_uppercase_hex() -> None:
    """sha256 .hex() emits lowercase; uppercase is foreign data."""
    assert _is_content_addressed_key("A" * 64 + ".md") is False
