"""Horizons public REST API.

The public API is the single HTTP surface; the webapp is just another
client. ``create_app`` (in ``app.py``) is the entry point used by
uvicorn / ACA's startup command and by tests.
"""

from __future__ import annotations

__version__ = "0.0.0"
