"""Names & terms: ``/lists``, ``/terms``, ``/patterns``, ``/templates``.

Name lists are received from the shell as raw bytes, like documents. The shell reads the selected
file
and forwards it as multipart data. The sidecar does not know the file path; the "Refresh" button in
the
shell triggers a new read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_404_NOT_FOUND,
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from .. import templates
from ..analysis import _run_recognizers
from ..api_common import parse_model, read_json_object, utf16_offset
from ..api_models import (
    ConfiguredNames,
    ListsResponse,
    NameListOut,
    PatternsRequest,
    PatternsResponse,
    PatternTestRequest,
    PatternTestResponse,
    SaveTemplateRequest,
    TemplateApplyResponse,
    TemplateExportRequest,
    TemplateExportResponse,
    TemplateImportRequest,
    TemplateOut,
    TemplatesResponse,
    TermsRequest,
    TermsResponse,
)
from ..catalog import placeholder_for
from ..detection import FindingSource
from ..detection.spans import resolve_overlaps
from ..registry import RegistryError
from ..services import PhaseOneServices
from ..settings_store import SettingsError
from ..vocabulary import (
    ListKind,
    PatternTimeoutError,
    Template,
    VocabularyError,
    VocabularyNotFoundError,
    VocabularyValidationError,
    list_recognizers,
    parse_name_list,
    pattern_recognizers,
    term_recognizers,
    validate_patterns,
)

# A name list from the ERP can be large, but must have a reasonable size.
MAX_LIST_BYTES = 16 * 1024 * 1024
_KINDS: frozenset[str] = frozenset({"persons", "organizations"})


def build_router(services: PhaseOneServices) -> APIRouter:
    router = APIRouter()

    async def call(function: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except VocabularyNotFoundError as error:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(error)) from error
        except VocabularyValidationError as error:
            detail: dict[str, Any] = {"code": error.code, "message": str(error)}
            if error.index is not None:
                detail["index"] = error.index
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT, detail=detail
            ) from error
        except PatternTimeoutError as error:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "pattern_slow", "message": str(error)},
            ) from error
        except (VocabularyError, SettingsError, RegistryError, OSError) as error:
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
                detail="Namen & Begriffe sind nicht sicher verfuegbar.",
            ) from error

    # -- Name lists --------------------------------------------------------

    def lists_payload(created: str | None = None) -> dict[str, Any]:
        store = services.vocabulary()
        now = datetime.now(UTC)
        configured = services.master_data_status()
        return ListsResponse(
            lists=[
                NameListOut(
                    id=item.id,
                    kind=item.kind,
                    source_name=item.source_name,
                    count=len(item.values),
                    imported_at=item.imported_at,
                    updated_at=item.updated_at,
                    stale=store.is_stale(item, now=now),
                )
                for item in store.lists()
            ],
            configured=(
                ConfiguredNames(
                    count=int(configured["total_count"]),
                    updated_at=configured.get("updated_at"),
                )
                if configured.get("total_count")
                else None
            ),
            created=created,
        ).model_dump(mode="json")

    @router.get("/lists")
    async def get_lists() -> Any:
        return await call(lists_payload)

    @router.post("/lists", status_code=HTTP_201_CREATED)
    async def add_list(request: Request, kind: str, modified: int | None = None) -> Any:
        """Create a new name list; ``kind`` = ``persons`` | ``organizations``.

        ``modified`` is the source file's modification time in milliseconds –
        the list's state.
        """

        list_kind = _kind(kind)
        name, content = await _read_list_upload(request)

        def run() -> Any:
            values = parse_name_list(name, content, list_kind)
            item = services.vocabulary().add_list(
                kind=list_kind,
                source_name=name,
                values=values,
                source_modified_at=_modified(modified),
            )
            return lists_payload(created=item.id)

        return await call(run)

    @router.post("/lists/{list_id}/refresh")
    async def refresh_list(request: Request, list_id: str, modified: int | None = None) -> Any:
        name, content = await _read_list_upload(request)

        def run() -> Any:
            store = services.vocabulary()
            current = next((item for item in store.lists() if item.id == list_id), None)
            if current is None:
                raise VocabularyNotFoundError("Namensliste nicht gefunden.")
            values = parse_name_list(name, content, current.kind)
            store.refresh_list(
                list_id,
                source_name=name,
                values=values,
                source_modified_at=_modified(modified),
            )
            return lists_payload()

        return await call(run)

    @router.delete("/lists/{list_id}", status_code=HTTP_204_NO_CONTENT)
    async def delete_list(list_id: str) -> Response:
        await call(services.vocabulary().delete_list, list_id)
        return Response(status_code=HTTP_204_NO_CONTENT)

    # -- Terms and Patterns -------------------------------------------------

    @router.get("/terms")
    async def get_terms() -> Any:
        def run() -> Any:
            return TermsResponse(terms=list(services.vocabulary().terms())).model_dump(mode="json")

        return await call(run)

    @router.put("/terms")
    async def put_terms(request: Request) -> Any:
        body = parse_model(TermsRequest, await read_json_object(request, max_bytes=256 * 1024))

        def run() -> Any:
            terms = services.vocabulary().set_terms(body.terms)
            return TermsResponse(terms=list(terms)).model_dump(mode="json")

        return await call(run)

    @router.get("/patterns")
    async def get_patterns() -> Any:
        def run() -> Any:
            patterns = services.vocabulary().patterns()
            return PatternsResponse(patterns=list(patterns)).model_dump(mode="json")

        return await call(run)

    @router.put("/patterns")
    async def put_patterns(request: Request) -> Any:
        body = parse_model(PatternsRequest, await read_json_object(request, max_bytes=256 * 1024))

        def run() -> Any:
            patterns = services.vocabulary().set_patterns(body.patterns)
            return PatternsResponse(patterns=list(patterns)).model_dump(mode="json")

        return await call(run)

    @router.post("/patterns/test")
    async def test_patterns(request: Request) -> Any:
        """“Try out": patterns, terms and lists on a sample sentence.

        Uses the sidecar's regex engine – the same one later used for documents.
        No data is persisted.
        """

        body = parse_model(PatternTestRequest, await read_json_object(request, max_bytes=64 * 1024))

        def run() -> Any:
            store = services.vocabulary()
            patterns = (
                validate_patterns(body.patterns) if body.patterns is not None else store.patterns()
            )
            language = services.settings_store().get().general.language
            sources = (
                ("pattern", FindingSource.RULE, pattern_recognizers(patterns)),
                ("term", FindingSource.MASTER_DATA, term_recognizers(store.terms())),
                ("list", FindingSource.MASTER_DATA, list_recognizers(store.lists())),
            )
            origin: dict[tuple[int, int, str], str] = {}
            findings = []
            for name, source, recognizers in sources:
                for finding in _run_recognizers(body.text, recognizers, source=source):
                    origin.setdefault((finding.start, finding.end, finding.entity_type), name)
                    findings.append(finding)
            hits = [
                {
                    "start": utf16_offset(body.text, finding.start),
                    "end": utf16_offset(body.text, finding.end),
                    "entity_type": finding.entity_type,
                    "source": origin[(finding.start, finding.end, finding.entity_type)],
                    "label": placeholder_for(finding.entity_type, language),
                }
                for finding in sorted(resolve_overlaps(findings), key=lambda item: item.start)
            ]
            return PatternTestResponse(hits=hits, count=len(hits)).model_dump(mode="json")

        return await call(run)

    # -- Templates ------------------------------------------------------------

    def all_templates() -> tuple[Template, ...]:
        examples = templates.example_templates(services.profiles())
        return (*examples, *services.vocabulary().templates())

    def find_template(template_id: str) -> Template:
        for template in all_templates():
            if template.id == template_id:
                return template
        raise VocabularyNotFoundError("Vorlage nicht gefunden.")

    def templates_payload() -> dict[str, Any]:
        active = services.settings_store().get().names_terms.template
        known = all_templates()
        return TemplatesResponse(
            templates=[
                TemplateOut(
                    id=item.id,
                    title=item.title,
                    example=templates.is_example(item.id),
                    terms=len(item.terms),
                    patterns=len(item.patterns),
                    masking=item.masking,
                )
                for item in known
            ],
            active=active if any(item.id == active for item in known) else None,
        ).model_dump(mode="json")

    def apply(template: Template) -> dict[str, Any]:
        store = services.vocabulary()
        store.apply_template(template)
        changes: dict[str, Any] = {"names_terms": {"template": template.id}}
        if template.masking is not None:
            changes["masking"] = template.masking.model_dump(mode="json")
        settings = services.settings_store().update(changes)
        return TemplateApplyResponse.model_validate(
            {
                "templates": templates_payload(),
                "terms": [item.model_dump(mode="json") for item in store.terms()],
                "patterns": [item.model_dump(mode="json") for item in store.patterns()],
                "settings": settings.model_dump(mode="json"),
            }
        ).model_dump(mode="json")

    @router.get("/templates")
    async def get_templates() -> Any:
        return await call(templates_payload)

    @router.post("/templates", status_code=HTTP_201_CREATED)
    async def save_template(request: Request) -> Any:
        """Save as …: store terms, patterns and masking defaults as a template."""

        body = parse_model(SaveTemplateRequest, await read_json_object(request, max_bytes=4096))

        def run() -> Any:
            if any(
                templates.is_example(item.id) and item.title.casefold() == body.title.casefold()
                for item in all_templates()
            ):
                raise VocabularyValidationError(
                    "template_example", "Beispielvorlagen werden nicht ueberschrieben."
                )
            store = services.vocabulary()
            template = store.save_template(
                title=body.title,
                terms=store.terms(),
                patterns=store.patterns(),
                masking=services.settings_store().get().masking,
            )
            services.settings_store().update({"names_terms": {"template": template.id}})
            return templates_payload()

        return await call(run)

    @router.post("/templates/{template_id}/apply")
    async def apply_template(template_id: str) -> Any:
        """Select a template: replace current terms and patterns."""

        return await call(lambda: apply(find_template(template_id)))

    @router.post("/templates/export")
    async def export_template(request: Request) -> Any:
        body = parse_model(TemplateExportRequest, await read_json_object(request, max_bytes=4096))

        def run() -> Any:
            if body.id is not None:
                template = find_template(body.id)
            else:
                store = services.vocabulary()
                template = Template(
                    id="aktuell",
                    title=body.title or "Vorlage",
                    terms=store.terms(),
                    patterns=store.patterns(),
                    masking=services.settings_store().get().masking,
                    created_at=datetime.now(UTC),
                )
            return TemplateExportResponse(
                name=templates.file_name(template), content=templates.render(template)
            ).model_dump(mode="json")

        return await call(run)

    @router.post("/templates/import", status_code=HTTP_201_CREATED)
    async def import_template(request: Request) -> Any:
        """Import a template file: store it and select it immediately."""

        body = parse_model(
            TemplateImportRequest,
            await read_json_object(request, max_bytes=templates.MAX_TEMPLATE_BYTES * 2),
        )

        def run() -> Any:
            parsed = templates.parse(body.content)
            if any(
                templates.is_example(item.id) and item.title.casefold() == parsed.title.casefold()
                for item in all_templates()
            ):
                raise VocabularyValidationError(
                    "template_example",
                    "Eine Beispielvorlage gleichen Namens gibt es schon.",
                )
            stored = services.vocabulary().save_template(
                title=parsed.title,
                terms=parsed.terms,
                patterns=parsed.patterns,
                masking=parsed.masking,
            )
            return apply(stored)

        return await call(run)

    return router


def _kind(raw: str) -> ListKind:
    if raw not in _KINDS:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "list_kind", "message": "Art muss persons oder organizations sein."},
        )
    return raw  # type: ignore[return-value]


def _modified(milliseconds: int | None) -> datetime | None:
    if milliseconds is None or milliseconds <= 0:
        return None
    try:
        moment = datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    # A file timestamped in the future (wrong clock) is treated as now.
    return min(moment, datetime.now(UTC))


async def _read_list_upload(request: Request) -> tuple[str, bytes]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].casefold()
    if content_type != "multipart/form-data":
        raise HTTPException(
            status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Erlaubt ist nur multipart/form-data.",
        )
    try:
        async with request.form(max_files=1, max_fields=2, max_part_size=MAX_LIST_BYTES) as form:
            part = form.get("file")
            if not isinstance(part, UploadFile):
                raise HTTPException(
                    status_code=HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Multipart braucht ein Dateifeld 'file'.",
                )
            raw = await part.read(MAX_LIST_BYTES + 1)
            name = part.filename or "liste.csv"
    except HTTPException:
        raise
    except (AssertionError, RuntimeError) as error:
        raise HTTPException(
            status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Multipart konnte nicht gelesen werden.",
        ) from error
    if len(raw) > MAX_LIST_BYTES:
        raise HTTPException(
            status_code=HTTP_413_CONTENT_TOO_LARGE,
            detail={"code": "too_large", "message": "Die Liste ist zu gross."},
        )
    return name, raw


__all__ = ["build_router"]
