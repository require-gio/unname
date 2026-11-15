"""GET /catalog – categories, styles and placeholders as the single source."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..api_models import CatalogResponse
from ..catalog import catalog_payload


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/catalog")
    async def catalog() -> Any:
        # Simple and cheap: keep on the event loop.
        return CatalogResponse.model_validate(catalog_payload()).model_dump(mode="json")

    return router
