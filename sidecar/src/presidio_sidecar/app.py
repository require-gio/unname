"""FastAPI application for the sidecar."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.status import (
    HTTP_204_NO_CONTENT,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from . import PROTOCOL_VERSION, __version__, workflow
from .api_common import parse_model
from .api_common import read_json_object as _read_json_object
from .api_models import ApplyRequest, ApplyResponse
from .config import Settings
from .diagnostics import DiagnosticsRecorder
from .entries import EntryError
from .findings_view import detection_for_profile as _detection_for_profile
from .findings_view import serialize_detection as _serialize_detection_result
from .jobs import JobNotFoundError, JobNotReadyError
from .processing import (
    ProcessingError,
    ProcessingValidationError,
)
from .resources import use_models_directory
from .routers import catalog as catalog_router
from .routers import entries as entries_router
from .routers import layout as layout_router
from .routers import models as models_router
from .routers import restore as restore_router
from .routers import settings as settings_router
from .routers import text as text_router
from .routers import vault as vault_router
from .routers import vocabulary as vocabulary_router
from .runtime import health_report
from .security import BearerTokenMiddleware
from .services import PhaseOneServices
from .vault import (
    VaultCollisionError,
    VaultError,
)


def create_app(
    settings: Settings,
    diagnostics: DiagnosticsRecorder | None = None,
    *,
    on_started: Callable[[], None] | None = None,
    on_stopping: Callable[[], None] | None = None,
    phase_one: PhaseOneServices | None = None,
) -> FastAPI:
    """Build the application.

    ``on_started`` runs as soon as the service accepts connections — that is
    where the handshake line hangs. Reported earlier, the harness would
    query a still-closed port.
    """
    recorder = diagnostics if diagnostics is not None else DiagnosticsRecorder()
    # Before the first request: detection looks for its models here first.
    use_models_directory(settings.models_directory)
    services = phase_one or PhaseOneServices(settings, recorder)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        services.start()
        if on_started is not None:
            on_started()
        try:
            yield
        finally:
            services.close()
            if on_stopping is not None:
                on_stopping()

    app = FastAPI(
        title="presidio-sidecar",
        version=__version__,
        lifespan=lifespan,
        # No interactive docs: the service has exactly one caller.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.diagnostics = recorder
    app.state.phase_one = services

    # Order matters: Starlette runs the last added
    # middleware first. CORS must therefore be
    # registered *after* the token check so that
    # preflight requests — which cannot carry an
    # Authorization header — are answered first.
    app.add_middleware(BearerTokenMiddleware, token=settings.token)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    async def health() -> Any:
        """Model and version info – used for reachability checks.

        Implemented as a cheap async endpoint: it only answers the
        question "is this service alive?" without acquiring locks or
        building resources. A busy service is not a broken one; confusing
        the two would block the interface during normal operation.
        """
        snapshot = services.health_snapshot()
        worker_health = snapshot["job_worker"]
        report = {
            **health_report(
                nlp_engine=snapshot["nlp_engine"],
                master_data=snapshot["master_data"],
                workers=snapshot["workers"],
                device=snapshot["device"],
            ),
            "protocol": PROTOCOL_VERSION,
            "job_worker": worker_health,
            "warming_up": snapshot["warming_up"],
            "busy": snapshot["busy"],
            "models": snapshot["models"],
            "setup_required": snapshot["setup_required"],
            "ocr": snapshot["ocr"],
        }
        if not worker_health["healthy"] or not snapshot["master_data"]["usable"]:
            report["status"] = "error"
            return JSONResponse(report, status_code=HTTP_503_SERVICE_UNAVAILABLE)
        return report

    @app.get("/diagnostics")
    def diagnostics_report() -> dict[str, Any]:
        """Session operational data — no document contents (spec §10)."""
        entries = recorder.as_dicts()
        return {
            "entries": entries,
            "count": len(entries),
            "fallbacks": sum(1 for entry in entries if entry["category"].endswith("_fallback")),
        }

    @app.get("/jobs/{job_id}/findings")
    def job_findings(job_id: str) -> dict[str, Any]:
        manager = services.jobs()
        try:
            record = manager.get(job_id)
            result = manager.get_result(job_id)
            text = manager.get_source_text(job_id)
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except JobNotReadyError as error:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(error)) from error
        if record.profile_id is not None:
            profile = services.profile(record.profile_id)
            result = _detection_for_profile(result, profile)
        else:
            profile = None
        if record.entry_id is not None:
            services.jobs().touch((job_id,))
        return _serialize_detection_result(
            job_id,
            text,
            result,
            profile=profile,
            document=record.extracted_document,
        )

    app.include_router(catalog_router.build_router())
    app.include_router(settings_router.build_router(services))
    app.include_router(entries_router.build_router(services))
    app.include_router(layout_router.build_router(services))
    app.include_router(text_router.build_router(services, settings))
    app.include_router(restore_router.build_router(services, settings))
    app.include_router(vault_router.build_router(services))
    app.include_router(vocabulary_router.build_router(services))
    app.include_router(models_router.build_router())

    @app.post("/jobs/{job_id}/apply")
    async def apply_job(job_id: str, request: Request) -> dict[str, Any]:
        """Apply masking and review state to a
        completed job (API v2).

        Body ``{ masking, review }``: a dry-run
        without write access to the translation
        table. Only ``/entries/{id}/save`` or
        ``/text/commit`` write mappings.
        """

        payload = await _read_json_object(
            request,
            max_bytes=min(settings.max_text_bytes, 1024 * 1024),
        )
        body = parse_model(ApplyRequest, payload)
        revision: int | None = None
        committed = False
        try:
            revision = await run_in_threadpool(services.jobs().begin_processing, job_id)
            result = await run_in_threadpool(workflow.apply_job, services, job_id, body, revision)
            committed = True
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except JobNotReadyError as error:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(error)) from error
        except workflow.WorkflowError as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": error.code, "message": str(error)},
            ) from error
        except VaultCollisionError as error:
            raise HTTPException(
                status_code=HTTP_409_CONFLICT,
                detail="Kürzel- oder Decknamenkollision; Verarbeitung wurde abgebrochen.",
            ) from error
        except ProcessingValidationError as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error
        except (OSError, ProcessingError, VaultError, EntryError) as error:
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
                detail="Die lokale Verarbeitung konnte nicht sicher abgeschlossen werden.",
            ) from error
        finally:
            if revision is not None and not committed:
                with suppress(JobNotFoundError):
                    await run_in_threadpool(
                        services.jobs().abandon_processing,
                        job_id,
                        revision,
                    )
        return ApplyResponse.model_validate(result).model_dump(mode="json")

    @app.delete("/jobs/{job_id}", status_code=HTTP_204_NO_CONTENT)
    def delete_job(job_id: str) -> None:
        """Cancel a running job ("Abbrechen") or remove a completed one from memory.

        For files belonging to an entry the draft state is kept; deletion only occurs
        via the entry (``DELETE /entries/{id}``).
        """

        try:
            services.jobs().cancel(job_id)
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except JobNotReadyError as error:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(error)) from error

    return app
