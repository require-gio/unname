"""Translation table: ``/vault/*``.

``/vault/status`` and ``/vault/rotate`` existed for the old UI; their responses stay backward
compatible. New endpoints are clear, backup, and import. Any operation that makes existing codes
unreadable requires explicit confirmation – the UI shows a dialog describing the consequences.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response
from starlette.status import (
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from ..api_common import parse_model, read_json_object
from ..api_models import (
    VaultBackupRequest,
    VaultConfirmRequest,
    VaultImportRequest,
    VaultStatusResponse,
)
from ..services import PhaseOneServices
from ..vault import ROTATION_WARNING, VaultError
from ..vault_backup import MAX_BACKUP_BYTES, VaultBackupError

_UNAVAILABLE = "Die Übersetzungstabelle ist gerade nicht sicher verfügbar."


def build_router(services: PhaseOneServices) -> APIRouter:
    router = APIRouter()

    async def call(function: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except VaultBackupError as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": error.code, "message": str(error)},
            ) from error
        except (OSError, VaultError) as error:
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE, detail=_UNAVAILABLE
            ) from error

    def status() -> dict[str, Any]:
        return _status(services.vault().status().as_dict())

    def confirmed(flag: bool, code: str) -> None:
        if flag is not True:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": code, "message": ROTATION_WARNING},
            )

    @router.get("/vault/status")
    async def vault_status() -> Any:
        """Metrics without secrets: mappings, entries, key date."""

        try:
            return await run_in_threadpool(status)
        except (OSError, VaultError):
            return JSONResponse(
                {
                    "state": "unavailable",
                    "key_fingerprint": None,
                    "mapping_count": 0,
                    "entry_count": 0,
                    "warning": ROTATION_WARNING,
                },
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
            )

    @router.post("/vault/rotate")
    async def rotate_vault(request: Request) -> Any:
        """\"New key …\": only after explicit confirmation."""

        payload = await read_json_object(request, max_bytes=4096)
        if payload.get("confirm_key_change") is not True:
            raise HTTPException(status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=ROTATION_WARNING)
        try:
            await run_in_threadpool(services.vault().rotate_secret, confirm_loss=True)
            return await run_in_threadpool(status)
        except (OSError, VaultError) as error:
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
                detail="Der lokale Schlüssel konnte nicht sicher gewechselt werden.",
            ) from error

    @router.post("/vault/clear")
    async def clear_vault(request: Request) -> Any:
        """\"Clear table …\": all mappings removed, key stays."""

        body = parse_model(VaultConfirmRequest, await read_json_object(request, max_bytes=4096))
        confirmed(body.confirm, "confirm_required")

        def run() -> dict[str, Any]:
            services.vault().clear(confirm=True)
            return status()

        return await call(run)

    @router.post("/vault/backup")
    async def backup_vault(request: Request) -> Response:
        """Backup with password – the shell writes it to the chosen location."""

        body = parse_model(VaultBackupRequest, await read_json_object(request, max_bytes=8192))
        payload = await call(services.vault().export_backup, body.password)
        return Response(
            content=payload,
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/vault/import")
    async def import_vault(request: Request) -> Any:
        """Read backup: replace this device's keys and table."""

        raw = await read_json_object(request, max_bytes=MAX_BACKUP_BYTES * 4 // 3 + 64 * 1024)
        body = parse_model(VaultImportRequest, raw)
        confirmed(body.confirm, "confirm_required")
        try:
            backup = base64.b64decode(body.backup, validate=True)
        except (binascii.Error, ValueError) as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "invalid_backup", "message": "Die Sicherung ist unlesbar."},
            ) from error
        if len(backup) > MAX_BACKUP_BYTES:
            raise HTTPException(
                status_code=HTTP_413_CONTENT_TOO_LARGE,
                detail={"code": "too_large", "message": "Die Sicherung ist zu gross."},
            )

        def run() -> dict[str, Any]:
            services.vault().import_backup(backup, body.password, confirm=True)
            return status()

        return await call(run)

    return router


def _status(raw: dict[str, Any]) -> dict[str, Any]:
    """Legacy fields for the previous UI plus the new metrics."""

    checked = VaultStatusResponse.model_validate(raw).model_dump(mode="json")
    return {**raw, **checked}


__all__ = ["build_router"]
