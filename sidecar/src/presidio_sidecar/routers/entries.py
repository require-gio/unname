"""History and entries: ``/entries*``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_202_ACCEPTED,
    HTTP_204_NO_CONTENT,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from .. import saving, workflow
from ..api_common import parse_model, read_json_object
from ..api_models import (
    CreateEntryRequest,
    EntryListResponse,
    EntryReportResponse,
    OccurrencesRequest,
    OccurrencesResponse,
    OutputsRequest,
    PatchEntryRequest,
    ProgressResponse,
    RedetectRequest,
    RedetectResponse,
    RedetectStatusResponse,
    ReplaceFileResponse,
    RetryFileResponse,
    SaveEntryRequest,
    SaveEntryResponse,
    StorageResponse,
    SubmitFilesResponse,
)
from ..audit import AuditError
from ..base_profile import base_profile
from ..drafts import DraftError, EntryView
from ..entries import Entry, EntryError, EntryNotFoundError
from ..profiles import ProfileRuleViolation
from ..services import PhaseOneServices
from ..settings_store import SettingsError
from ..vault import VaultCollisionError, VaultError

#: The sidecar accepts at most this many files per request; larger folders must be split.
MAX_FILES_PER_REQUEST = 200

#: State conflicts: request is valid but cannot be processed now. All other WorkflowError cases are
#: invalid (422).
_CONFLICTS = frozenset(
    {
        "wrong_kind",
        "still_processing",
        "files_expired",
        "files_unavailable",
        "nothing_to_save",
        "irreversible_confirmation_required",
        "outputs_mismatch",
        "retry_not_failed",
        "nothing_to_redetect",
    }
)


def build_router(services: PhaseOneServices) -> APIRouter:
    router = APIRouter()

    async def call(function: Any, *args: Any) -> Any:
        try:
            return await run_in_threadpool(function, *args)
        except EntryNotFoundError as error:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(error)) from error
        except workflow.WorkflowError as error:
            detail: dict[str, Any] = {"code": error.code, "message": str(error)}
            if isinstance(error, saving.SaveError) and error.file_id is not None:
                detail["file_id"] = error.file_id
            status = HTTP_409_CONFLICT if error.code in _CONFLICTS else 422
            if error.code == "pdf_check_failed":
                status = HTTP_503_SERVICE_UNAVAILABLE
            raise HTTPException(status_code=status, detail=detail) from error
        except ProfileRuleViolation as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": error.code, "message": str(error)},
            ) from error
        except VaultCollisionError as error:
            raise HTTPException(
                status_code=HTTP_409_CONFLICT,
                detail="Kürzel- oder Decknamenkollision; Speichern wurde abgebrochen.",
            ) from error
        except (VaultError, AuditError, OSError) as error:
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
                detail="Speichern konnte nicht sicher abgeschlossen werden.",
            ) from error
        except (EntryError, SettingsError) as error:
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
                detail="Der Verlauf ist nicht sicher verfuegbar.",
            ) from error
        except DraftError as error:
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
                detail="Der Arbeitsstand ist nicht sicher verfuegbar.",
            ) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
                detail="Analyse-Worker ist nicht verfuegbar; Sidecar-Neustart laeuft.",
            ) from error

    @router.get("/entries")
    async def list_entries() -> Any:
        def run() -> list[Entry]:
            services.entries()
            services.purge_expired_entries()
            return services.entries().list()

        entries = await call(run)
        return EntryListResponse(entries=entries, count=len(entries)).model_dump(mode="json")

    @router.post("/entries", status_code=HTTP_201_CREATED)
    async def create_entry(request: Request) -> Any:
        body = parse_model(CreateEntryRequest, await read_json_object(request, max_bytes=16384))

        def run() -> Entry:
            language = services.settings_store().get().general.language
            fallback = {"documents": ("Dokument", "Document"), "text": ("Text", "Text")}
            title = body.title or fallback[body.kind][0 if language == "de" else 1]
            return services.entries().create(
                kind=body.kind,
                masking=body.masking or workflow.default_masking(services),
                title=title,
                title_custom=body.title is not None,
                status="processing" if body.kind == "documents" else "review",
                # Document entries get their draft key at creation time.
                draft_key=services.drafts().create_key if body.kind == "documents" else None,
            )

        return (await call(run)).model_dump(mode="json")

    @router.delete("/entries", status_code=HTTP_204_NO_CONTENT)
    async def clear_entries(confirm: bool = False) -> Response:
        """Clear history – requires ``?confirm=true``."""

        if not confirm:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Verlauf leeren braucht die Bestaetigung confirm=true.",
            )

        await call(services.clear_entries)
        return Response(status_code=HTTP_204_NO_CONTENT)

    @router.get("/storage")
    async def storage() -> Any:
        """Total and per-entry storage usage."""

        def run() -> Any:
            services.entries()
            services.purge_expired_entries()
            return workflow.storage(services)

        return StorageResponse.model_validate(await call(run)).model_dump(mode="json")

    @router.get("/entries/{entry_id}")
    async def get_entry(entry_id: str) -> Any:
        return (await call(workflow.sync_entry, services, entry_id)).model_dump(mode="json")

    @router.patch("/entries/{entry_id}")
    async def patch_entry(entry_id: str, request: Request) -> Any:
        body = parse_model(PatchEntryRequest, await read_json_object(request, max_bytes=16384))

        def run() -> Entry:
            def change(current: Entry) -> Entry:
                update: dict[str, Any] = {}
                if body.title is not None:
                    update.update(title=body.title, title_custom=True)
                if body.masking is not None:
                    update["masking"] = body.masking
                if body.status is not None:
                    update["status"] = body.status
                return current.model_copy(update=update)

            return services.entries().update(entry_id, change)

        return (await call(run)).model_dump(mode="json")

    @router.get("/entries/{entry_id}/view")
    async def get_view(entry_id: str) -> Any:
        view = await call(workflow.get_view, services, entry_id)
        return view.model_dump(mode="json") if view is not None else None

    @router.put("/entries/{entry_id}/view")
    async def put_view(entry_id: str, request: Request) -> Any:
        body = parse_model(EntryView, await read_json_object(request, max_bytes=4096))
        return (await call(workflow.put_view, services, entry_id, body)).model_dump(mode="json")

    @router.delete("/entries/{entry_id}", status_code=HTTP_204_NO_CONTENT)
    async def delete_entry(entry_id: str) -> Response:
        """Delete entry together with jobs, saved originals and intermediate data."""

        await call(services.delete_entry, entry_id)
        return Response(status_code=HTTP_204_NO_CONTENT)

    @router.post("/entries/{entry_id}/files", status_code=HTTP_202_ACCEPTED)
    async def submit_files(entry_id: str, request: Request) -> Any:
        """Submit file(s): multipart with one or more ``file`` parts.

        The sidecar deliberately does not accept paths (spec §8): the shell reads the files
        from a real dialog or dropzone and passes the bytes onward. Accepting a path would let
        anyone with the token read arbitrary device files, which the macOS sandbox also blocks.
        """

        uploads = await _read_uploads(request)

        def run() -> Any:
            entry, accepted, rejected = workflow.submit_files(services, entry_id, uploads)
            return SubmitFilesResponse(
                entry=entry,
                accepted=accepted,
                rejected=[
                    {"name": item.name, "code": item.code, "message": item.message}
                    for item in rejected
                ],
            )

        return (await call(run)).model_dump(mode="json")

    @router.get("/entries/{entry_id}/progress")
    async def entry_progress(entry_id: str) -> Any:
        result = await call(workflow.progress, services, entry_id)
        return ProgressResponse.model_validate(result).model_dump(mode="json")

    @router.post("/entries/{entry_id}/occurrences")
    async def entry_occurrences(entry_id: str, request: Request) -> Any:
        body = parse_model(OccurrencesRequest, await read_json_object(request, max_bytes=4096))

        def run() -> Any:
            return workflow.occurrences(services, entry_id, body.text, ignore_case=body.ignore_case)

        return OccurrencesResponse.model_validate(await call(run)).model_dump(mode="json")

    @router.post("/entries/{entry_id}/files/{file_id}/replace", status_code=HTTP_202_ACCEPTED)
    async def replace_file(entry_id: str, file_id: str, request: Request) -> Any:
        """Select original: existing version or unreadable draft.

        Only raw bytes are sent, never a path; exactly one ``file``.
        """

        uploads = await _read_uploads(request)
        if len(uploads) != 1:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Ersetzen nimmt genau eine Datei an.",
            )

        def run() -> Any:
            entry, item, changed = workflow.replace_file(services, entry_id, file_id, uploads[0])
            return ReplaceFileResponse(entry=entry, file=item, changed=changed)

        return (await call(run)).model_dump(mode="json")

    @router.post("/entries/{entry_id}/files/{file_id}/retry", status_code=HTTP_202_ACCEPTED)
    async def retry_file(entry_id: str, file_id: str) -> Any:
        entry, item = await call(workflow.retry_file, services, entry_id, file_id)
        return RetryFileResponse(entry=entry, file=item).model_dump(mode="json")

    @router.get("/entries/{entry_id}/redetect")
    async def redetect_status(entry_id: str) -> Any:
        """Files that can be re-detected: detection no longer matches."""

        files = await call(workflow.redetect_status, services, entry_id)
        return RedetectStatusResponse(entry_id=entry_id, files=files).model_dump(mode="json")

    @router.post("/entries/{entry_id}/redetect", status_code=HTTP_202_ACCEPTED)
    async def redetect(entry_id: str, request: Request) -> Any:
        """Re-detect from saved state – never automatic."""

        body = parse_model(RedetectRequest, await read_json_object(request, max_bytes=65536))
        entry, files = await call(workflow.redetect_files, services, entry_id, body.file_ids)
        return RedetectResponse(entry=entry, files=files).model_dump(mode="json")

    @router.delete("/entries/{entry_id}/files/{file_id}")
    async def remove_file(entry_id: str, file_id: str) -> Any:
        entry = await call(workflow.remove_file, services, entry_id, file_id)
        return entry.model_dump(mode="json")

    @router.post("/entries/{entry_id}/save")
    async def save_entry(entry_id: str, request: Request) -> Any:
        """Save all files: export final state, commit mappings.

        Files are placed in a private deployment folder; the shell validates,
        publishes them and reports back via ``/outputs``.
        """

        body = parse_model(
            SaveEntryRequest,
            await read_json_object(request, max_bytes=4 * 1024 * 1024),
        )
        result = await call(saving.save_entry, services, entry_id, body)
        return SaveEntryResponse.model_validate(result).model_dump(mode="json")

    @router.post("/entries/{entry_id}/outputs")
    async def record_outputs(entry_id: str, request: Request) -> Any:
        body = parse_model(OutputsRequest, await read_json_object(request, max_bytes=256 * 1024))
        return (await call(saving.record_outputs, services, entry_id, body)).model_dump(mode="json")

    @router.get("/entries/{entry_id}/report")
    async def entry_report(entry_id: str, export_id: str | None = None) -> Any:
        """Review report of the latest save or one from the export history."""

        result = await call(saving.entry_report, services, entry_id, export_id)
        return EntryReportResponse.model_validate(result).model_dump(mode="json")

    return router


async def _read_uploads(request: Request) -> list[workflow.Upload]:
    max_bytes = base_profile().max_file_bytes
    content_type = request.headers.get("content-type", "").split(";", 1)[0].casefold()
    if content_type != "multipart/form-data":
        raise HTTPException(
            status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Erlaubt ist nur multipart/form-data.",
        )
    uploads: list[workflow.Upload] = []
    try:
        async with request.form(
            max_files=MAX_FILES_PER_REQUEST,
            max_fields=4,
            max_part_size=max_bytes,
        ) as form:
            for part in form.getlist("file"):
                if not isinstance(part, UploadFile):
                    continue
                raw = await part.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise HTTPException(
                        status_code=HTTP_413_CONTENT_TOO_LARGE,
                        detail="Eine Datei ist zu gross.",
                    )
                uploads.append(workflow.Upload(part.filename or "dokument", raw))
    except HTTPException:
        raise
    except (AssertionError, RuntimeError) as error:
        raise HTTPException(
            status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Multipart konnte nicht gelesen werden.",
        ) from error
    if not uploads:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Multipart braucht mindestens ein Dateifeld 'file'.",
        )
    return uploads
