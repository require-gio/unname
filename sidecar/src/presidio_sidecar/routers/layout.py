"""Layout map and page images for the review view.

- ``GET /jobs/{id}/layout``: location of each word in the rendered document.
- ``GET /jobs/{id}/pages/{n}?scale=``: page image as PNG.
- ``GET /jobs/{id}/pages/{n}/result?scale=``: control image – same page after real
  editing in memory, for the last applied state (tests, A-26). PDF and image.
- ``GET /jobs/{id}/layout/document``: preview copy of a DOCX with anchors per
  text unit, only for ``kind = "flow"``.
- ``GET /jobs/{id}/layout/sheets/{i}?rows=a-b&lang=``: cells of a sheet from the
  table model, only for ``kind = "grid"``.
- ``GET /jobs/{id}/layout/paged?side=original|result``: word-faithful view –
  pages and words from the PDF of the Office engine; only for ``kind = "flow"``
  and only when the engine is available (``paged``).
- ``GET /jobs/{id}/layout/paged/{n}?side=&revision=&scale=``: page image for it.

All data stays in clear text and remains only in the sidecar’s memory,
extracted with a token and never cached (``Cache-Control: no-store``).
"""

from __future__ import annotations

import json
import threading
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from starlette.concurrency import run_in_threadpool
from starlette.status import HTTP_409_CONFLICT, HTTP_422_UNPROCESSABLE_CONTENT

from ..api_models import LayoutPagedResponse, LayoutResponse, LayoutSheetRowsResponse
from ..document_workflow import (
    DocumentWorkflowError,
    docx_result_bytes,
    image_result_redactions,
    pdf_result_redactions,
)
from ..docx_preview import build_docx_preview
from ..extraction import DocumentFormat
from ..grid_model import (
    GRID_BLOCK_ROWS,
    GridModel,
    Language,
    build_grid,
    cell_blocks,
    grid_rows,
    is_grid_document,
)
from ..jobs import JobNotFoundError, JobNotReadyError
from ..layout import LayoutError, build_layout, docx_unit_segments
from ..office_engine import OfficeEngineError, docx_to_pdf, office_engine
from ..paged_docx import Side, paged_layout, side_units
from ..rendering import (
    RenderError,
    render_page_png,
    render_pdf_bytes_png,
    render_result_image_png,
    render_result_page_png,
)
from ..services import PhaseOneServices

_NO_STORE = {"Cache-Control": "no-store"}
_LAYOUT_KEY = ("layout",)
_DOCX_PREVIEW_KEY = ("docx_preview",)
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class PagedUnavailableError(LookupError):
    """No page-faithful view without the Office engine."""


class StaleRevisionError(RuntimeError):
    """The page image belongs to a state that is no longer valid."""


def build_router(services: PhaseOneServices) -> APIRouter:
    router = APIRouter()
    # One conversion per page for both view and state: map and page images
    # are requested together, conversion happens only once.
    paged_lock = threading.Lock()

    def max_image_pixels() -> int:
        return services.extractor().max_image_pixels

    def layout_of(job_id: str) -> dict[str, Any]:
        """Layout map from cache or freshly from extraction and source file."""

        cache = services.job_memory()
        cached = cache.get(job_id, _LAYOUT_KEY)
        if cached is not None:
            return json.loads(cached)
        manager = services.jobs()
        # The lease holds the source file and job while computation runs.
        with manager.lease(job_id) as record:
            document = record.extracted_document
            if document is not None and is_grid_document(document):
                layout = grid_of(job_id, document, record.source_path)
            else:
                layout = build_layout(
                    job_id,
                    document,
                    record.source_path,
                    max_image_pixels=max_image_pixels(),
                )
        if layout.get("kind") == "flow":
            layout["paged"] = office_engine() is not None
        payload = LayoutResponse.model_validate(layout).model_dump(mode="json")
        cache.put(job_id, _LAYOUT_KEY, json.dumps(payload, separators=(",", ":")).encode())
        return payload

    def grid_of(job_id: str, document: Any, source_path: Any) -> dict[str, Any]:
        """Compute table model; cells are stored blockwise in the cache."""

        grid = build_grid(job_id, document, source_path)
        if grid is None:
            return {"job_id": job_id, "kind": "text", "offset_encoding": "utf-16"}
        cache_grid(job_id, grid)
        return grid.layout

    def cache_grid(job_id: str, grid: GridModel) -> None:
        cache = services.job_memory()
        for sheet, cells in enumerate(grid.cells):
            blocks = cell_blocks(cells)
            rows = grid.layout["sheets"][sheet]["rows"]
            for block in range(rows // GRID_BLOCK_ROWS + 1):
                payload = json.dumps(blocks.get(block, []), separators=(",", ":")).encode()
                cache.put(job_id, ("grid", sheet, block), payload)

    def sheet_rows(
        job_id: str, sheet: int, first: int, last: int, language: Language
    ) -> dict[str, Any]:
        layout = layout_of(job_id)
        if layout["kind"] != "grid":
            raise RenderError("Diese Datei hat kein Tabellenmodell.")
        sheets = layout["sheets"]
        if not 0 <= sheet < len(sheets):
            raise LookupError("Dieses Blatt gibt es nicht.")
        last = min(last, max(0, sheets[sheet]["rows"] - 1))
        cells: list[Any] = []
        for block in range(first // GRID_BLOCK_ROWS, last // GRID_BLOCK_ROWS + 1):
            cached = cache_block(job_id, sheet, block)
            cells.extend(grid_rows(cached, first, last, language))
        return LayoutSheetRowsResponse(
            job_id=job_id, sheet=sheet, first=first, last=last, cells=cells
        ).model_dump(mode="json")

    def cache_block(job_id: str, sheet: int, block: int) -> list[Any]:
        cached = services.job_memory().get(job_id, ("grid", sheet, block))
        if cached is not None:
            return json.loads(cached)
        # Cache miss or too large: recompute the model.
        with services.jobs().lease(job_id) as record:
            document = record.extracted_document
            if document is None or not is_grid_document(document):
                raise RenderError("Die Extraktion fehlt.")
            grid = build_grid(job_id, document, record.source_path)
        if grid is None:
            raise RenderError("Das Blatt ist zu gross fuer das Raster.")
        cache_grid(job_id, grid)
        return cell_blocks(grid.cells[sheet]).get(block, [])

    def checked_scale(job_id: str, page: int, scale: float) -> None:
        layout = layout_of(job_id)
        if layout["kind"] != "pages":
            raise RenderError("Diese Datei hat keine festen Seiten.")
        pages = layout["pages"]
        if not 0 <= page < len(pages):
            raise LookupError("Diese Seite gibt es nicht.")
        if scale not in pages[page]["scales"]:
            raise ValueError("Diesen Massstab gibt es fuer diese Seite nicht.")

    def page_png(job_id: str, page: int, scale: float) -> bytes:
        checked_scale(job_id, page, scale)
        cache = services.job_memory()
        key = ("page", page, scale)
        cached = cache.get(job_id, key)
        if cached is not None:
            return cached
        manager = services.jobs()
        with manager.lease(job_id) as record:
            document = record.extracted_document
            if document is None:
                raise RenderError("Die Extraktion fehlt.")
            image = render_page_png(
                record.source_path,
                document.format,
                page,
                scale,
                max_image_pixels=max_image_pixels(),
            )
        cache.put(job_id, key, image)
        return image

    def result_png(job_id: str, page: int, scale: float) -> bytes:
        checked_scale(job_id, page, scale)
        manager = services.jobs()
        snapshot = manager.get_processing_snapshot(job_id)
        cache = services.job_memory()
        key = ("result", snapshot.revision, page, scale)
        cached = cache.get(job_id, key)
        if cached is not None:
            return cached
        text = manager.get_source_text(job_id)
        with manager.lease(job_id) as record:
            document = record.extracted_document
            if document is not None and document.format in {
                DocumentFormat.PNG,
                DocumentFormat.JPEG,
            }:
                if page != 0:
                    raise RenderError("Ein Bild hat genau eine Seite.")
                image = render_result_image_png(
                    record.source_path,
                    image_result_redactions(document, text, snapshot.result),
                    scale,
                    max_image_pixels=max_image_pixels(),
                )
            else:
                image = render_result_page_png(
                    record.source_path,
                    pdf_result_redactions(document, text, snapshot.result, record.source_path),
                    page,
                    scale,
                    max_image_pixels=max_image_pixels(),
                )
        cache.put(job_id, key, image)
        return image

    def docx_preview(job_id: str) -> bytes:
        layout = layout_of(job_id)
        if layout["kind"] != "flow":
            raise RenderError("Diese Datei hat keine Vorschau-Kopie.")
        cache = services.job_memory()
        cached = cache.get(job_id, _DOCX_PREVIEW_KEY)
        if cached is not None:
            return cached
        manager = services.jobs()
        with manager.lease(job_id) as record:
            document = record.extracted_document
            if document is None:
                raise RenderError("Die Extraktion fehlt.")
            preview = build_docx_preview(
                record.source_path,
                [document.text[s.start : s.end] for s in docx_unit_segments(document)],
            )
        cache.put(job_id, _DOCX_PREVIEW_KEY, preview)
        return preview

    def paged_state(job_id: str, side: Side) -> tuple[int, Any]:
        """Page apply state: always 0 for original, last applied for result."""

        if side == "original":
            return 0, None
        snapshot = services.jobs().get_processing_snapshot(job_id)
        return snapshot.revision, snapshot.result

    def paged_pdf(job_id: str, side: Side, revision: int | None = None) -> tuple[int, bytes, Any]:
        """PDF from the Office engine for a view page, including state and apply result."""

        if office_engine() is None:
            raise PagedUnavailableError("Fuer diese Ansicht fehlt die Office-Engine.")
        if layout_of(job_id)["kind"] != "flow":
            raise RenderError("Diese Datei hat keine seitentreue Ansicht.")
        cache = services.job_memory()
        with paged_lock:
            current, processing = paged_state(job_id, side)
            wanted = current if revision is None else revision
            cached = cache.get(job_id, ("paged", side, wanted))
            if cached is not None:
                return wanted, cached, processing if wanted == current else None
            if wanted != current:
                raise StaleRevisionError("Dieser Stand gilt nicht mehr.")
            manager = services.jobs()
            text = manager.get_source_text(job_id)
            with manager.lease(job_id) as record:
                document = record.extracted_document
                if document is None:
                    raise RenderError("Die Extraktion fehlt.")
                docx = docx_result_bytes(
                    record.source_path,
                    document=document,
                    source_text=text,
                    processing=processing,
                )
            engine = office_engine()
            if engine is None:
                raise PagedUnavailableError("Fuer diese Ansicht fehlt die Office-Engine.")
            pdf = docx_to_pdf(engine, docx)
            cache.put(job_id, ("paged", side, current), pdf)
            return current, pdf, processing

    def paged_of(job_id: str, side: Side) -> dict[str, Any]:
        revision, pdf, processing = paged_pdf(job_id, side)
        cache = services.job_memory()
        key = ("paged-layout", side, revision)
        cached = cache.get(job_id, key)
        if cached is not None:
            return json.loads(cached)
        manager = services.jobs()
        text = manager.get_source_text(job_id)
        with manager.lease(job_id) as record:
            document = record.extracted_document
            if document is None:
                raise RenderError("Die Extraktion fehlt.")
            units = side_units(document, text, side, processing)
        engine = office_engine()
        if engine is None:
            raise PagedUnavailableError("Fuer diese Ansicht fehlt die Office-Engine.")
        layout = paged_layout(pdf, units, text, max_image_pixels=max_image_pixels())
        payload = LayoutPagedResponse(
            job_id=job_id,
            side=side,
            revision=revision,
            engine={"name": engine.name, "version": engine.version},
            pages=layout["pages"],
            words=layout["words"],
            unplaced=layout["unplaced"],
        ).model_dump(mode="json")
        cache.put(job_id, key, json.dumps(payload, separators=(",", ":")).encode())
        return payload

    def paged_png(job_id: str, side: Side, revision: int, page: int, scale: float) -> bytes:
        cache = services.job_memory()
        key = ("paged-page", side, revision, page, scale)
        cached = cache.get(job_id, key)
        if cached is not None:
            return cached
        _, pdf, _ = paged_pdf(job_id, side, revision)
        image = render_pdf_bytes_png(pdf, page, scale, max_image_pixels=max_image_pixels())
        cache.put(job_id, key, image)
        return image

    @router.get("/jobs/{job_id}/layout/paged")
    async def job_layout_paged(
        job_id: str,
        side: Literal["original", "result"] = Query(default="result"),
    ) -> Response:
        try:
            payload = await run_in_threadpool(paged_of, job_id, side)
        except (JobNotFoundError, PagedUnavailableError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except JobNotReadyError as error:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(error)) from error
        except (LayoutError, RenderError, OfficeEngineError, DocumentWorkflowError) as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        return Response(
            content=json.dumps(payload, separators=(",", ":")),
            media_type="application/json",
            headers=_NO_STORE,
        )

    @router.get("/jobs/{job_id}/layout/paged/{page}")
    async def job_layout_paged_page(
        job_id: str,
        page: int,
        side: Literal["original", "result"] = Query(default="result"),
        revision: int = Query(default=0, ge=0),
        scale: float = Query(default=2.0, gt=0, le=4),
    ) -> Response:
        try:
            image = await run_in_threadpool(paged_png, job_id, side, revision, page, scale)
        except (JobNotFoundError, PagedUnavailableError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (JobNotReadyError, StaleRevisionError) as error:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(error)) from error
        except (
            LayoutError,
            RenderError,
            OfficeEngineError,
            DocumentWorkflowError,
        ) as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        return Response(content=image, media_type="image/png", headers=_NO_STORE)

    @router.get("/jobs/{job_id}/layout/document")
    async def job_layout_document(job_id: str) -> Response:
        try:
            preview = await run_in_threadpool(docx_preview, job_id)
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except JobNotReadyError as error:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(error)) from error
        except (LayoutError, RenderError) as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        return Response(content=preview, media_type=_DOCX_MEDIA_TYPE, headers=_NO_STORE)

    @router.get("/jobs/{job_id}/layout/sheets/{sheet}")
    async def job_layout_sheet(
        job_id: str,
        sheet: int,
        rows: str = Query(default="0-499", pattern=r"^\d{1,7}-\d{1,7}$"),
        lang: str = Query(default="de", pattern=r"^(de|en)$"),
    ) -> Response:
        first, last = (int(value) for value in rows.split("-"))
        language: Language = "en" if lang == "en" else "de"
        if last < first or last - first >= 4 * GRID_BLOCK_ROWS:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Hoechstens {4 * GRID_BLOCK_ROWS} Zeilen je Abruf.",
            )
        try:
            payload = await run_in_threadpool(sheet_rows, job_id, sheet, first, last, language)
        except (JobNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except JobNotReadyError as error:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(error)) from error
        except (LayoutError, RenderError) as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        return Response(
            content=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            media_type="application/json",
            headers=_NO_STORE,
        )

    @router.get("/jobs/{job_id}/layout")
    async def job_layout(job_id: str) -> Any:
        try:
            payload = await run_in_threadpool(layout_of, job_id)
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except JobNotReadyError as error:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(error)) from error
        except LayoutError as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        return Response(
            content=json.dumps(payload, separators=(",", ":")),
            media_type="application/json",
            headers=_NO_STORE,
        )

    @router.get("/jobs/{job_id}/pages/{page}")
    async def job_page(
        job_id: str,
        page: int,
        scale: float = Query(default=2.0, gt=0, le=4),
    ) -> Response:
        try:
            image = await run_in_threadpool(page_png, job_id, page, scale)
        except (JobNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except JobNotReadyError as error:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(error)) from error
        except (ValueError, LayoutError, RenderError) as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        return Response(content=image, media_type="image/png", headers=_NO_STORE)

    @router.get("/jobs/{job_id}/pages/{page}/result")
    async def job_page_result(
        job_id: str,
        page: int,
        scale: float = Query(default=2.0, gt=0, le=4),
    ) -> Response:
        try:
            image = await run_in_threadpool(result_png, job_id, page, scale)
        except (JobNotFoundError, LookupError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except JobNotReadyError as error:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(error)) from error
        except (ValueError, LayoutError, RenderError, DocumentWorkflowError) as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        return Response(content=image, media_type="image/png", headers=_NO_STORE)

    return router
