"""API v2 flow: entries, files, progress, apply and text mode.

The routers stay thin; this outlines the path between entry store,
job manager, processing and vault. Everything runs synchronously and is handed
to the thread pool as a whole – locks and models stay off the event loop
(see ``app.py``).

Two rules guide the design:

* **Apply is a dry-run.** Short codes and display names are computed but not
  written to the translation table. Only “Save” or “Copy” persists them.
* **When in doubt, mask.** Open questions are replaced instead of blocking
  export.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from . import draft_jobs
from .api_common import python_offset_from_utf16, utf16_offset
from .api_models import ReviewDecisionIn
from .audit import AuditAction, AuditContext, AuditDecision
from .base_profile import BASE_PROFILE_ID, base_profile
from .catalog import CATEGORY_BY_ENTITY, CATEGORY_IDS
from .detection import DetectionResult
from .document_workflow import (
    PageGeometry,
    image_page_geometry,
    pdf_page_geometry,
    plan_placements,
)
from .drafts import DraftError, DraftUnavailableError, EntryView
from .entries import (
    DecisionState,
    Entry,
    EntryFile,
    EntryNotFoundError,
    FileReview,
    FileStatus,
    ManualState,
)
from .exporting import ExportValidationError
from .extraction import DocumentFormat
from .findings_view import detection_for_profile, question_ids, serialize_detection
from .jobs import (
    JobNotFoundError,
    JobNotReadyError,
    JobRecord,
    JobStatus,
    JobUnavailableError,
)
from .masking import (
    MaskingSpec,
    counts_by_category,
    derived_anonymize,
    label_facts,
    processing_request,
)
from .placement import TextStyle
from .processing import (
    FindingKind,
    ManualFinding,
    ProcessingResult,
    ReviewAction,
    ReviewDecision,
    UnresolvedPolicy,
    finding_id,
)
from .profiles import ProfileRuleViolation, runtime_entity_type
from .services import PhaseOneServices
from .titles import TitleSource, documents_title, text_title


class WorkflowError(ValueError):
    """Request does not match the entry's state (HTTP 409/422)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# -- entries and files ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Upload:
    name: str
    content: bytes


@dataclass(frozen=True, slots=True)
class Rejection:
    name: str
    code: str
    message: str


def default_masking(services: PhaseOneServices) -> MaskingSpec:
    defaults = services.settings_store().get().masking
    return MaskingSpec(
        method=defaults.method,
        style=defaults.style,
        categories=defaults.categories,
        anonymize=derived_anonymize(defaults.method, defaults.categories),
    )


def submit_files(
    services: PhaseOneServices,
    entry_id: str,
    uploads: Iterable[Upload],
) -> tuple[Entry, list[EntryFile], list[Rejection]]:
    """One job per file; rejected files do not block the others."""

    store = services.entries()
    entry = store.get(entry_id)
    if entry.kind != "documents":
        raise WorkflowError("wrong_kind", "Dateien gehoeren nur zu Dokument-Eintraegen.")
    submit = _submitter(services, entry_id)

    accepted: list[EntryFile] = []
    rejected: list[Rejection] = []
    for upload in uploads:
        try:
            accepted.append(submit(upload))
        except ProfileRuleViolation as error:
            rejected.append(Rejection(upload.name, error.code, str(error)))

    def add_files(current: Entry) -> Entry:
        files = (*current.files, *accepted)
        title = current.title
        if not current.title_custom:
            title = documents_title([TitleSource(item.name) for item in files])
        return current.model_copy(
            update={
                "files": files,
                "title": title,
                "status": "processing" if accepted else current.status,
            }
        )

    try:
        entry = store.update(entry_id, add_files)
    except EntryNotFoundError:
        # The entry was deleted while jobs were starting.
        # Those jobs no longer belong to anything; their state disappears with the entry.
        for item in accepted:
            with suppress(JobNotFoundError):
                services.jobs().cancel(item.id)
        raise
    return entry, accepted, rejected


def _submitter(services: PhaseOneServices, entry_id: str) -> Callable[[Upload], EntryFile]:
    """Validate a file against the base profile, store it, and start its job.

    The original is stored encrypted in the entry state before the job starts
    (spec §2). Raises ``ProfileRuleViolation`` for format, size, empty file,
    or ``storage_failed``.
    """

    profile = base_profile()
    runner = services.analyzer_for_profile(profile)
    extract = draft_jobs.document_extractor(services)
    pipeline = draft_jobs.pipeline_fingerprint(services)
    drafts = draft_jobs.entry_drafts(services, entry_id)

    def submit(upload: Upload) -> EntryFile:
        document_format = profile.validate_upload(upload.name, len(upload.content))
        if not upload.content:
            raise ProfileRuleViolation("empty", "Die Datei ist leer.")
        record = draft_jobs.ingest(
            services,
            drafts,
            filename=upload.name,
            content=upload.content,
            document_format=document_format,
            extractor=extract,
            runner=runner,
            pipeline=pipeline,
        )
        return EntryFile(
            id=record.id,
            name=record.filename,
            format=document_format.value,
            size_bytes=len(upload.content),
            source_sha256=hashlib.sha256(upload.content).hexdigest(),
        )

    return submit


def replace_file(
    services: PhaseOneServices,
    entry_id: str,
    file_id: str,
    upload: Upload,
) -> tuple[Entry, EntryFile, bool]:
    """Reload original: existing stock (E9) and unreadable work state (``unavailable``).

    The file gets a new job and thus a new ID, but keeps its location, name and the stored output.
    Its review state moves with it; finding IDs are tied to type and position, so an unchanged file
    finds its decisions again. If the hash differs from the first load, ``changed`` is reported –
    decisions then only apply where the positions stayed the same. The new original ends up in the
    work state (AS1.9); the state of the old file disappears.
    """

    store = services.entries()
    entry = store.get(entry_id)
    if entry.kind != "documents":
        raise WorkflowError("wrong_kind", "Dateien gehoeren nur zu Dokument-Eintraegen.")
    previous = entry.file(file_id)
    if previous is None:
        raise WorkflowError("unknown_file", "Die Datei gehoert nicht zu diesem Eintrag.")
    if previous.status in {"queued", "processing"}:
        raise WorkflowError("still_processing", "Die Datei wird gerade erkannt.")
    new_file = _submitter(services, entry_id)(upload)
    changed = previous.source_sha256 is not None and previous.source_sha256 != (
        new_file.source_sha256
    )
    manager = services.jobs()
    if manager.is_loaded(file_id):
        with suppress(JobNotFoundError):
            manager.cancel(file_id)

    def swap(current: Entry) -> Entry:
        files = tuple(
            new_file.model_copy(
                update={
                    "name": item.name,
                    "label": item.label,
                    "output_path": item.output_path,
                    "output": item.output,
                    "source_changed": item.source_changed or changed,
                }
            )
            if item.id == file_id
            else item
            for item in current.files
        )
        review = {key: value for key, value in current.review.items() if key != file_id}
        if file_id in current.review:
            review[new_file.id] = current.review[file_id]
        return current.model_copy(
            update={
                "files": files,
                "review": review,
                "status": "processing" if current.status != "saved" else current.status,
            }
        )

    entry = store.update(entry_id, swap)
    with suppress(DraftError, OSError, EntryNotFoundError):
        draft_jobs.entry_drafts(services, entry_id).delete_file(file_id)
    replaced = entry.file(new_file.id)
    assert replaced is not None
    return entry, replaced, changed


def get_view(services: PhaseOneServices, entry_id: str) -> EntryView | None:
    store = services.entries()
    store.get(entry_id)
    key = store.draft_key(entry_id)
    if key is None:
        return None
    try:
        return services.drafts().open(entry_id, key).read_view()
    except DraftUnavailableError:
        return None


def put_view(services: PhaseOneServices, entry_id: str, view: EntryView) -> EntryView:
    entry = services.entries().get(entry_id)
    if entry.kind != "documents":
        raise WorkflowError("wrong_kind", "Ansichtsstaende gehoeren zu Dokument-Eintraegen.")
    if view.active_file_id is not None and entry.file(view.active_file_id) is None:
        raise WorkflowError("unknown_file", "Die Datei gehoert nicht zu diesem Eintrag.")
    # Viewing does not modify history or retention period.
    key = services.entries().draft_key(entry_id)
    if key is not None:
        services.drafts().open(entry_id, key).write_view(view)
    return view


def retry_file(services: PhaseOneServices, entry_id: str, file_id: str) -> tuple[Entry, EntryFile]:
    """Restart a failed file from the saved original."""

    entry = services.entries().get(entry_id)
    if entry.kind != "documents":
        raise WorkflowError("wrong_kind", "Dateien gehoeren nur zu Dokument-Eintraegen.")
    if entry.file(file_id) is None:
        raise WorkflowError("unknown_file", "Die Datei gehoert nicht zu diesem Eintrag.")
    key = services.entries().draft_key(entry_id)
    if key is None:
        raise WorkflowError("files_expired", "Das Original muss erneut ausgewaehlt werden.")

    def prepare(previous: JobRecord | None) -> Any:
        drafts = services.drafts().open(entry_id, key)
        manifest = drafts.read_manifest(file_id)
        if previous is None and manifest.state not in {"failed", "cancelled"}:
            raise WorkflowError("retry_not_failed", "Nur fehlgeschlagene Dateien erneut versuchen.")
        # A corrupted original must not turn the existing error checkpoint
        # into a seemingly resumable state.
        drafts.verify_original(file_id)
        drafts.update_manifest(file_id, state="ingested", error_code=None)
        return draft_jobs.load_job(services, file_id)

    try:
        services.jobs().retry(file_id, prepare)
    except JobNotReadyError as error:
        raise WorkflowError("retry_not_failed", str(error)) from error
    except (DraftUnavailableError, JobUnavailableError) as error:
        _mark_unavailable(services, entry_id, file_id)
        raise WorkflowError(
            "files_unavailable", "Die gespeicherte Kopie ist nicht mehr lesbar."
        ) from error

    def change(current: Entry) -> Entry:
        files = tuple(
            item.model_copy(update={"status": "queued", "error_code": None})
            if item.id == file_id
            else item
            for item in current.files
        )
        return current.model_copy(
            update={
                "files": files,
                "status": "saved" if current.status == "saved" else "processing",
            }
        )

    entry = services.entries().update(entry_id, change)
    item = entry.file(file_id)
    assert item is not None
    return entry, item


def _mark_unavailable(services: PhaseOneServices, entry_id: str, file_id: str) -> None:
    def unavailable(current: Entry) -> Entry:
        return current.model_copy(
            update={
                "files": tuple(
                    item.model_copy(update={"status": "unavailable"})
                    if item.id == file_id
                    else item
                    for item in current.files
                )
            }
        )

    services.entries().update(entry_id, unavailable)


# -- Redetect ---------------------------------------------------------------


def redetect_status(services: PhaseOneServices, entry_id: str) -> list[dict[str, Any]]:
    """Files whose detection no longer matches the current settings.

    Based on the fingerprint in the manifest: languages, second opinion,
    OCR, NER model. Reopening never recomputes automatically; this only
    offers a re-detect option.
    """

    store = services.entries()
    entry = store.get(entry_id)
    key = store.draft_key(entry_id)
    if entry.kind != "documents" or key is None:
        return []
    candidates = [item for item in entry.files if item.status == "review"]
    if not candidates:
        return []
    try:
        drafts = services.drafts().open(entry_id, key)
    except DraftError:
        return []
    current = draft_jobs.comparable_fingerprint(services)
    stale: list[dict[str, Any]] = []
    for item in candidates:
        try:
            manifest = drafts.read_manifest(item.id)
        except DraftError:
            continue
        if manifest.state != "detected":
            continue
        changes = draft_jobs.pipeline_changes(manifest.pipeline, current)
        if changes:
            stale.append(_redetect_file(item, changes))
    return stale


def _redetect_file(item: EntryFile, changes: list[str]) -> dict[str, Any]:
    return {
        "file_id": item.id,
        "changes": changes,
        "reextract": item.ocr_used and bool(set(changes) & draft_jobs.REEXTRACT_KEYS),
    }


def redetect_files(
    services: PhaseOneServices,
    entry_id: str,
    file_ids: list[str] | None = None,
) -> tuple[Entry, list[dict[str, Any]]]:
    """Re-detect files from the saved state without touching the original.

    Extraction stays unchanged unless OCR of a scan has changed; then the saved
    original is re-extracted. Decisions are applied via the finding ID just like
    ``replace_file``. If the new extraction changes the text, decisions and
    manual entries are kept only where the location carries the same text.
    """

    store = services.entries()
    entry = store.get(entry_id)
    if entry.kind != "documents":
        raise WorkflowError("wrong_kind", "Dateien gehoeren nur zu Dokument-Eintraegen.")
    key = store.draft_key(entry_id)
    if key is None:
        raise WorkflowError("files_expired", "Das Original muss erneut ausgewaehlt werden.")
    stale = {item["file_id"]: item for item in redetect_status(services, entry_id)}
    if file_ids is None:
        targets = [stale[item.id] for item in entry.files if item.id in stale]
    else:
        targets = []
        for file_id in dict.fromkeys(file_ids):
            item = entry.file(file_id)
            if item is None:
                raise WorkflowError("unknown_file", "Die Datei gehoert nicht zu diesem Eintrag.")
            if item.status != "review":
                raise WorkflowError("still_processing", "Nur fertig erkannte Dateien neu erkennen.")
            targets.append(stale.get(file_id) or _redetect_file(item, []))
    if not targets:
        raise WorkflowError("nothing_to_redetect", "Die Erkennung passt zu den Einstellungen.")

    ids = {item["file_id"] for item in targets}

    def change(current: Entry) -> Entry:
        files = tuple(
            item.model_copy(
                update={"status": "queued", "error_code": None, "extraction_changed": False}
            )
            if item.id in ids
            else item
            for item in current.files
        )
        return current.model_copy(
            update={
                "files": files,
                "status": "saved" if current.status == "saved" else "processing",
                # New findings start a new draft – also for “Since saved” changes.
                "draft_revision": current.draft_revision + 1,
            }
        )

    # First update the entry, then the jobs: the worker reconciles the review state
    # after the new extraction and must not be overwritten. If a job does not start,
    # ``sync_entry`` fetches the true state.
    store.update(entry_id, change)
    for target in targets:
        file_id = target["file_id"]
        try:
            services.jobs().retry(
                file_id,
                _redetect_preparer(services, entry_id, key, file_id, reextract=target["reextract"]),
                allow_completed=True,
            )
        except JobNotReadyError as error:
            sync_entry(services, entry_id)
            raise WorkflowError("still_processing", str(error)) from error
        except (DraftUnavailableError, JobUnavailableError) as error:
            _mark_unavailable(services, entry_id, file_id)
            sync_entry(services, entry_id)
            raise WorkflowError(
                "files_unavailable", "Die gespeicherte Kopie ist nicht mehr lesbar."
            ) from error
    return sync_entry(services, entry_id), targets


def _redetect_preparer(
    services: PhaseOneServices,
    entry_id: str,
    key: str,
    file_id: str,
    *,
    reextract: bool,
) -> Callable[[JobRecord | None], Any]:
    def prepare(previous: JobRecord | None) -> Any:
        drafts = services.drafts().open(entry_id, key)
        manifest = drafts.read_manifest(file_id)
        if previous is None and manifest.state != "detected":
            raise JobNotReadyError("Nur fertig erkannte Dateien neu erkennen.")
        if not reextract:
            # Extraction must be readable; otherwise detection would run into nothing.
            drafts.read_extraction(file_id)
            drafts.update_manifest(file_id, state="extracted", error_code=None)
            return draft_jobs.load_job(services, file_id)
        drafts.verify_original(file_id)
        old_text = drafts.read_extraction(file_id).text
        old_detection = drafts.read_detection(file_id)
        drafts.update_manifest(file_id, state="ingested", error_code=None)

        def reconcile(document: Any) -> None:
            def change(current: Entry) -> Entry:
                review = current.review.get(file_id)
                migrated, changed = migrate_review(
                    review or FileReview(), old_text, document.text, old_detection
                )
                if not changed:
                    return current
                files = tuple(
                    item.model_copy(update={"extraction_changed": True})
                    if item.id == file_id
                    else item
                    for item in current.files
                )
                reviews = dict(current.review)
                if review is not None:
                    reviews[file_id] = migrated
                return current.model_copy(update={"files": files, "review": reviews})

            with suppress(EntryNotFoundError):
                services.entries().update(entry_id, change)

        return draft_jobs.load_job(services, file_id, after_extraction=reconcile)

    return prepare


def migrate_review(
    review: FileReview,
    old_text: str,
    new_text: str,
    old_detection: DetectionResult,
) -> tuple[FileReview, bool]:
    """Review state after a new extraction: keep only findings that still match the same text.

    Finding IDs are tied to type and position. A decision stays if the text at the original
    position is unchanged; a manual entry as well. ``changed`` is True when the text differs.
    """

    if old_text == new_text:
        return review, False
    spans = {
        finding_id(finding, kind=kind): (finding.start, finding.end)
        for kind, findings in (
            (FindingKind.CONFIRMED, old_detection.confirmed),
            (FindingKind.SUSPICIOUS, old_detection.suspicious),
        )
        for finding in findings
    }

    def same(start: int, end: int) -> bool:
        return end <= len(new_text) and old_text[start:end] == new_text[start:end]

    def same_utf16(start: int, end: int) -> bool:
        try:
            old = (
                python_offset_from_utf16(old_text, start),
                python_offset_from_utf16(old_text, end),
            )
            new = (
                python_offset_from_utf16(new_text, start),
                python_offset_from_utf16(new_text, end),
            )
        except ValueError:
            return False
        return old == new and same(*old)

    decisions = tuple(
        item
        for item in review.decisions
        if (span := spans.get(item.finding_id)) is not None and same(*span)
    )
    manual = tuple(item for item in review.manual if same_utf16(item.start, item.end))
    return FileReview(decisions=decisions, manual=manual), True


# -- Storage ---------------------------------------------------------------

#: The UI warns before loading when space is low (display instead of quote).
LOW_SPACE_BYTES = 2 * 1024**3


def storage(services: PhaseOneServices) -> dict[str, Any]:
    """Total and per-entry storage usage, including deletion date.

    Deletion date follows the retention period from the last modification; merely opening
    does not reset the timer (spec §5).
    """

    store = services.entries()
    drafts = services.drafts()
    usage = drafts.usage()
    retention = services.settings_store().get().history.retention_days
    free: int | None = None
    with suppress(OSError):
        free = shutil.disk_usage(drafts.root).free
    return {
        "total_bytes": usage.bytes,
        "free_bytes": free,
        "low_space_bytes": LOW_SPACE_BYTES,
        "retention_days": retention,
        "entries": [
            {
                "entry_id": entry.id,
                "bytes": usage.entries.get(entry.id, 0),
                "delete_at": (
                    entry.updated_at + timedelta(days=retention) if retention is not None else None
                ),
            }
            for entry in store.list()
        ],
    }


def remove_file(services: PhaseOneServices, entry_id: str, file_id: str) -> Entry:
    """Remove a file together with its state, review and loaded cache."""

    entry = services.entries().get(entry_id)
    if entry.file(file_id) is None:
        raise WorkflowError("unknown_file", "Die Datei gehoert nicht zu diesem Eintrag.")
    key = services.entries().draft_key(entry_id)
    if key is not None:
        services.drafts().delete_file(entry_id, file_id)
    if services.jobs().is_loaded(file_id):
        with suppress(JobNotFoundError):
            services.jobs().cancel(file_id)

    def change(current: Entry) -> Entry:
        files = tuple(item for item in current.files if item.id != file_id)
        return current.model_copy(
            update={
                "files": files,
                "review": {key: value for key, value in current.review.items() if key != file_id},
                "status": _entry_status(current, files),
            }
        )

    return services.entries().update(entry_id, change)


def sync_entry(services: PhaseOneServices, entry_id: str) -> Entry:
    """Synchronize file status, counts, and title with job records.

    Missing jobs are loaded from the persisted state, so a restart leaves the
    entry unchanged. Each access extends the idle timeout: viewing an entry
    keeps it active.
    """

    store = services.entries()
    entry = store.get(entry_id)
    manager = services.jobs()
    records: dict[str, JobRecord] = {}
    unavailable: set[str] = set()
    for item in entry.files:
        try:
            records[item.id] = manager.get(item.id)
        except JobUnavailableError:
            unavailable.add(item.id)
        except JobNotFoundError:
            continue

    def change(current: Entry) -> Entry:
        files = tuple(
            _synced_file(item, records.get(item.id), unavailable=item.id in unavailable)
            for item in current.files
        )
        title = current.title
        if not current.title_custom and files and all(_is_settled(item) for item in files):
            title = _final_title(files, records)
        return current.model_copy(
            update={
                "files": files,
                "title": title,
                "status": _entry_status(current, files),
            }
        )

    return store.update(entry_id, change)


def _synced_file(
    item: EntryFile,
    record: JobRecord | None,
    *,
    unavailable: bool = False,
) -> EntryFile:
    # Once an artifact is known to be unreadable, the cached failure record
    # cannot make it readable again. Replacing the original creates a new ID.
    if unavailable or item.status == "unavailable":
        return item.model_copy(update={"status": "unavailable"})
    if record is None:
        if item.status in {"failed", "cancelled", "expired", "unavailable"}:
            return item
        # Without a persisted state the file is considered expired; if it is
        # unreadable we mark it unavailable – fallback is to select the original.
        return item.model_copy(update={"status": "unavailable" if unavailable else "expired"})
    with record.lock:
        status: FileStatus = {
            JobStatus.QUEUED: "queued",
            JobStatus.RUNNING: "processing",
            JobStatus.COMPLETED: "review",
            JobStatus.FAILED: "failed",
            JobStatus.CANCELLED: "cancelled",
        }[record.status]
        document = record.extracted_document
        counts = counts_by_category(record.interim_counts)
        error_code = record.error_code
    return item.model_copy(
        update={
            "status": status,
            "error_code": error_code if status == "failed" else None,
            "counts": counts,
            "pages": document.page_count if document is not None else item.pages,
            "ocr_used": document.ocr_used if document is not None else item.ocr_used,
        }
    )


def _is_settled(item: EntryFile) -> bool:
    return item.status not in {"queued", "processing"}


def _entry_status(current: Entry, files: tuple[EntryFile, ...]) -> Any:
    if not files:
        return current.status
    if current.status == "saved":
        # Saved entries stay saved even when re-reading an expired original
        # during a “Edit again” operation; the target files remain present.
        return current.status
    if any(not _is_settled(item) for item in files):
        return "processing"
    if current.status in {"saved", "copied"}:
        return current.status
    if all(item.status in {"failed", "cancelled"} for item in files):
        return "failed"
    return "review"


def _final_title(files: tuple[EntryFile, ...], records: dict[str, JobRecord]) -> str:
    sources: list[TitleSource] = []
    exclude: set[str] = set()
    for item in files:
        record = records.get(item.id)
        text = ""
        if record is not None:
            with record.lock:
                text = record.source_text or ""
                if record.result is not None:
                    exclude.update(
                        text[finding.start : finding.end]
                        for finding in (*record.result.confirmed, *record.result.suspicious)
                    )
        sources.append(TitleSource(item.name, text=text, ocr_used=item.ocr_used))
    return documents_title(sources, exclude_values=exclude)


def progress(services: PhaseOneServices, entry_id: str) -> dict[str, Any]:
    entry = sync_entry(services, entry_id)
    manager = services.jobs()
    files: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    for item in entry.files:
        phase: str | None = None
        percent = 100 if _is_settled(item) else 0
        questions: int | None = None
        try:
            record = manager.get(item.id)
        except JobNotFoundError:
            record = None
        if record is not None:
            status = record.status_dict()
            percent = int(status["progress"])  # type: ignore[arg-type]
            phase = record.current_phase
            if record.status is JobStatus.QUEUED:
                phase = None
            if record.status is JobStatus.COMPLETED:
                questions = _open_questions(manager, item.id, entry.masking)
        totals.update(item.counts)
        files.append(
            {
                "id": item.id,
                "name": item.name,
                "status": item.status,
                "phase": phase,
                "progress": percent,
                "counts": dict(item.counts),
                "questions": questions,
                "error_code": item.error_code,
            }
        )
    done = sum(1 for item in entry.files if _is_settled(item))
    return {
        "entry_id": entry.id,
        "status": entry.status,
        "files": files,
        "totals": {category: totals[category] for category in CATEGORY_IDS if totals[category]},
        "done": done,
        "total": len(entry.files),
        "ready": any(item.status == "review" for item in entry.files),
    }


def _open_questions(manager: Any, job_id: str, spec: MaskingSpec) -> int | None:
    """Count open questions for a completed file that affect entry masking.

    Questions about categories that stay in clear text are ignored because
    masking would have no effect. Only the presence of a question matters,
    not its value. Anything already decided by the local AI (spec §K) is not
    considered an open question.
    """

    try:
        detection = detection_for_profile(manager.get_result(job_id), base_profile())
    except (JobNotFoundError, JobNotReadyError):
        return None
    ids = question_ids(detection, base_profile())
    kept = spec.kept_plain_entity_types
    return sum(
        1
        for kind, findings in (
            (FindingKind.CONFIRMED, detection.confirmed),
            (FindingKind.SUSPICIOUS, detection.suspicious),
        )
        for finding in findings
        if finding.entity_type not in kept
        and (identifier := finding_id(finding, kind=kind)) in ids
        and not _decided_by_ai(detection, identifier)
    )


def _decided_by_ai(detection: DetectionResult, identifier: str) -> bool:
    assessment = detection.assessments.get(identifier)
    return assessment is not None and assessment.decided


# -- Align drafts -------------------------------------------------------------


def reconcile_drafts(services: PhaseOneServices) -> int:
    """Mark files with unreadable manifests as ``unavailable``.

    This is cheap because only small manifests (<1 KB) are read; the original
    loading and detection steps run later. Returns the number of files marked.
    """

    store = services.entries()
    drafts = services.drafts()
    marked = 0
    for entry in store.list():
        key = store.draft_key(entry.id)
        if entry.kind != "documents" or key is None:
            continue
        candidates = [
            item.id for item in entry.files if item.status not in {"expired", "unavailable"}
        ]
        if not candidates:
            continue
        try:
            opened = drafts.open(entry.id, key)
        except DraftError:
            unreadable = set(candidates)
        else:
            unreadable = set()
            for file_id in candidates:
                try:
                    opened.read_manifest(file_id)
                except DraftError:
                    unreadable.add(file_id)
        if not unreadable:
            continue

        def mark(current: Entry, unreadable: set[str] = unreadable) -> Entry:
            files = tuple(
                item.model_copy(update={"status": "unavailable"}) if item.id in unreadable else item
                for item in current.files
            )
            return current.model_copy(
                update={"files": files, "status": _entry_status(current, files)}
            )

        with suppress(EntryNotFoundError):
            store.update(entry.id, mark)
            marked += len(unreadable)
    return marked


def resume_interrupted(services: PhaseOneServices, *, wait: bool = True) -> int:
    """Resume work that was interrupted, using low priority.

    An entry is considered finished when the user returns; after resuming,
    ``sync_entry`` aligns status and counters in the history.
    """

    store = services.entries()
    manager = services.jobs()
    resumed: dict[str, list[JobRecord]] = {}
    for entry in store.list():
        if entry.kind != "documents" or store.draft_key(entry.id) is None:
            continue
        for item in entry.files:
            if item.status not in {"queued", "processing"} or manager.is_loaded(item.id):
                continue
            try:
                record = manager.restore(draft_jobs.load_job(services, item.id, background=True))
            except JobNotFoundError:
                continue
            resumed.setdefault(entry.id, []).append(record)
    for entry_id, records in resumed.items():
        if wait:
            for record in records:
                if record.future is not None:
                    with suppress(Exception):
                        record.future.result(timeout=manager.timeout_seconds)
        with suppress(EntryNotFoundError):
            sync_entry(services, entry_id)
    return sum(len(records) for records in resumed.values())


# -- Apply -------------------------------------------------------------------


def apply_job(
    services: PhaseOneServices,
    job_id: str,
    request: Any,
    revision: int,
) -> dict[str, Any]:
    """Apply API v2 to a job of an entry – always as a dry-run."""

    manager = services.jobs()
    with manager.lease(job_id) as record:
        if record.profile_id != BASE_PROFILE_ID or record.entry_id is None:
            raise WorkflowError(
                "v2_job_required",
                "Dieser Job wurde nicht ueber einen Eintrag eingereicht.",
            )
        profile = base_profile()
        detection = detection_for_profile(manager.get_result(job_id), profile)
        text = manager.get_source_text(job_id)
        spec: MaskingSpec = request.masking
        # Decisions are permanent even if preview or dry-run fails or the app crashes
        # (ensures consistent state).
        with record.lock:
            if revision != record.processing_revision:
                raise JobNotReadyError("Eine neuere Review-Entscheidung liegt bereits vor.")
            _remember_review(services, record.entry_id, job_id, spec, request.review)
        result, processing = dry_run(services, text, detection, spec, request.review)
        placements = job_placements(services, record, text, result)
        stored = manager.store_processing(job_id, revision, processing, result)
        if not stored:
            raise JobNotReadyError(
                "Der Apply-Stand wurde durch eine neuere Review-Entscheidung ersetzt."
            )
        entry_id = record.entry_id
        with record.lock:
            if revision == record.processing_revision:
                _remember_review(services, entry_id, job_id, spec, request.review, result)
    return {
        "job_id": job_id,
        "entry_id": entry_id,
        **apply_result(spec, result),
        "placements": placements,
    }


def job_placements(
    services: PhaseOneServices,
    record: JobRecord,
    text: str,
    result: ProcessingResult,
) -> list[dict[str, Any]]:
    """Placement plan for fixed pages; otherwise empty.

    The sidecar reads page size, rotation and word boxes of a PDF page once per job
    and keeps them in memory like the page images. If the plan fails, the view shows
    only the marked spots – saving validates anyway.
    """

    document = record.extracted_document
    if document is None or document.format not in {
        DocumentFormat.PDF,
        DocumentFormat.PNG,
        DocumentFormat.JPEG,
    }:
        return []
    cache = services.job_memory()

    def geometry(page_index: int) -> PageGeometry:
        if document.format is not DocumentFormat.PDF:
            return image_page_geometry(document)
        key = ("geometry", page_index)
        cached = cache.get(record.id, key)
        if cached is not None:
            raw = json.loads(cached)
            return PageGeometry(
                width=raw["width"],
                height=raw["height"],
                rotation=raw["rotation"],
                matrix=tuple(raw["matrix"]),
                words=tuple(tuple(word) for word in raw["words"]),
                styles=tuple(TextStyle.from_tuple(style) for style in raw["styles"]),
            )
        fresh = pdf_page_geometry(record.source_path, page_index)
        cache.put(
            record.id,
            key,
            json.dumps(
                {
                    "width": fresh.width,
                    "height": fresh.height,
                    "rotation": fresh.rotation,
                    "matrix": fresh.matrix,
                    "words": fresh.words,
                    "styles": [style.as_tuple() for style in fresh.styles],
                }
            ).encode(),
        )
        return fresh

    try:
        return plan_placements(document, text, result, geometry, record.source_path)
    except (ExportValidationError, OSError, RuntimeError, ValueError):
        return []


def dry_run(
    services: PhaseOneServices,
    text: str,
    detection: DetectionResult,
    spec: MaskingSpec,
    review: Any,
) -> tuple[ProcessingResult, Any]:
    decisions = tuple(
        ReviewDecision(item.finding_id, ReviewAction(item.action)) for item in review.decisions
    )
    manual = tuple(
        ManualFinding(
            entity_type=item.entity_type,
            start=python_offset_from_utf16(text, item.start),
            end=python_offset_from_utf16(text, item.end),
            corrects_finding_id=item.corrects_finding_id,
        )
        for item in review.manual
    )
    current = services.settings_store().get()
    # Placeholder names follow the paragraph language if the setting allows and detection
    # identified languages; otherwise they use the app language.
    placeholder_languages = (
        tuple((segment.start, segment.end, segment.language) for segment in detection.languages)
        if current.document_languages.placeholders_in_document_language
        else ()
    )
    request = processing_request(
        spec,
        decisions=decisions,
        manual_findings=manual,
        unresolved=UnresolvedPolicy(review.unresolved),
        question_ids=question_ids(detection, base_profile()),
        placeholder_language=current.general.language,
        placeholder_languages=placeholder_languages,
    )
    result = services.processor().apply(text, detection, request, commit=False)
    return result, request


def review_state(review: Any) -> FileReview:
    """The empty review state of a file as stored in the entry."""

    return FileReview(
        decisions=tuple(
            DecisionState(finding_id=item.finding_id, action=item.action, by=item.by)
            for item in review.decisions
        ),
        manual=tuple(
            ManualState(
                entity_type=item.entity_type,
                start=item.start,
                end=item.end,
                corrects_finding_id=item.corrects_finding_id,
            )
            for item in review.manual
        ),
    )


def _remember_review(
    services: PhaseOneServices,
    entry_id: str,
    file_id: str,
    spec: MaskingSpec,
    review: Any,
    result: ProcessingResult | None = None,
) -> None:
    """Records masking, review state, and label on the entry (without content)."""

    file_review = review_state(review)
    label = label_facts(spec, result)["label"] if result is not None else None

    def change(current: Entry) -> Entry:
        files = tuple(
            item.model_copy(update={"label": label})
            if item.id == file_id and label is not None
            else item
            for item in current.files
        )
        return current.model_copy(
            update={
                "masking": spec,
                "files": files,
                "review": {**current.review, file_id: file_review},
            }
        )

    services.entries().update(entry_id, change)


def apply_result(spec: MaskingSpec, result: ProcessingResult) -> dict[str, Any]:
    """Part of the response shared by Apply and text mode (``ApplyResult``)."""

    full = result.as_dict()
    applied = [
        {
            "finding_id": item.finding_id,
            "kind": item.kind.value,
            "entity_type": item.entity_type,
            "category": _category_id(item.entity_type),
            "source": item.source,
            "mode": item.mode.value,
            "reversibility": item.reversibility.value,
            "original_start": item.original_start,
            "original_end": item.original_end,
            "output_start": item.output_start,
            "output_end": item.output_end,
        }
        for item in result.applied_findings
    ]
    return {
        "offset_encoding": "utf-16",
        "text": result.text,
        "masking": spec,
        "labels": label_facts(spec, result),
        "applied_findings": applied,
        "applied_by_category": counts_by_category(
            Counter(item.entity_type for item in result.applied_findings)
        ),
        "residual_risk": full["residual_risk"],
        "facts": full["result_facts"],
        "report": full["report"],
    }


def _category_id(entity_type: str) -> str | None:
    category = CATEGORY_BY_ENTITY.get(entity_type)
    return category.id if category is not None else None


# -- Occurrences ---------------------------------------------------------------

MAX_OCCURRENCES = 10_000


def occurrences(
    services: PhaseOneServices,
    entry_id: str,
    value: str,
    *,
    ignore_case: bool,
) -> dict[str, Any]:
    """All occurrences of a highlighted string across all files of the entry.

    Only whole-word matches count: "Hofer" does not match "Hoferstrasse".
    The value is not compiled or cached, only escaped.
    """

    needle = value.strip()
    if len(needle) < 2:
        raise WorkflowError("too_short", "Die Markierung ist zu kurz.")
    entry = sync_entry(services, entry_id)
    manager = services.jobs()
    pattern = re.compile(
        rf"(?<!\w){re.escape(needle)}(?!\w)",
        re.IGNORECASE if ignore_case else 0,
    )
    matches: list[dict[str, Any]] = []
    files_with_matches: set[str] = set()
    for item in entry.files:
        try:
            text = manager.get_source_text(item.id)
            detection = manager.get_result(item.id)
        except (JobNotFoundError, JobNotReadyError):
            # Skip files that are not finished or have expired.
            continue
        occupied = [
            (finding.start, finding.end)
            for finding in (*detection.confirmed, *detection.suspicious)
        ]
        for match in pattern.finditer(text):
            start, end = match.span()
            matches.append(
                {
                    "file_id": item.id,
                    "start": utf16_offset(text, start),
                    "end": utf16_offset(text, end),
                    "covered": any(a < end and start < b for a, b in occupied),
                }
            )
            files_with_matches.add(item.id)
            if len(matches) > MAX_OCCURRENCES:
                raise WorkflowError("too_many", "Zu viele Vorkommen fuer eine Markierung.")
    return {
        "entry_id": entry.id,
        "offset_encoding": "utf-16",
        "matches": matches,
        "count": len(matches),
        "file_count": len(files_with_matches),
    }


# -- Text mode --------------------------------------------------------------


def with_ai_decisions(review: Any, detection: DetectionResult) -> Any:
    """Add AI decisions where none exist yet.

    Text mode has no question cards; AI decisions follow the same rules as in the review view.
    User decisions always take precedence.
    """

    decided = {item.finding_id for item in review.decisions}
    extra = [
        ReviewDecisionIn(finding_id=identifier, action=assessment.suggestion, by="ai-suggestion")
        for identifier, assessment in detection.assessments.items()
        if assessment.decided and assessment.suggestion is not None and identifier not in decided
    ]
    if not extra:
        return review
    return review.model_copy(update={"decisions": [*review.decisions, *extra]})


def _text_detection(services: PhaseOneServices, text: str) -> DetectionResult:
    cached = services.cached_text_detection(text)
    if cached is not None:
        return cached
    profile = base_profile()
    runner = services.analyzer_for_profile(profile)
    detection = detection_for_profile(runner.analyze(text, progress=lambda _phase: None), profile)
    services.remember_text_detection(text, detection)
    return detection


def _check_text_size(services: PhaseOneServices, text: str) -> None:
    if len(text.encode("utf-8")) > services.settings.max_text_bytes:
        raise WorkflowError("too_large", "Der Text ist zu gross.")


def text_mask(services: PhaseOneServices, request: Any) -> dict[str, Any]:
    """Live masking: detect, dry-run, do not persist."""

    _check_text_size(services, request.text)
    detection = _text_detection(services, request.text)
    review = with_ai_decisions(request.review, detection)
    result, _ = dry_run(services, request.text, detection, request.masking, review)
    view = serialize_detection("text", request.text, detection, profile=base_profile())
    return {
        "findings": view["findings"],
        "counts": view["counts"],
        "result": apply_result(request.masking, result),
    }


def text_commit(services: PhaseOneServices, request: Any) -> dict[str, Any]:
    """\"Copy\": same calculation, then write mappings and create a history entry.

    Open questions are always masked here – copying is equivalent to saving.
    """

    _check_text_size(services, request.text)
    detection = _text_detection(services, request.text)
    review = with_ai_decisions(request.review, detection).model_copy(update={"unresolved": "mask"})
    result, _ = dry_run(services, request.text, detection, request.masking, review)
    store = services.entries()
    language = services.settings_store().get().general.language
    entry = store.create(
        kind="text",
        masking=request.masking,
        title=request.title or text_title(request.text, language=language),
        title_custom=request.title is not None,
        status="copied",
    )
    try:
        committed = services.vault().commit(result.pending_mappings, entry_id=entry.id)
        _audit_copy(services, entry.id, request.masking, detection, review, request.text, result)
    except Exception:
        # Without written mappings the text could not be restored,
        # and without an audit the action would not be traceable; therefore no
        # history entry may claim it was "copied".
        store.delete(entry.id)
        raise
    return {
        "entry": entry,
        "result": apply_result(request.masking, result),
        "committed_mappings": committed,
    }


def audit_decisions(
    detection: DetectionResult,
    review: Any,
    text: str,
) -> tuple[AuditDecision, ...]:
    """API v2 decisions for the audit: type, action, ID, origin – never values."""

    entities: dict[str, str] = {}
    for kind, findings in (
        (FindingKind.CONFIRMED, detection.confirmed),
        (FindingKind.SUSPICIOUS, detection.suspicious),
    ):
        for finding in findings:
            entities[finding_id(finding, kind=kind)] = runtime_entity_type(finding.entity_type)
    decisions: list[AuditDecision] = [
        AuditDecision(
            entity_type=entities[item.finding_id],
            action=AuditAction(item.action),
            finding_id=item.finding_id,
            origin=item.by,
        )
        for item in review.decisions
        if item.finding_id in entities
    ]
    for item in review.manual:
        # Verify offsets before a manual entry counts as a decision;
        # the position itself is never included in the audit.
        python_offset_from_utf16(text, item.start)
        python_offset_from_utf16(text, item.end)
        decisions.append(
            AuditDecision(
                entity_type=runtime_entity_type(item.entity_type),
                action=(
                    AuditAction.ADJUST_SPAN
                    if item.corrects_finding_id is not None
                    else AuditAction.ADD_MANUAL
                ),
                finding_id=item.corrects_finding_id,
                origin="user",
            )
        )
    return tuple(decisions)


def _audit_copy(
    services: PhaseOneServices,
    entry_id: str,
    spec: MaskingSpec,
    detection: DetectionResult,
    review: Any,
    text: str,
    result: ProcessingResult,
) -> None:
    """Audit for “Copy”: hash of the copied text, job, origin – never the content."""

    decisions = audit_decisions(detection, review, text)
    services.audit_log().record_text(
        result.text,
        profile_version=f"{BASE_PROFILE_ID}@{base_profile().version}",
        findings_by_entity=Counter(
            runtime_entity_type(finding.entity_type)
            for finding in (*detection.confirmed, *detection.suspicious)
        ),
        decisions=decisions,
        context=AuditContext(
            entry_id=entry_id,
            method=spec.method,
            style=spec.style,
            categories=spec.categories,
            protection_level=int(spec.protection_level),
            decided_by_user=sum(1 for item in decisions if item.origin == "user"),
            decided_by_ai=sum(1 for item in decisions if item.origin == "ai-suggestion"),
            masked_without_decision=result.report.unresolved_masked_count,
        ),
    )


__all__ = [
    "Rejection",
    "Upload",
    "WorkflowError",
    "apply_job",
    "apply_result",
    "audit_decisions",
    "default_masking",
    "dry_run",
    "get_view",
    "migrate_review",
    "occurrences",
    "progress",
    "put_view",
    "reconcile_drafts",
    "redetect_files",
    "redetect_status",
    "replace_file",
    "remove_file",
    "retry_file",
    "resume_interrupted",
    "review_state",
    "storage",
    "submit_files",
    "sync_entry",
    "text_commit",
    "text_mask",
]
