"""Isolated detection jobs; for entries only a view on the state.

API v2 jobs (files of an entry) are cached in memory since the state (spec §?):
original, extraction and detection are stored encrypted in ``drafts.py``. If a job is
missing, ``get()`` reloads it via the loader (``set_loader``). The TTL is a quiet period:
a job not touched for ``ttl_seconds`` is evicted from memory, but nothing is deleted.
The JobManager itself does not know the state – persistence is triggered via hooks
(``JobHooks``).

Plain-text jobs without an entry (old Analyze API) remain short-lived as before.
"""

from __future__ import annotations

import ctypes
import os
import secrets
import shutil
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from .audit import AuditDecision
from .detection import DetectionResult, DuplicateReviewCapacityError, Finding
from .extraction import (
    EmptyDocumentError,
    ExtractedDocument,
    ExtractionError,
    OcrUnavailableError,
    UnsupportedDocumentError,
)
from .processing import ProcessingRequest, ProcessingResult
from .qos import QosClass, set_thread_qos, user_initiated

#: Job IDs are 32 hex characters, e.g. ``secrets.token_hex(16)``. Also used for the
#: folder name and state file of a job.
_JOB_ID_LENGTH = 32
#: Limits for loaded jobs (spec §?).
DEFAULT_MAX_LOADED_JOBS = 64
DEFAULT_MAX_LOADED_BYTES = 768 * 1024 * 1024
#: A recently used job is exempt from eviction – otherwise a large entry could
#: push out its own files.
DEFAULT_EVICTION_GRACE_SECONDS = 30.0

PHASES = ("master_data", "rules", "ner", "suspicion")
#: API v2 jobs read their file only in the worker. Multiple files of an
#: entry are accepted immediately, and “Detection running” also indicates that
#: extraction (OCR can be the longest step) is in progress.
DEFERRED_PHASES = ("extraction", *PHASES)
JOB_ROOT_PREFIX = "presidio-sidecar-jobs-"
#: Staging folder used when saving an entry (``create_staging``).
STAGING_PREFIX = "save-"
PHASE_LABELS = {
    "extraction": "Text auslesen",
    "master_data": "Stammdaten",
    "rules": "Regeln und Prüfsummen",
    "ner": "Semantische Erkennung",
    "suspicion": "Verdachtsprüfung",
}


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})


class PhaseStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobError(RuntimeError):
    """Base class for job errors translated to HTTP responses."""


class JobNotFoundError(JobError):
    pass


class JobUnavailableError(JobNotFoundError):
    """The file has a state, but it is no longer readable."""


class JobNotReadyError(JobError):
    pass


class AnalysisRunner(Protocol):
    def analyze(
        self,
        text: str,
        *,
        progress: Callable[[str], None],
    ) -> DetectionResult: ...


#: Reads a submitted file in the worker (API v2).
Extractor = Callable[[bytes, str], ExtractedDocument]


@dataclass(frozen=True, slots=True)
class JobHooks:
    """Callbacks at job transitions – used to persist the state.

    They run outside the record lock, one per job, and never after an abort
    (except ``on_cancelled`` and the timeout in ``on_failed``). An error in a hook
    does not stop the job.
    """

    on_extracted: Callable[[ExtractedDocument], None] | None = None
    on_completed: Callable[[DetectionResult], None] | None = None
    on_failed: Callable[[str], None] | None = None
    on_cancelled: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class RestoredJob:
    """A job as described by its saved state (``JobManager.restore``).

    ``status`` ``pending`` means the work was still running when the sidecar stopped.
    Without ``extracted_document`` it resumes at extraction, otherwise at detection.
    """

    id: str
    entry_id: str
    filename: str
    profile_id: str
    source_size: int
    created_at: datetime
    status: Literal["pending", "completed", "failed", "cancelled"]
    error_code: str | None = None
    extracted_document: ExtractedDocument | None = None
    result: DetectionResult | None = None
    #: Provides the original at the given path (decrypted).
    write_source: Callable[[Path], object] | None = None
    extractor: Extractor | None = None
    analysis_runner: AnalysisRunner | None = None
    hooks: JobHooks | None = None
    #: Continue on start: low priority (QoS ``utility``).
    background: bool = False


#: Finds the saved state for a job ID; raises ``JobNotFoundError`` otherwise.
JobLoader = Callable[[str], RestoredJob]


@dataclass(slots=True)
class JobRecord:
    id: str
    filename: str
    workspace: Path
    text_path: Path
    source_path: Path
    created_at: datetime
    deadline_at: datetime
    expires_at: datetime
    extracted_document: ExtractedDocument | None = field(default=None, repr=False)
    profile_id: str | None = None
    analysis_runner: AnalysisRunner | None = field(default=None, repr=False)
    status: JobStatus = JobStatus.QUEUED
    phases: dict[str, PhaseStatus] = field(
        default_factory=lambda: {phase: PhaseStatus.PENDING for phase in PHASES}
    )
    #: API v2 entry this file belongs to.
    entry_id: str | None = None
    extractor: Extractor | None = field(default=None, repr=False)
    source_size: int = 0
    #: Interim count per entity type after each pipeline stage – counters only, no values
    #: (Board "Detection running").
    interim_counts: Counter[str] = field(default_factory=Counter, repr=False)
    #: UI-only error code (``unsupported``, ``empty``, ``ocr_unavailable``,
    #: ``too_large``, ``extraction_failed``, ``analysis_failed``,
    #: ``timeout``, ``cancelled``).
    error_code: str | None = None
    result: DetectionResult | None = None
    source_text: str | None = field(default=None, repr=False)
    processing_request: ProcessingRequest | None = field(default=None, repr=False)
    processing_result: ProcessingResult | None = field(default=None, repr=False)
    processing_revision: int = field(default=0, repr=False)
    stored_processing_revision: int = field(default=0, repr=False)
    pending_processing_revision: int | None = field(default=None, repr=False)
    audit_decisions: tuple[AuditDecision, ...] = field(default=(), repr=False)
    active_leases: int = field(default=0, repr=False)
    #: Can be reloaded from saved state without loss.
    restorable: bool = field(default=False, repr=False)
    hooks: JobHooks | None = field(default=None, repr=False)
    #: Restores the original if the workspace disappeared
    #: (the job temp lives in cache and may be cleared at any time).
    source_writer: Callable[[Path], object] | None = field(default=None, repr=False)
    #: Job hooks run sequentially so “cancelled” isn’t overwritten by a later “read”.
    hook_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    hooks_disabled: bool = field(default=False, repr=False)
    background: bool = field(default=False, repr=False)
    error: str | None = None
    cancelled: bool = field(default=False, repr=False)
    future: Future[None] | None = field(default=None, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def status_dict(self) -> dict[str, object]:
        with self.lock:
            completed = sum(state is PhaseStatus.COMPLETED for state in self.phases.values())
            running = any(state is PhaseStatus.RUNNING for state in self.phases.values())
            progress = int((completed + (0.5 if running else 0)) / len(self.phases) * 100)
            if self.status is JobStatus.COMPLETED:
                progress = 100
            return {
                "id": self.id,
                "filename": self.filename,
                "format": self.source_path.suffix.casefold().removeprefix("."),
                "profile_id": self.profile_id,
                "extraction": (
                    {
                        "method": self.extracted_document.method,
                        "ocr_used": self.extracted_document.ocr_used,
                        "page_count": self.extracted_document.page_count,
                        "text_layer_pages": list(self.extracted_document.text_layer_pages),
                        "ocr_pages": list(self.extracted_document.ocr_pages),
                    }
                    if self.extracted_document is not None
                    else {
                        "method": "direct",
                        "ocr_used": False,
                        "page_count": None,
                        "text_layer_pages": [],
                        "ocr_pages": [],
                    }
                ),
                "status": self.status,
                "progress": progress,
                "phases": [
                    {
                        "id": phase,
                        "label": PHASE_LABELS[phase],
                        "status": state,
                    }
                    for phase, state in self.phases.items()
                ],
                "created_at": self.created_at.isoformat(),
                "deadline_at": self.deadline_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
                "error": self.error,
                "error_code": self.error_code,
                "entry_id": self.entry_id,
            }

    @property
    def current_phase(self) -> str | None:
        with self.lock:
            return next(
                (phase for phase, state in self.phases.items() if state is PhaseStatus.RUNNING),
                None,
            )


@dataclass(frozen=True, slots=True)
class ProcessingSnapshot:
    """Atomic snapshot of the apply state, safe to export."""

    revision: int
    request: ProcessingRequest
    result: ProcessingResult


class JobManager:
    """Thread-safe pool with an independent janitor for idle timeout and time limits."""

    def __init__(
        self,
        runner: AnalysisRunner,
        *,
        max_workers: int,
        ttl_seconds: float,
        max_text_bytes: int,
        timeout_seconds: float | None = None,
        root: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        on_failed: Callable[[Exception], None] | None = None,
        max_loaded_jobs: int | None = DEFAULT_MAX_LOADED_JOBS,
        max_loaded_bytes: int | None = DEFAULT_MAX_LOADED_BYTES,
        eviction_grace_seconds: float = DEFAULT_EVICTION_GRACE_SECONDS,
        on_hook_failed: Callable[[str, Exception], None] | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers muss mindestens 1 sein")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds muss groesser als 0 sein")
        if max_text_bytes <= 0:
            raise ValueError("max_text_bytes muss groesser als 0 sein")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds muss groesser als 0 sein")

        self.runner = runner
        self.max_workers = max_workers
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds or ttl_seconds
        self.max_text_bytes = max_text_bytes
        self.max_loaded_jobs = max_loaded_jobs
        self.max_loaded_bytes = max_loaded_bytes
        self.eviction_grace_seconds = eviction_grace_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._on_failed = on_failed
        self._on_hook_failed = on_hook_failed
        self._owns_root = root is None
        if root is None:
            temp_parent = Path(tempfile.gettempdir())
            self.root = Path(
                tempfile.mkdtemp(
                    prefix=f"{JOB_ROOT_PREFIX}{os.getpid()}-",
                    dir=temp_parent,
                )
            )
            self._scavenge_orphan_roots()
        else:
            self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _restrict_permissions(self.root, 0o700)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="pii-document",
            # macOS prefers these threads on performance cores (Phase T3) when waiting for
            # documents.
            initializer=user_initiated,
        )
        #: Submitted but not yet finished jobs. Read without lock (``busy``) so ``/health`` stays
        #: cheap.
        self._active_jobs = 0
        self._jobs: dict[str, JobRecord] = {}
        self._cleanup_retries: set[Path] = set()
        self._lock = threading.RLock()
        self._stop_janitor = threading.Event()
        self._janitor: threading.Thread | None = None
        self._closed = False
        self._unhealthy_reason: str | None = None
        #: Holds in-memory data per job (page images, layout maps) and releases it when the job
        #: disappears.
        self._removal_listeners: list[Callable[[str], None]] = []
        #: Loads missing jobs from the draft (``set_loader``) sequentially to avoid duplicate jobs;
        #: ``forget_entry`` also waits for any ongoing load.
        self._loader: JobLoader | None = None
        self._load_lock = threading.RLock()

    def submit_text(self, text: str, *, filename: str = "dokument.txt") -> JobRecord:
        encoded = text.encode("utf-8")
        if not encoded:
            raise ValueError("Das Dokument ist leer.")
        if len(encoded) > self.max_text_bytes:
            raise ValueError(f"Das Dokument ist groesser als {self.max_text_bytes} Byte.")

        return self._submit(
            text,
            source_bytes=encoded,
            filename=filename,
            extracted_document=None,
            profile_id=None,
        )

    def submit_deferred(
        self,
        source_bytes: bytes,
        *,
        filename: str,
        extractor: Extractor,
        profile_id: str,
        entry_id: str,
        analysis_runner: AnalysisRunner | None = None,
        job_id: str | None = None,
        hooks: JobHooks | None = None,
        source_writer: Callable[[Path], object] | None = None,
    ) -> JobRecord:
        """Accept a file and defer reading until a worker processes it (API v2).

        ``job_id`` is supplied by the caller if the original is already stored under
        that ID (draft); ``hooks`` record intermediate states, and ``source_writer``
        restores the original if the working directory disappears.

        The caller validates format and size against the base profile; any issues
        discovered only during reading (empty, encrypted, missing OCR) are reported
        as ``error_code`` on the job rather than as an HTTP error – a bad file should
        not block other entries.
        """

        if not isinstance(source_bytes, bytes) or not source_bytes:
            raise ValueError("Das Dokument ist leer.")
        if not callable(extractor):
            raise TypeError("extractor muss aufrufbar sein.")
        return self._submit(
            None,
            source_bytes=source_bytes,
            filename=filename,
            extracted_document=None,
            profile_id=profile_id.strip().lower(),
            analysis_runner=analysis_runner,
            extractor=extractor,
            entry_id=entry_id,
            job_id=job_id,
            hooks=hooks,
            source_writer=source_writer,
        )

    def _submit(
        self,
        text: str | None,
        *,
        source_bytes: bytes,
        filename: str,
        extracted_document: ExtractedDocument | None,
        profile_id: str | None,
        analysis_runner: AnalysisRunner | None = None,
        extractor: Extractor | None = None,
        entry_id: str | None = None,
        job_id: str | None = None,
        hooks: JobHooks | None = None,
        source_writer: Callable[[Path], object] | None = None,
    ) -> JobRecord:
        if job_id is not None:
            _check_job_id(job_id)
        encoded = text.encode("utf-8") if text is not None else None
        if encoded is not None:
            if not encoded:
                raise ValueError("Das Dokument ist leer.")
            if len(encoded) > self.max_text_bytes:
                raise ValueError(f"Das Dokument ist groesser als {self.max_text_bytes} Byte.")

        with self._lock:
            if self._closed:
                raise RuntimeError("Der JobManager wurde bereits geschlossen.")
            if self._unhealthy_reason is not None:
                raise RuntimeError(
                    "Der Analyse-Worker ist nicht mehr gesund; Neustart erforderlich."
                )
            self._start_janitor()
            job_id = job_id or secrets.token_hex(16)
            if job_id in self._jobs:
                raise ValueError("Die Job-ID ist bereits vergeben.")
            workspace = self.root / job_id
            workspace.mkdir(mode=0o700)
            text_path = workspace / "input.txt"
            cleaned_name = safe_filename(filename)
            suffix = Path(cleaned_name).suffix.casefold() or ".bin"
            source_path = workspace / f"source{suffix}"
            try:
                if encoded is not None:
                    text_path.write_bytes(encoded)
                    _restrict_permissions(text_path, 0o600)
                source_path.write_bytes(source_bytes)
                _restrict_permissions(source_path, 0o600)
            except OSError:
                # Ensure a partially written document does not remain as an
                # unregistered plaintext folder.
                self._delete_workspace(workspace)
                raise
            now = self._clock()
            record = JobRecord(
                id=job_id,
                filename=cleaned_name,
                workspace=workspace,
                text_path=text_path,
                source_path=source_path,
                created_at=now,
                deadline_at=now + timedelta(seconds=self.timeout_seconds),
                expires_at=now + timedelta(seconds=self.ttl_seconds),
                extracted_document=extracted_document,
                profile_id=profile_id,
                analysis_runner=analysis_runner,
                entry_id=entry_id,
                extractor=extractor,
                source_size=len(source_bytes),
                restorable=hooks is not None,
                hooks=hooks,
                source_writer=source_writer,
            )
            if extractor is not None:
                record.phases = {phase: PhaseStatus.PENDING for phase in DEFERRED_PHASES}
            self._jobs[job_id] = record
            self._start(record)
        self._enforce_capacity()
        return record

    def set_loader(self, loader: JobLoader | None) -> None:
        """Load missing jobs from the saved state."""

        with self._lock:
            self._loader = loader

    def restore(self, spec: RestoredJob) -> JobRecord:
        """Recreate a job from its saved state.

        Completed, failed, or cancelled files remain as they were before the
        restart. If processing was still ongoing, it resumes at the point where
        the saved state ended: either during extraction or detection.
        """

        _check_job_id(spec.id)
        pending = spec.status == "pending"
        if (
            pending
            and spec.extracted_document is None
            and (spec.extractor is None or spec.write_source is None)
        ):
            raise ValueError("Zum Fortsetzen braucht es Original und Extraktor.")
        with self._load_lock:
            with self._lock:
                if self._closed:
                    raise RuntimeError("Der JobManager wurde bereits geschlossen.")
                existing = self._jobs.get(spec.id)
                if existing is not None:
                    return existing
                if pending and self._unhealthy_reason is not None:
                    raise RuntimeError(
                        "Der Analyse-Worker ist nicht mehr gesund; Neustart erforderlich."
                    )
                self._start_janitor()
                workspace = self.root / spec.id
                # A workspace from a previous load is treated as cache.
                self._delete_workspace(workspace)
                workspace.mkdir(mode=0o700)
            suffix = Path(safe_filename(spec.filename)).suffix.casefold() or ".bin"
            source_path = workspace / f"source{suffix}"
            try:
                if spec.write_source is not None:
                    spec.write_source(source_path)
                    _restrict_permissions(source_path, 0o600)
            except BaseException:
                self._delete_workspace(workspace)
                raise
            now = self._clock()
            record = JobRecord(
                id=spec.id,
                filename=safe_filename(spec.filename),
                workspace=workspace,
                text_path=workspace / "input.txt",
                source_path=source_path,
                created_at=spec.created_at,
                deadline_at=now + timedelta(seconds=self.timeout_seconds),
                expires_at=now + timedelta(seconds=self.ttl_seconds),
                extracted_document=spec.extracted_document,
                profile_id=spec.profile_id,
                analysis_runner=spec.analysis_runner,
                entry_id=spec.entry_id,
                extractor=spec.extractor,
                source_size=spec.source_size,
                restorable=True,
                hooks=spec.hooks,
                source_writer=spec.write_source,
                background=spec.background,
            )
            record.phases = {phase: PhaseStatus.PENDING for phase in DEFERRED_PHASES}
            document = spec.extracted_document
            if document is not None:
                record.phases["extraction"] = PhaseStatus.COMPLETED
            if spec.status == "completed":
                if spec.result is None or document is None:
                    raise ValueError("Ein fertiger Job braucht Auslesung und Erkennung.")
                record.status = JobStatus.COMPLETED
                record.result = spec.result
                record.source_text = document.text
                record.phases = {phase: PhaseStatus.COMPLETED for phase in DEFERRED_PHASES}
                record.interim_counts = Counter(
                    finding.entity_type
                    for finding in (*spec.result.confirmed, *spec.result.suspicious)
                )
            elif spec.status in {"failed", "cancelled"}:
                cancelled = spec.status == "cancelled"
                record.status = JobStatus.CANCELLED if cancelled else JobStatus.FAILED
                record.error_code = spec.error_code or ("cancelled" if cancelled else None)
                record.error = "Abgebrochen." if cancelled else "Analyse fehlgeschlagen."
                for phase, state in record.phases.items():
                    if state is not PhaseStatus.COMPLETED:
                        record.phases[phase] = PhaseStatus.FAILED
            with self._lock:
                if self._closed:
                    self._delete_workspace(workspace)
                    raise RuntimeError("Der JobManager wurde bereits geschlossen.")
                self._jobs[spec.id] = record
                if pending:
                    self._start(record)
        self._enforce_capacity()
        return record

    def _start(self, record: JobRecord) -> None:
        """Add a job to the pool; call only while holding ``_lock``."""

        record.future = self._executor.submit(self._run, record)
        self._active_jobs += 1
        record.future.add_done_callback(self._job_finished)

    def retry(
        self,
        job_id: str,
        prepare: Callable[[JobRecord | None], RestoredJob],
        *,
        allow_completed: bool = False,
    ) -> JobRecord:
        """Replace a failed job with the same ID.

        ``allow_completed``: also allow a completed job – “Neu erkennen”.
        Old worker hooks are finished or locked before the new commit point;
        concurrent loading cannot reuse an old error.
        """

        allowed = {JobStatus.FAILED, JobStatus.CANCELLED}
        if allow_completed:
            allowed.add(JobStatus.COMPLETED)
        with self._load_lock:
            with self._lock:
                previous = self._jobs.get(job_id)
            if previous is not None:
                with previous.hook_lock, previous.lock:
                    if previous.status not in allowed:
                        raise JobNotReadyError("Nur fehlgeschlagene Dateien erneut versuchen.")
                    if previous.active_leases:
                        raise JobNotReadyError("Die Datei wird gerade verwendet.")
                    previous.cancelled = True
                    previous.hooks_disabled = True
                    self.delete(job_id)
                    return self.restore(prepare(previous))
            return self.restore(prepare(None))

    def _job_finished(self, _future: Future[None]) -> None:
        with self._lock:
            self._active_jobs -= 1

    @property
    def busy(self) -> bool:
        """Are there running or waiting jobs? No lock, for live check."""

        return self._active_jobs > 0

    def get(self, job_id: str) -> JobRecord:
        """A job from memory—or freshly loaded from its saved state."""

        self.cleanup_expired()
        record = self._loaded(job_id)
        self._touch_record(record)
        return record

    def is_loaded(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._jobs

    def _loaded(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._jobs.get(job_id)
            loader = self._loader
            closed = self._closed
        if record is not None:
            return record
        if loader is None or closed or not _is_job_id(job_id):
            raise JobNotFoundError("Job nicht gefunden oder bereits geloescht.")
        with self._load_lock:
            with self._lock:
                record = self._jobs.get(job_id)
            if record is not None:
                return record
            try:
                return self.restore(loader(job_id))
            except JobError:
                raise
            except RuntimeError as error:
                # Closed or worker unhealthy: not "gone", just not now.
                raise JobNotReadyError("Die Datei laesst sich gerade nicht laden.") from error

    def _ensure_source(self, record: JobRecord) -> None:
        """Restore the original if its workspace folder is missing."""

        writer = record.source_writer
        if writer is None or record.source_path.is_file():
            return
        with record.lock:
            if record.source_path.is_file() or record.status is not JobStatus.COMPLETED:
                return
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            _restrict_permissions(self.root, 0o700)
            record.workspace.mkdir(mode=0o700, exist_ok=True)
            _restrict_permissions(record.workspace, 0o700)
            writer(record.source_path)
            _restrict_permissions(record.source_path, 0o600)

    def _touch_record(self, record: JobRecord) -> None:
        deadline = self._clock() + timedelta(seconds=self.ttl_seconds)
        with record.lock:
            if record.status in TERMINAL_STATUSES and record.expires_at < deadline:
                record.expires_at = deadline

    def get_result(self, job_id: str) -> DetectionResult:
        record = self.get(job_id)
        with record.lock:
            if record.status is JobStatus.FAILED:
                raise JobNotReadyError("Der Job ist fehlgeschlagen.")
            if record.status is not JobStatus.COMPLETED or record.result is None:
                raise JobNotReadyError("Die Erkennung ist noch nicht abgeschlossen.")
            return record.result

    def get_source_text(self, job_id: str) -> str:
        record = self.get(job_id)
        with record.lock:
            if record.status is not JobStatus.COMPLETED or record.source_text is None:
                raise JobNotReadyError("Der Quelltext ist nicht mehr verfuegbar.")
            return record.source_text

    def begin_processing(self, job_id: str) -> int:
        """Reserve a monotonically increasing processing revision for an incoming apply call.

        Multiple UI decisions may be computed in parallel. The revision is assigned
        before thread-pool work so that a later-finishing older request never overwrites
        a newer export state.
        """

        record = self.get(job_id)
        with record.lock:
            if record.status is not JobStatus.COMPLETED:
                raise JobNotReadyError("Der Job ist noch nicht abgeschlossen.")
            record.processing_revision += 1
            record.pending_processing_revision = record.processing_revision
            return record.processing_revision

    def store_processing(
        self,
        job_id: str,
        revision: int,
        request: ProcessingRequest,
        result: ProcessingResult,
        *,
        audit_decisions: tuple[AuditDecision, ...] = (),
    ) -> bool:
        """Record only the latest fully computed review state."""

        record = self.get(job_id)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("Apply-Revision muss eine positive Ganzzahl sein.")
        if not isinstance(request, ProcessingRequest) or not isinstance(result, ProcessingResult):
            raise TypeError("Ungueltiger Verarbeitungsstand.")
        if not all(isinstance(decision, AuditDecision) for decision in audit_decisions):
            raise TypeError("Ungueltige Audit-Entscheidungen.")
        with record.lock:
            if record.status is not JobStatus.COMPLETED:
                raise JobNotReadyError("Der Job ist noch nicht abgeschlossen.")
            if (
                revision != record.processing_revision
                or record.pending_processing_revision != revision
            ):
                return False
            record.processing_request = request
            record.processing_result = result
            record.stored_processing_revision = revision
            record.pending_processing_revision = None
            record.audit_decisions = tuple(audit_decisions)
            return True

    def abandon_processing(self, job_id: str, revision: int) -> bool:
        """Release only the current failed apply revision."""

        record = self.get(job_id)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("Apply-Revision muss eine positive Ganzzahl sein.")
        with record.lock:
            if record.pending_processing_revision != revision:
                return False
            record.pending_processing_revision = None
            return True

    def get_processing(
        self,
        job_id: str,
    ) -> tuple[ProcessingRequest, ProcessingResult]:
        snapshot = self.get_processing_snapshot(job_id)
        return snapshot.request, snapshot.result

    def get_processing_snapshot(self, job_id: str) -> ProcessingSnapshot:
        """Read result and revision under the same record lock."""

        record = self.get(job_id)
        with record.lock:
            if (
                record.status is not JobStatus.COMPLETED
                or record.processing_request is None
                or record.processing_result is None
                or record.pending_processing_revision is not None
            ):
                raise JobNotReadyError(
                    "Vor dem Export muss der aktuelle Review-Stand angewendet werden."
                )
            return ProcessingSnapshot(
                revision=record.stored_processing_revision,
                request=record.processing_request,
                result=record.processing_result,
            )

    def create_staging(self) -> Path:
        """Private staging folder for storing an entry.

        Located alongside job work folders in the same private root (in the app:
        the shell's temp area). The shell verifies hash and size before publishing
        to the target folder. Orphaned folders disappear with the job root.
        """

        with self._lock:
            if self._closed:
                raise RuntimeError("Der JobManager wurde bereits geschlossen.")
            staging = self.root / f"{STAGING_PREFIX}{secrets.token_hex(12)}"
            staging.mkdir(mode=0o700)
        _restrict_permissions(staging, 0o700)
        return staging

    def remove_staging(self, staging: Path) -> None:
        # Only direct staging folders created by this manager.
        if staging.parent != self.root or not staging.name.startswith(STAGING_PREFIX):
            raise RuntimeError("Unsicherer Bereitstellungspfad; Loeschung verweigert.")
        if staging.exists():
            shutil.rmtree(staging, onexc=_ignore_missing_during_rmtree)

    def jobs_for_entry(self, entry_id: str) -> tuple[JobRecord, ...]:
        with self._lock:
            return tuple(record for record in self._jobs.values() if record.entry_id == entry_id)

    def touch(self, job_ids: Iterable[str]) -> None:
        """Extend the idle timeout of loaded, completed jobs; do not reload them.

        Running jobs keep their own timeout and remain untouched.
        """

        with self._lock:
            records = [self._jobs[job_id] for job_id in job_ids if job_id in self._jobs]
        for record in records:
            self._touch_record(record)

    def forget_entry(self, entry_id: str, *, timeout: float = 10.0) -> int:
        """Remove all jobs for an entry from memory – for deletion.

        Running jobs are cancelled without waiting: the entry is already locked
        at this point (spec §3.6). Ongoing exports and Apply calls (leases) may run
        until ``timeout``; afterwards the data is removed anyway.
        """

        with self._load_lock:
            with self._lock:
                records = [record for record in self._jobs.values() if record.entry_id == entry_id]
            for record in records:
                with record.lock:
                    record.cancelled = True
                    if record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                        if record.future is not None:
                            record.future.cancel()
                        record.status = JobStatus.CANCELLED
                        record.error = "Abgebrochen."
                        record.error_code = "cancelled"
            deadline = time.monotonic() + timeout
            while any(record.active_leases for record in records):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
            with self._lock:
                removed = [record for record in records if self._jobs.get(record.id) is record]
                for record in removed:
                    del self._jobs[record.id]
        self._announce_removed(record.id for record in removed)
        for record in removed:
            self._delete_workspace(record.workspace)
        return len(removed)

    def cancel(self, job_id: str) -> None:
        """Cancel a queued or running job and clean it up.

        A native inference worker cannot be interrupted. It finishes computation,
        discards its result because ``cancelled`` is set, and its workspace is then
        deleted.
        """

        with self._load_lock:
            self._cancel_loaded(job_id)

    def _cancel_loaded(self, job_id: str) -> None:
        candidate = self._loaded(job_id)
        with candidate.lock, self._lock:
            record = self._jobs.get(job_id)
            if record is not candidate:
                raise JobNotFoundError("Job nicht gefunden oder bereits geloescht.")
            running = record.status in {JobStatus.QUEUED, JobStatus.RUNNING}
            if running:
                record.cancelled = True
                if record.future is not None:
                    record.future.cancel()
                record.status = JobStatus.CANCELLED
                record.error = "Abgebrochen."
                record.error_code = "cancelled"
                for phase, state in record.phases.items():
                    if state is not PhaseStatus.COMPLETED:
                        record.phases[phase] = PhaseStatus.FAILED
                del self._jobs[job_id]
        if not running:
            # Completed jobs follow the normal path, including lease checks.
            self.delete(job_id)
            return
        # "Abgebrochen" remains: after a restart the file stays that way.
        self._fire(record, "on_cancelled", allow_cancelled=True)
        self._announce_removed((record.id,))
        self._delete_workspace(record.workspace)

    def delete(self, job_id: str) -> None:
        """Remove a completed job from memory (entry state stays)."""

        with self._load_lock:
            self._delete_loaded(job_id)

    def _delete_loaded(self, job_id: str) -> None:
        with self._lock:
            candidate = self._jobs.get(job_id)
            if candidate is None:
                raise JobNotFoundError("Job nicht gefunden oder bereits geloescht.")
        # Do not wait under the global catalog lock for a long export.
        # After acquiring the record lock, re-validate the entry because TTL cleanup
        # or a parallel DELETE may have occurred.
        with candidate.lock, self._lock:
            record = self._jobs.get(job_id)
            if record is not candidate:
                raise JobNotFoundError("Job nicht gefunden oder bereits geloescht.")
            if record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                raise JobNotReadyError("Ein laufender Job kann nicht geloescht werden.")
            if record.active_leases:
                raise JobNotReadyError("Der Job wird gerade verarbeitet oder exportiert.")
            del self._jobs[job_id]
        self._announce_removed((record.id,))
        self._delete_workspace(record.workspace)

    @contextmanager
    def lease(self, job_id: str):
        """Protect job data during Apply/Export from idle timeout and DELETE."""

        candidate = self._loaded(job_id)
        self._touch_record(candidate)
        self._ensure_source(candidate)
        # Another export may hold the record lock for a long time. The global
        # lock stays free while waiting so that /health and other jobs remain responsive.
        with candidate.lock, self._lock:
            record = self._jobs.get(job_id)
            if record is not candidate:
                raise JobNotFoundError("Job nicht gefunden oder bereits geloescht.")
            if record.status is not JobStatus.COMPLETED:
                raise JobNotReadyError("Nur ein abgeschlossener Job kann exportiert werden.")
            if not record.workspace.is_dir() or not record.source_path.is_file():
                raise JobNotReadyError("Die kurzlebigen Jobdaten sind abgelaufen.")
            record.active_leases += 1
        try:
            yield record
        finally:
            with record.lock:
                record.active_leases = max(0, record.active_leases - 1)

    def cleanup_expired(self, now: datetime | None = None) -> int:
        # Old workspace must be removed before re-loading the same ID.
        with self._load_lock:
            return self._cleanup_expired(now)

    def _cleanup_expired(self, now: datetime | None = None) -> int:
        current = now or self._clock()
        expired: list[JobRecord] = []
        timed_out: list[JobRecord] = []
        self._retry_workspace_cleanup()
        with self._lock:
            for record in tuple(self._jobs.values()):
                # An export holds the record lock for the whole file verification.
                # The janitor must not block the global catalog lock, otherwise
                # even /health and independent jobs would hang. Occupied records
                # are re-checked on the next janitor run.
                if not record.lock.acquire(blocking=False):
                    continue
                try:
                    terminal = record.status in TERMINAL_STATUSES
                    if terminal and not record.active_leases and record.expires_at <= current:
                        # Cool-down passed: drop from memory. Jobs of an entry
                        # are re-loaded on next access.
                        expired.append(record)
                    elif not terminal and record.deadline_at <= current:
                        record.cancelled = True
                        if record.future is not None:
                            record.future.cancel()
                        record.status = JobStatus.FAILED
                        record.error = "Zeitlimit des Analysejobs überschritten."
                        record.error_code = "timeout"
                        for phase, status in record.phases.items():
                            if status is PhaseStatus.RUNNING:
                                record.phases[phase] = PhaseStatus.FAILED
                        record.expires_at = current + timedelta(seconds=self.ttl_seconds)
                        timed_out.append(record)
                        self._unhealthy_reason = (
                            "Ein Analyse-Worker hat sein Zeitlimit überschritten."
                        )
                finally:
                    record.lock.release()
            # Expired terminal jobs are still removed under the same manager lock.
            # A new export lease cannot capture them between selection and file deletion.
            for record in expired:
                if self._jobs.get(record.id) is record:
                    del self._jobs[record.id]
        removed = len(expired)
        for record in timed_out:
            self._fire(record, "on_failed", "timeout", allow_cancelled=True)
        self._announce_removed(record.id for record in (*expired, *timed_out))
        for record in (*expired, *timed_out):
            try:
                self._delete_workspace(record.workspace)
            except OSError:
                with self._lock:
                    self._cleanup_retries.add(record.workspace)
                raise
        if timed_out and self._on_failed is not None:
            self._on_failed(TimeoutError(self._unhealthy_reason))
        return removed + len(timed_out)

    def health_status(self) -> dict[str, str | bool | None]:
        with self._lock:
            reason = self._unhealthy_reason
        return {
            "healthy": reason is None,
            "reason": reason,
        }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._stop_janitor.set()
        if self._janitor is not None:
            self._janitor.join(timeout=2)
        # An external ML backend may hang during native inference. The HTTP
        # lifespan shutdown must not wait indefinitely; /health 503 triggers the
        # shell to hard-restart the sidecar process.
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            records = tuple(self._jobs.values())
            workspaces = [record.workspace for record in records]
            removed_ids = tuple(self._jobs)
            self._jobs.clear()
        for record in records:
            # A worker that ends after shutdown writes nothing more: its state
            # stays as-is, and work resumes after restart instead of being logged
            # as an error because its workspace is already missing.
            with record.lock:
                record.cancelled = True
        self._announce_removed(removed_ids)
        for workspace in workspaces:
            self._delete_workspace(workspace)
        if self._owns_root and self.root.exists():
            shutil.rmtree(self.root)

    def _run(self, record: JobRecord) -> None:
        if record.background:
            set_thread_qos(QosClass.UTILITY)
        try:
            self._work(record)
        finally:
            if record.background:
                user_initiated()

    def _work(self, record: JobRecord) -> None:
        try:
            with record.lock:
                if record.cancelled:
                    return
                record.status = JobStatus.RUNNING
            resumed = record.extracted_document
            if resumed is not None:
                # Resumed: extraction was already in the state.
                text = resumed.text
            elif record.extractor is not None:
                text = self._extract(record)
                if text is None:
                    return
                self._fire(record, "on_extracted", record.extracted_document)
            else:
                # ``Path.read_text`` normalises line endings (CRLF → LF). Document
                # segments and findings must stay aligned with the raw extracted text.
                text = record.text_path.read_bytes().decode("utf-8")
                # Plain text is only needed for the isolated folder transfer.
                # From here the worker runs in memory; a hanging model leaves no
                # indefinitely lingering input file.
                record.text_path.unlink(missing_ok=True)
            with record.lock:
                if record.cancelled:
                    return
            runner = record.analysis_runner or self.runner
            result = runner.analyze(
                text,
                progress=_JobProgress(self, record),
            )
            with record.lock:
                if record.cancelled:
                    return
                record.result = result
                record.source_text = text
                record.status = JobStatus.COMPLETED
                for phase in record.phases:
                    record.phases[phase] = PhaseStatus.COMPLETED
                record.interim_counts = Counter(
                    finding.entity_type for finding in (*result.confirmed, *result.suspicious)
                )
                record.expires_at = self._clock() + timedelta(seconds=self.ttl_seconds)
            self._fire(record, "on_completed", result)
        except Exception as error:
            with record.lock:
                if record.cancelled:
                    # Aborted: workspace already gone; subsequent error is not a finding.
                    return
                record.status = JobStatus.FAILED
                record.error = _safe_error(error)
                record.error_code = record.error_code or "analysis_failed"
                for phase, status in record.phases.items():
                    if status is PhaseStatus.RUNNING:
                        record.phases[phase] = PhaseStatus.FAILED
                record.expires_at = self._clock() + timedelta(seconds=self.ttl_seconds)
                error_code = record.error_code
            self._fire(record, "on_failed", error_code)
            if self._on_failed is not None:
                self._on_failed(error)

    def _extract(self, record: JobRecord) -> str | None:
        """Read the file in the worker; ``None`` means cancelled or failed."""

        assert record.extractor is not None
        self._set_phase(record, "extraction")
        try:
            document = record.extractor(record.source_path.read_bytes(), record.filename)
        except Exception as error:
            with record.lock:
                if record.cancelled:
                    return None
                record.error_code = _extraction_error_code(error)
            raise
        encoded = document.text.encode("utf-8")
        with record.lock:
            if record.cancelled:
                return None
            record.extracted_document = document
            if not encoded:
                record.error_code = "empty"
                raise EmptyDocumentError("Das Dokument enthaelt keinen Text.")
            if len(encoded) > self.max_text_bytes:
                record.error_code = "too_large"
                raise ValueError("Der extrahierte Text ist zu gross.")
        return document.text

    def _fire(
        self,
        record: JobRecord,
        name: str,
        *args: object,
        allow_cancelled: bool = False,
    ) -> None:
        hooks = record.hooks
        callback = getattr(hooks, name, None) if hooks is not None else None
        if callback is None:
            return
        with record.hook_lock:
            with record.lock:
                if record.hooks_disabled or (record.cancelled and not allow_cancelled):
                    return
            try:
                callback(*args)
            except Exception as error:  # noqa: BLE001 — the job itself is unaffected
                if self._on_hook_failed is not None:
                    with suppress(Exception):
                        self._on_hook_failed(name, error)

    def _enforce_capacity(self) -> int:
        """Maximum number of loaded jobs (LRU): evict the longest idle first.

        Only jobs that can be reloaded, are finished, not leased, and not recently used.
        """

        with self._load_lock:
            return self._evict_over_capacity()

    def _evict_over_capacity(self) -> int:
        if self.max_loaded_jobs is None and self.max_loaded_bytes is None:
            return 0
        now = self._clock()
        grace = timedelta(seconds=self.eviction_grace_seconds)
        ttl = timedelta(seconds=self.ttl_seconds)
        evicted: list[JobRecord] = []
        with self._lock:
            count = len(self._jobs)
            size = sum(_loaded_bytes(record) for record in self._jobs.values())

            def over() -> bool:
                return (self.max_loaded_jobs is not None and count > self.max_loaded_jobs) or (
                    self.max_loaded_bytes is not None and size > self.max_loaded_bytes
                )

            if not over():
                return 0
            for record in sorted(
                (record for record in self._jobs.values() if record.restorable),
                key=lambda item: item.expires_at,
            ):
                if not over():
                    break
                if not record.lock.acquire(blocking=False):
                    continue
                try:
                    if (
                        record.status not in TERMINAL_STATUSES
                        or record.active_leases
                        or record.expires_at - ttl > now - grace
                    ):
                        continue
                    del self._jobs[record.id]
                    evicted.append(record)
                    count -= 1
                    size -= _loaded_bytes(record)
                finally:
                    record.lock.release()
        self._announce_removed(record.id for record in evicted)
        for record in evicted:
            try:
                self._delete_workspace(record.workspace)
            except OSError:
                with self._lock:
                    self._cleanup_retries.add(record.workspace)
        return len(evicted)

    def on_removed(self, listener: Callable[[str], None]) -> None:
        """Notify ``listener`` for each job ID whose data is removed."""

        with self._lock:
            self._removal_listeners.append(listener)

    def _announce_removed(self, job_ids: Iterable[str]) -> None:
        with self._lock:
            listeners = tuple(self._removal_listeners)
        for job_id in tuple(job_ids):
            for listener in listeners:
                # A cache must never block cleanup of job data.
                with suppress(Exception):
                    listener(job_id)

    def _set_phase(self, record: JobRecord, phase: str) -> None:
        if phase not in record.phases:
            raise ValueError(f"Unbekannte Pipeline-Phase: {phase}")
        order = tuple(record.phases)
        requested_index = order.index(phase)
        with record.lock:
            for index, known_phase in enumerate(order):
                if index < requested_index:
                    record.phases[known_phase] = PhaseStatus.COMPLETED
                elif index == requested_index:
                    record.phases[known_phase] = PhaseStatus.RUNNING

    def _start_janitor(self) -> None:
        if self._janitor is not None:
            return
        interval = min(
            5.0,
            max(0.05, min(self.ttl_seconds, self.timeout_seconds) / 2),
        )
        self._janitor = threading.Thread(
            target=self._janitor_loop,
            args=(interval,),
            name="pii-job-ttl",
            daemon=True,
        )
        self._janitor.start()

    def _janitor_loop(self, interval: float) -> None:
        set_thread_qos(QosClass.UTILITY)
        while not self._stop_janitor.wait(interval):
            try:
                self.cleanup_expired()
                if self._owns_root:
                    self._scavenge_orphan_roots()
            except OSError as error:
                # Transient file locks (especially on Windows) must not permanently stop the sole
                # TTL janitor.
                if self._on_failed is not None:
                    self._on_failed(error)

    def _delete_workspace(self, workspace: Path) -> None:
        # Only direct children created by this manager may be removed recursively.
        if workspace.parent != self.root or not workspace.name:
            raise RuntimeError("Unsicherer Jobpfad; Loeschung verweigert.")
        if workspace.exists():
            shutil.rmtree(workspace, onexc=_ignore_missing_during_rmtree)

    def _retry_workspace_cleanup(self) -> None:
        with self._lock:
            pending = tuple(self._cleanup_retries)
        for workspace in pending:
            self._delete_workspace(workspace)
            with self._lock:
                self._cleanup_retries.discard(workspace)

    def _scavenge_orphan_roots(self) -> int:
        """Remove TTL-old job roots from abruptly terminated sidecar processes."""

        parent = self.root.parent
        cutoff = time.time() - self.ttl_seconds
        removed = 0
        for candidate in parent.glob(f"{JOB_ROOT_PREFIX}*"):
            if candidate == self.root or candidate.is_symlink() or not candidate.is_dir():
                continue
            try:
                if candidate.stat().st_mtime > cutoff:
                    continue
            except FileNotFoundError:
                continue
            owner_pid = _job_root_pid(candidate.name)
            if owner_pid is not None and _process_is_alive(owner_pid):
                continue
            # Candidate is a direct child of the system temp directory with a fixed app prefix; do
            # not delete resolved links/globs.
            if candidate.parent != parent or not candidate.name.startswith(JOB_ROOT_PREFIX):
                continue
            try:
                shutil.rmtree(candidate)
            except OSError:
                continue
            else:
                removed += 1
        return removed


def safe_filename(filename: str) -> str:
    candidate = Path(filename).name.strip()
    return candidate or "dokument.txt"


def _is_job_id(job_id: str) -> bool:
    return (
        isinstance(job_id, str)
        and len(job_id) == _JOB_ID_LENGTH
        and all(character in "0123456789abcdef" for character in job_id)
    )


def _check_job_id(job_id: str) -> None:
    if not _is_job_id(job_id):
        raise ValueError("Ungueltige Job-ID.")


def _loaded_bytes(record: JobRecord) -> int:
    """Rough size of a loaded job: original, text, segments."""

    document = record.extracted_document
    if document is None:
        return record.source_size
    return record.source_size + 2 * len(document.text) + 256 * len(document.segments)


class _JobProgress:
    """Job progress callback: set phase and count intermediate state."""

    def __init__(self, manager: JobManager, record: JobRecord) -> None:
        self._manager = manager
        self._record = record

    def __call__(self, phase: str) -> None:
        self._manager._set_phase(self._record, phase)

    def interim(self, findings: Iterable[Finding]) -> None:
        counts = Counter(finding.entity_type for finding in findings)
        with self._record.lock:
            self._record.interim_counts = counts


def _extraction_error_code(error: Exception) -> str:
    if isinstance(error, UnsupportedDocumentError):
        return "unsupported"
    if isinstance(error, EmptyDocumentError):
        return "empty"
    if isinstance(error, OcrUnavailableError):
        return "ocr_unavailable"
    if isinstance(error, ExtractionError):
        return "extraction_failed"
    return "extraction_failed"


def _safe_error(error: Exception) -> str:
    """Do not expose document values from exceptions in status/logs."""

    if isinstance(error, DuplicateReviewCapacityError):
        return "Analyse fehlgeschlagen: Zu viele wiederholte Fundstellen fuer ein sicheres Review."
    return f"Analyse fehlgeschlagen ({type(error).__name__})."


def _restrict_permissions(path: Path, mode: int) -> None:
    if os.name != "nt":
        path.chmod(mode)


def _ignore_missing_during_rmtree(
    _function: Callable[..., object],
    _path: str,
    error: BaseException,
) -> None:
    """A worker may have already safely removed its file concurrently."""

    if isinstance(error, FileNotFoundError):
        return
    raise error


def _job_root_pid(name: str) -> int | None:
    suffix = name.removeprefix(JOB_ROOT_PREFIX)
    raw_pid, separator, _ = suffix.partition("-")
    if not separator or not raw_pid.isdigit():
        return None
    return int(raw_pid)


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    if os.name == "nt":
        try:
            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[name-defined,attr-defined]
            if not process:
                return False
            exit_code = ctypes.c_ulong()  # type: ignore[name-defined]
            ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))  # type: ignore[name-defined,attr-defined]
            ctypes.windll.kernel32.CloseHandle(process)  # type: ignore[name-defined,attr-defined]
            return exit_code.value == 259
        except (AttributeError, OSError):
            return False
    return False
