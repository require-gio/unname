"""Text mode: ``POST /text/mask`` (live, no save) and ``/text/commit``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.status import (
    HTTP_409_CONFLICT,
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from .. import workflow
from ..api_common import parse_model, read_json_object
from ..api_models import TextCommitRequest, TextCommitResponse, TextMaskRequest, TextMaskResponse
from ..config import Settings
from ..entries import EntryError
from ..processing import ProcessingError, ProcessingValidationError
from ..services import PhaseOneServices
from ..vault import VaultCollisionError, VaultError


def build_router(services: PhaseOneServices, settings: Settings) -> APIRouter:
    router = APIRouter()
    max_bytes = settings.max_text_bytes + 64 * 1024

    @router.post("/text/mask")
    async def text_mask(request: Request) -> Any:
        """Detect and mask a text without persisting anything.

        The UI calls this on each input and cancels the previous call.
        Detection and processing run in a thread pool so the live check can
        respond while work is ongoing.
        """

        body = parse_model(TextMaskRequest, await read_json_object(request, max_bytes=max_bytes))
        result = await _run(workflow.text_mask, services, body)
        return TextMaskResponse.model_validate(result).model_dump(mode="json")

    @router.post("/text/commit")
    async def text_commit(request: Request) -> Any:
        """“Copy”: write assignments and add a history entry “Text · copied”."""

        body = parse_model(TextCommitRequest, await read_json_object(request, max_bytes=max_bytes))
        result = await _run(workflow.text_commit, services, body)
        return TextCommitResponse.model_validate(result).model_dump(mode="json")

    return router


async def _run(function: Any, services: PhaseOneServices, body: Any) -> dict[str, Any]:
    try:
        return await run_in_threadpool(function, services, body)
    except workflow.WorkflowError as error:
        status = (
            HTTP_413_CONTENT_TOO_LARGE
            if error.code == "too_large"
            else HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            status_code=status, detail={"code": error.code, "message": str(error)}
        ) from error
    except VaultCollisionError as error:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail="Kürzel- oder Decknamenkollision; Verarbeitung wurde abgebrochen.",
        ) from error
    except ProcessingValidationError as error:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except (OSError, ProcessingError, VaultError, EntryError) as error:
        raise HTTPException(
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
            detail="Die lokale Verarbeitung konnte nicht sicher abgeschlossen werden.",
        ) from error
