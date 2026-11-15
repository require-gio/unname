"""``GET`` / ``PUT /settings`` – settings with schema version."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT, HTTP_503_SERVICE_UNAVAILABLE

from ..api_common import read_json_object
from ..services import PhaseOneServices
from ..settings_store import SettingsError, SettingsValidationError


def build_router(services: PhaseOneServices) -> APIRouter:
    router = APIRouter()

    @router.get("/settings")
    async def get_settings() -> Any:
        try:
            current = await run_in_threadpool(lambda: services.settings_store().get())
        except SettingsError as error:
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error
        return current.model_dump(mode="json")

    @router.put("/settings")
    async def put_settings(request: Request) -> Any:
        """Apply the provided sections; omitted sections stay unchanged."""

        payload = await read_json_object(request, max_bytes=64 * 1024)
        try:
            updated = await run_in_threadpool(services.settings_store().update, payload)
        except SettingsValidationError as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        except SettingsError as error:
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
            ) from error
        if "history" in payload or "vault" in payload:
            # "Changes take effect immediately": a shorter retention period is applied right away,
            # not only on the next hourly run.
            try:
                await run_in_threadpool(services.apply_retention)
            except Exception as error:  # noqa: BLE001 — the setting still applies
                services.diagnostics.record(
                    "retention",
                    "Die geaenderte Frist konnte nicht sofort angewendet werden.",
                    severity="warning",
                    error_type=type(error).__name__,
                )
        return updated.model_dump(mode="json")

    return router
