"""Common setup for all tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from presidio_sidecar import resources


@pytest.fixture(autouse=True)
def _fresh_model_inventory() -> Iterator[None]:
    """``/health`` caches the model inventory. Tests replace locations via
    ``monkeypatch``, which the cache key cannot see."""
    resources.invalidate_model_components()
    yield
    resources.invalidate_model_components()
