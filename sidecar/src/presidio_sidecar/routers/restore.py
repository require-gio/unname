"""Restore: ``/restore/text`` and ``/restore/file`` endpoints."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.status import (
    HTTP_204_NO_CONTENT,
    HTTP_404_NOT_FOUND,
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from .. import restore
from ..api_common import parse_model, read_json_object
from ..api_models import RestoreFileResponse, RestoreTextRequest, RestoreTextResponse
from ..base_profile import base_profile
from ..config import Settings
from ..entries import EntryError
from ..services import PhaseOneServices
from ..vault import VaultError
from ..workflow import Upload

_ENTRY_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def build_router(services: PhaseOneServices, settings: Settings) -> APIRouter:
    router = APIRouter()
    max_text_bytes = settings.max_text_bytes + 64 * 1024

    async def call(function: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except restore.RestoreError as error:
            status = {
                "not_found": HTTP_404_NOT_FOUND,
                "too_large": HTTP_413_CONTENT_TOO_LARGE,
                "unsupported_format": HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            }.get(error.code, HTTP_422_UNPROCESSABLE_CONTENT)
            raise HTTPException(
                status_code=status, detail={"code": error.code, "message": str(error)}
            ) from error
        except (VaultError, EntryError, OSError) as error:
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
                detail="Die Uebersetzungstabelle ist nicht sicher verfuegbar.",
            ) from error

    @router.post("/restore/text")
    async def restore_text(request: Request) -> Any:
        """Restore a KI answer from memory only.

        Slight variations are recognized when unambiguous; uncertain guesses are marked unknown.
        """

        body = parse_model(
            RestoreTextRequest, await read_json_object(request, max_bytes=max_text_bytes)
        )
        result = await call(restore.restore_text, services, body)
        return RestoreTextResponse.model_validate(result).model_dump(mode="json")

    @router.post("/restore/file")
    async def restore_file(
        request: Request, entry_id: str | None = None, surrogates: bool = False
    ) -> Any:
        """Restore a KI file (DOCX, XLSX, Markdown, Text) with original names.

        As with submission, a single ``file`` part is expected; the shell read the file from dialog
        or dropzone.
        The restored file stays in memory until the shell writes it via ``GET /restore/file/{id}``.
        """

        if entry_id is not None and not _ENTRY_ID.fullmatch(entry_id):
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail="Ungueltige Eintrags-ID."
            )
        max_bytes = base_profile().max_file_bytes
        upload = await _read_single_upload(request, max_bytes=max_bytes)
        result = await call(
            restore.restore_file,
            services,
            upload,
            entry_id=entry_id,
            surrogates=surrogates,
            max_bytes=max_bytes,
        )
        return RestoreFileResponse.model_validate(result).model_dump(mode="json")

    @router.get("/restore/file/{file_id}")
    async def fetch_file(file_id: str) -> Response:
        """Bytes of the restored file for “Save As…”."""

        item = await call(restore.fetch_restored_file, services, file_id)
        return Response(
            content=item.content,
            media_type="application/octet-stream",
            headers={"X-Content-SHA256": item.sha256, "Cache-Control": "no-store"},
        )

    @router.delete("/restore/file/{file_id}", status_code=HTTP_204_NO_CONTENT)
    async def discard_file(file_id: str) -> Response:
        """“Remove” or “Empty”: the file disappears from memory."""

        await call(restore.discard_restored_file, services, file_id)
        return Response(status_code=HTTP_204_NO_CONTENT)

    return router


async def _read_single_upload(request: Request, *, max_bytes: int) -> Upload:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].casefold()
    if content_type != "multipart/form-data":
        raise HTTPException(
            status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Erlaubt ist nur multipart/form-data.",
        )
    try:
        async with request.form(max_files=1, max_fields=2, max_part_size=max_bytes) as form:
            parts = [part for part in form.getlist("file") if isinstance(part, UploadFile)]
            if len(parts) != 1:
                raise HTTPException(
                    status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Zurueckuebersetzen nimmt genau eine Datei an.",
                )
            raw = await parts[0].read(max_bytes + 1)
            name = parts[0].filename or "antwort.txt"
    except HTTPException:
        raise
    except (AssertionError, RuntimeError) as error:
        raise HTTPException(
            status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Multipart konnte nicht gelesen werden.",
        ) from error
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=HTTP_413_CONTENT_TOO_LARGE,
            detail={"code": "too_large", "message": "Die Datei ist zu gross."},
        )
    return Upload(name, raw)
