"""API v2 jobs on the draft state: ingest, persist, load.

The JobManager does not know the draft state; here the hooks are created
that a job submits, and the loader with which ``JobManager.get()`` restores
a missing job.

Ingestion order: Original → Manifest ``ingested`` → Job → ``EntryFile`` in the entry → ``202``.
A crash may leave an orphaned folder; ``DraftStore.sweep`` cleans it up.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from . import __version__
from .base_profile import BASE_PROFILE_ID, base_profile
from .drafts import (
    DraftDeletedError,
    DraftError,
    DraftManifest,
    EntryDrafts,
)
from .entries import EntryNotFoundError
from .extraction import DocumentFormat, ExtractedDocument, ExtractionError, structured_payload
from .jobs import (
    AnalysisRunner,
    Extractor,
    JobHooks,
    JobNotFoundError,
    JobRecord,
    JobUnavailableError,
    RestoredJob,
    safe_filename,
)
from .languages import packs
from .profiles import ProfileRuleViolation
from .resources import selected_ocr_engine

if TYPE_CHECKING:
    from .services import PhaseOneServices


def entry_drafts(services: PhaseOneServices, entry_id: str) -> EntryDrafts:
    """Current draft state for an entry; assigns a key to legacy entries."""

    store = services.drafts()
    key = services.entries().ensure_draft_key(entry_id, store.create_key)
    try:
        return store.open(entry_id, key)
    except DraftDeletedError as error:
        raise EntryNotFoundError("Eintrag nicht gefunden.") from error


def document_extractor(services: PhaseOneServices) -> Extractor:
    """Reads a file with the base profile – in the worker, not in the request."""

    profile = base_profile()
    extractor = services.extractor()
    # OCR in the enabled document languages.
    ocr_languages = [
        item.tesseract for item in packs(services.document_language_options().languages)
    ]

    def extract(content: bytes, filename: str) -> ExtractedDocument:
        document = extractor.extract_bytes(
            content,
            filename=filename,
            ocr=profile.pipeline.ocr.value,
            ocr_languages=ocr_languages,
            structured_data=profile.pipeline.structured_data,
        )
        services.record_ocr_document(document)
        return document

    return extract


#: Fingerprint changes that affect detection.
#: Only a change in these keys triggers a full re-detect; otherwise
#: updates would unnecessarily force a “new detection” even when
#: detection results stay the same.
REDETECT_KEYS: Final = ("languages", "second_opinion", "ocr_engine", "ner")
REEXTRACT_KEYS: Final = frozenset({"languages", "ocr_engine"})


def pipeline_changes(stored: Mapping[str, str], current: Mapping[str, str]) -> list[str]:
    """Which parts of the detection have changed since the last save.

    Unknown (missing in one of the two) does not count: Better no offer
    than a wrong one.
    """

    return [
        key
        for key in REDETECT_KEYS
        if key in stored and key in current and stored[key] != current[key]
    ]


def comparable_fingerprint(services: PhaseOneServices) -> dict[str, str]:
    """The current fingerprint for comparison with a manifest.

    As long as the detection is not yet loaded, the sidecar only knows the
    intended NER model, not the actually active one; that remains
    out of the comparison.
    """

    fingerprint = pipeline_fingerprint(services)
    with suppress(Exception):
        if services.nlp_status().get("active") is None:
            fingerprint.pop("ner", None)
    return fingerprint


def pipeline_fingerprint(services: PhaseOneServices) -> dict[str, str]:
    """What detection depends on, without any content.

    If it no longer matches later, the UI offers "Detect again". An error here
    must never stop reading.
    """

    fingerprint = {
        "app": __version__,
        "profile": f"{BASE_PROFILE_ID}@{base_profile().version}",
    }
    with suppress(Exception):
        fingerprint["ocr_engine"] = selected_ocr_engine()
    with suppress(Exception):
        fingerprint["languages"] = services.document_language_options().fingerprint()
    with suppress(Exception):
        opinion = services.second_opinion_options()
        model = services.second_opinion_model() if opinion is not None else None
        fingerprint["second_opinion"] = (
            f"{opinion.fingerprint()}:{model.folder.name}" if opinion and model else "-"
        )
    with suppress(Exception):
        status = services.nlp_status()
        fingerprint["ner"] = str(status.get("active") or status.get("selected") or "-")
    return fingerprint


def ingest(
    services: PhaseOneServices,
    drafts: EntryDrafts,
    *,
    filename: str,
    content: bytes,
    document_format: DocumentFormat,
    extractor: Extractor,
    runner: AnalysisRunner,
    pipeline: dict[str, str],
) -> JobRecord:
    """Stores the original and only then starts the job (AS1.3).

    If the original cannot be stored completely, the file is rejected with
    ``storage_failed`` – half-saved is never.
    """

    file_id = secrets.token_hex(16)
    now = datetime.now(UTC)
    manifest = DraftManifest(
        file_id=file_id,
        state="ingested",
        filename=safe_filename(filename),
        format=document_format.value,
        size_bytes=len(content),
        source_sha256=hashlib.sha256(content).hexdigest(),
        profile_id=BASE_PROFILE_ID,
        pipeline=pipeline,
        created_at=now,
        updated_at=now,
    )
    try:
        drafts.write_original(file_id, content)
        drafts.write_manifest(file_id, manifest)
    except DraftDeletedError as error:
        raise EntryNotFoundError("Eintrag nicht gefunden.") from error
    except DraftError as error:
        _discard(drafts, file_id)
        services.diagnostics.record(
            "draft_write_failed",
            "Eine Datei liess sich nicht im Arbeitsstand ablegen; sie wurde abgelehnt.",
            severity="warning",
            artifact="original",
            error_type=type(error.__cause__ or error).__name__,
        )
        raise ProfileRuleViolation(
            "storage_failed", "Die Datei liess sich nicht sicher ablegen."
        ) from error
    try:
        return services.jobs().submit_deferred(
            content,
            filename=filename,
            extractor=extractor,
            profile_id=BASE_PROFILE_ID,
            entry_id=drafts.entry_id,
            analysis_runner=runner,
            job_id=file_id,
            hooks=job_hooks(services, drafts, file_id, pipeline),
            source_writer=_source_writer(drafts, file_id),
        )
    except BaseException:
        _discard(drafts, file_id)
        raise


def job_hooks(
    services: PhaseOneServices,
    drafts: EntryDrafts,
    file_id: str,
    pipeline: dict[str, str],
    *,
    after_extraction: Callable[[ExtractedDocument], None] | None = None,
) -> JobHooks:
    """The worker writes with: extraction, detection, error, abort.

    If writing fails, the manifest remains in the previous state – after a restart, the
    file is then processed anew from there. A deleted entry writes nothing more
    (tombstone). ``after_extraction`` runs as soon as a new extraction is committed –
    "Detect again" syncs the review state there (AS3.4).
    """

    def persist(artifact: str, write: Callable[[], object]) -> None:
        try:
            write()
        except DraftDeletedError:
            return
        except (DraftError, OSError) as error:
            services.diagnostics.record(
                "draft_write_failed",
                "Der Arbeitsstand einer Datei liess sich nicht schreiben; nach einem "
                "Neustart wird sie ab dem letzten Stand neu bearbeitet.",
                severity="warning",
                artifact=artifact,
                error_type=type(error.__cause__ or error).__name__,
            )

    def extracted(document: ExtractedDocument) -> None:
        def write() -> None:
            drafts.write_extraction(file_id, document)
            drafts.update_manifest(file_id, state="extracted")
            if after_extraction is not None:
                after_extraction(document)

        persist("extraction", write)

    def completed(result: Any) -> None:
        def write() -> None:
            drafts.write_detection(file_id, result)
            drafts.update_manifest(file_id, state="detected", error_code=None, pipeline=pipeline)

        persist("detection", write)

    def failed(error_code: str) -> None:
        persist(
            "manifest",
            lambda: drafts.update_manifest(file_id, state="failed", error_code=error_code),
        )

    def cancelled() -> None:
        persist(
            "manifest",
            lambda: drafts.update_manifest(file_id, state="cancelled", error_code="cancelled"),
        )

    return JobHooks(
        on_extracted=extracted,
        on_completed=completed,
        on_failed=failed,
        on_cancelled=cancelled,
    )


def load_job(
    services: PhaseOneServices,
    file_id: str,
    *,
    background: bool = False,
    after_extraction: Callable[[ExtractedDocument], None] | None = None,
) -> RestoredJob:
    """The loader for ``JobManager.get()`` (AS1.5): Draft state → Job.

    ``JobNotFoundError``: Unknown file or old stock without draft state
    (where ``expired`` continues to apply). ``JobUnavailableError``: The draft state
    is there, but not readable – the file becomes ``unavailable``.
    Reopening does not recalculate: Extraction and detection come as they
    were saved.
    """

    store = services.entries()
    entry = store.find_by_file(file_id)
    if entry is None:
        raise JobNotFoundError("Job nicht gefunden oder bereits geloescht.")
    key = store.draft_key(entry.id)
    if key is None:
        raise JobNotFoundError("Diese Datei hat keinen Arbeitsstand (Altbestand).")
    profile = base_profile()

    def structured(text: str, document_format: DocumentFormat) -> Any:
        return structured_payload(
            text, document_format, structured_data=profile.pipeline.structured_data
        )

    try:
        drafts = services.drafts().open(entry.id, key)
        manifest = drafts.read_manifest(file_id)
        if manifest.file_id != file_id:
            raise JobUnavailableError("Das Manifest gehoert zu einer anderen Datei.")
        document = (
            drafts.read_extraction(file_id, structured=structured)
            if manifest.state in {"extracted", "detected"}
            else None
        )
        result = drafts.read_detection(file_id) if manifest.state == "detected" else None
    except DraftDeletedError as error:
        raise JobNotFoundError("Der Eintrag wird gerade geloescht.") from error
    except (DraftError, ExtractionError) as error:
        raise JobUnavailableError("Der Arbeitsstand der Datei ist nicht lesbar.") from error

    write_source = _source_writer(drafts, file_id)
    common: dict[str, Any] = {
        "id": file_id,
        "entry_id": entry.id,
        "filename": manifest.filename,
        "profile_id": manifest.profile_id,
        "source_size": manifest.size_bytes,
        "created_at": manifest.created_at,
    }
    if manifest.state == "detected":
        return RestoredJob(
            **common,
            status="completed",
            extracted_document=document,
            result=result,
            write_source=write_source,
        )
    if manifest.state in {"failed", "cancelled"}:
        return RestoredJob(**common, status=manifest.state, error_code=manifest.error_code)
    # Still running when the sidecar ended: continue there where the state ends (E-AS3).
    runner = services.analyzer_for_profile(profile)
    pipeline = pipeline_fingerprint(services)
    return RestoredJob(
        **common,
        status="pending",
        extracted_document=document,
        write_source=write_source,
        extractor=document_extractor(services) if document is None else None,
        analysis_runner=runner,
        hooks=job_hooks(services, drafts, file_id, pipeline, after_extraction=after_extraction),
        background=background,
    )


def _source_writer(drafts: EntryDrafts, file_id: str) -> Callable[[Path], None]:
    """Decrypts the original into the draft folder of a job."""

    def write_source(path: Path) -> None:
        try:
            drafts.restore_original(file_id, path)
        except DraftError as error:
            raise JobUnavailableError("Das gespeicherte Original ist nicht lesbar.") from error

    return write_source


def _discard(drafts: EntryDrafts, file_id: str) -> None:
    with suppress(DraftError, OSError):
        drafts.delete_file(file_id)


__all__ = [
    "REDETECT_KEYS",
    "REEXTRACT_KEYS",
    "comparable_fingerprint",
    "document_extractor",
    "entry_drafts",
    "ingest",
    "job_hooks",
    "load_job",
    "pipeline_changes",
    "pipeline_fingerprint",
]
