"""Save an entry: batch export, commit, audit.

"Save all files" commits the whole entry:

1. For each reviewable file the sidecar recomputes the final masking and review state – using
   "mask when in doubt", regardless of the preview. Same calculation as Apply, so same result.
2. If the state contains irreversible replacements that have not been confirmed for this entry,
   the process aborts before any write (spec §4).
3. It exports each file to a private staging folder, using the same export chain as before –
   PDFs only after a successful render review. If a file fails, nothing is produced.
4. Only then does it write the mappings to the translation table (with the entry as source) and
   creates an empty audit record per file.
5. The shell verifies hash and size, publishes collision-free to the target folder and returns
   the paths (``record_outputs``). Only then is the entry considered saved.

Repeating is safe: identifiers are HMAC-deterministic, a second commit writes the same mappings.
"""

from __future__ import annotations

import re
import secrets
from collections import Counter
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from . import __version__
from .api_models import ManualFindingIn, ReviewDecisionIn, ReviewIn
from .audit import AuditContext
from .base_profile import BASE_PROFILE_ID, base_profile
from .catalog import CATEGORY_IDS
from .detection import DetectionResult
from .document_workflow import (
    DocumentWorkflowError,
    export_processed_document,
    pdf_retained_spans,
)
from .entries import (
    MAX_EXPORTS,
    Entry,
    EntryExport,
    EntryFile,
    ExportedFile,
    FileOutput,
    FileReview,
    confirmation_key,
)
from .exporting import (
    ExportError,
    ExportResult,
    PdfVerificationError,
    ReadbackError,
    export_plain_text,
)
from .findings_view import detection_for_profile
from .jobs import JobNotFoundError, JobNotReadyError, JobRecord
from .masking import MaskingSpec, counts_by_category, label_facts
from .processing import ProcessingResult
from .profiles import runtime_entity_type
from .services import PhaseOneServices
from .workflow import WorkflowError, audit_decisions, dry_run, review_state, sync_entry

#: Suffix for the protected copy name, in the app language.
NAME_SUFFIX = {"de": "_geschützt", "en": "_protected"}
_UNSAFE_NAME = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
_MAX_STEM = 150


class SaveError(WorkflowError):
    """A file could not be saved safely; nothing was written."""

    def __init__(self, code: str, message: str, *, file_id: str | None = None) -> None:
        super().__init__(code, message)
        self.file_id = file_id


# -- Name and format of the protected copy ----------------------------------


@dataclass(frozen=True, slots=True)
class OutputPlan:
    name: str
    format: str
    #: The copy is text instead of image or scanned PDF: fallback when OCR cannot read
    #: a hand-drawn replacement text.
    as_text: bool
    #: A scanned PDF saved with a text layer (E-L3).
    scan_layer: bool = False


def output_plan(
    name: str,
    *,
    document_format: str,
    ocr_used: bool,
    language: str = "de",
) -> OutputPlan:
    """``<name>_geschützt.<ext>`` – each format keeps its format.

    - Image (PNG/JPG): codes, invented names and placeholders are drawn into the
      pixels (E-L4), blackening and removal only fill.
    - PDF with OCR: overpainted, code as text, invisible text layer from the OCR
      (E-L3, replaces E12) – for both methods.

    If a drawn replacement text cannot be read back, saving falls back to
    :func:`text_fallback`; this cannot be predicted. The same rule applies to the
    preview in ``geschuetzterName()`` in the frontend.
    """

    stem, extension = _split_name(name)
    suffix = NAME_SUFFIX.get(language, NAME_SUFFIX["de"])
    return OutputPlan(
        name=f"{stem}{suffix}{extension}",
        format=document_format,
        as_text=False,
        scan_layer=ocr_used and document_format == "pdf",
    )


def text_fallback(plan: OutputPlan) -> OutputPlan:
    """Same copy as text file: ``<name>_geschützt.txt``."""

    dot = plan.name.rfind(".")
    stem = plan.name[:dot] if dot > 0 else plan.name
    return OutputPlan(name=f"{stem}.txt", format="txt", as_text=True)


def _split_name(name: str) -> tuple[str, str]:
    # Keep only the final path component, stripping control and special characters.
    # The name originates from the submitted document and ends up in the target folder.
    base = PureWindowsPath(PurePosixPath(name).name).name
    base = _UNSAFE_NAME.sub("_", base).strip().lstrip(".") or "dokument"
    dot = base.rfind(".")
    stem, extension = (base[:dot], base[dot:]) if dot > 0 else (base, "")
    return stem[:_MAX_STEM].rstrip() or "dokument", extension[:16]


# -- Save ---------------------------------------------------------------


@dataclass(slots=True)
class _Prepared:
    item: EntryFile
    record: JobRecord
    text: str
    detection: DetectionResult
    review: Any
    result: ProcessingResult
    plan: OutputPlan
    exported: ExportResult | None = None
    audit_id: int | None = None
    #: OCR did not read back replacement text from the image; text was saved instead.
    readback_failed: bool = False


def save_entry(services: PhaseOneServices, entry_id: str, request: Any) -> dict[str, Any]:
    entry = sync_entry(services, entry_id)
    if entry.kind != "documents":
        raise WorkflowError("wrong_kind", "Nur Dokument-Eintraege werden gespeichert.")
    if any(item.status in {"queued", "processing"} for item in entry.files):
        raise WorkflowError("still_processing", "Die Erkennung laeuft noch.")
    expired = [item.id for item in entry.files if item.status == "expired"]
    if expired:
        raise SaveError(
            "files_expired",
            "Die Daten einzelner Dateien sind abgelaufen; die Originale muessen neu "
            "eingelesen werden.",
            file_id=expired[0],
        )
    unavailable = [item.id for item in entry.files if item.status == "unavailable"]
    if unavailable:
        raise SaveError(
            "files_unavailable",
            "Die gespeicherte Kopie einzelner Dateien ist nicht mehr lesbar; das Original "
            "muss ausgewaehlt werden.",
            file_id=unavailable[0],
        )
    saveable = [item for item in entry.files if item.status == "review"]
    if not saveable:
        raise WorkflowError("nothing_to_save", "Es gibt keine pruefbare Datei.")
    submitted = {item.file_id: item.review for item in request.files}
    unknown = set(submitted) - {item.id for item in saveable}
    if unknown:
        raise WorkflowError("unknown_file", "Eine Datei gehoert nicht zu diesem Eintrag.")

    spec: MaskingSpec = request.masking
    profile = base_profile()
    manager = services.jobs()
    language = services.settings_store().get().general.language
    staging = manager.create_staging()
    try:
        with ExitStack() as leases:
            prepared: list[_Prepared] = []
            for item in saveable:
                try:
                    record = leases.enter_context(manager.lease(item.id))
                    detection = detection_for_profile(manager.get_result(item.id), profile)
                    text = manager.get_source_text(item.id)
                except (JobNotFoundError, JobNotReadyError) as error:
                    raise SaveError(
                        "files_expired",
                        "Die Daten einer Datei sind abgelaufen.",
                        file_id=item.id,
                    ) from error
                review = submitted.get(item.id) or _stored_review(entry, item.id)
                review = review.model_copy(update={"unresolved": "mask"})
                result, _ = dry_run(services, text, detection, spec, review)
                plan = output_plan(
                    item.name,
                    document_format=item.format,
                    ocr_used=item.ocr_used,
                    language=language,
                )
                prepared.append(_Prepared(item, record, text, detection, review, result, plan))

            irreversible = any(part.result.facts.has_irreversible_changes for part in prepared)
            # Confirmation applies to the masking it was given for; changing method or style
            # on a saved entry requires reconfirmation.
            confirmed = entry.irreversible_confirmed_for == confirmation_key(spec)
            if irreversible and not (request.confirm_irreversible or confirmed):
                raise WorkflowError(
                    "irreversible_confirmation_required",
                    "Irreversibles Speichern braucht eine Bestaetigung.",
                )

            for part in prepared:
                if part.result.applied_findings:
                    part.exported = _export(services, part, staging)

        committed = services.vault().commit(
            [mapping for part in prepared for mapping in part.result.pending_mappings],
            entry_id=entry_id,
        )
        for part in prepared:
            if part.exported is not None:
                part.audit_id = _audit(services, entry_id, spec, part)
    except Exception:
        _remove_quietly(manager, staging)
        raise

    outputs = {part.item.id: _file_output(spec, part) for part in prepared}
    reviews = {part.item.id: review_state(part.review) for part in prepared}

    def change(current: Entry) -> Entry:
        files = tuple(
            item.model_copy(update={"output": outputs[item.id], "label": outputs[item.id].label})
            if item.id in outputs
            else item
            for item in current.files
        )
        update: dict[str, Any] = {
            "masking": spec,
            "files": files,
            "review": {**current.review, **reviews},
        }
        if irreversible:
            update["irreversible_confirmed_for"] = confirmation_key(spec)
        return current.model_copy(update=update)

    try:
        entry = services.entries().update(entry_id, change)
    except Exception:
        _remove_quietly(manager, staging)
        raise
    # The sidecar remembers what was saved until the shell reports back.
    # If the user makes further changes meanwhile, the entry is still considered
    # saved only up to this state (spec §3.1).
    services.remember_staging(
        entry_id,
        staging,
        _StagedSave(masking=spec, revision=entry.draft_revision, outputs=outputs),
    )
    return {
        "entry": entry,
        "staging_directory": str(staging),
        "staged": [
            {
                "file_id": part.item.id,
                "name": part.plan.name,
                "path": str(part.exported.path),
                "sha256": part.exported.sha256,
                "size_bytes": part.exported.size_bytes,
            }
            for part in prepared
            if part.exported is not None
        ],
        "unchanged": [part.item.id for part in prepared if part.exported is None],
        "committed_mappings": committed,
    }


def _stored_review(entry: Entry, file_id: str) -> ReviewIn:
    stored = entry.review.get(file_id, FileReview())
    return ReviewIn(
        decisions=[
            ReviewDecisionIn(finding_id=item.finding_id, action=item.action, by=item.by)
            for item in stored.decisions
        ],
        manual=[
            ManualFindingIn(
                entity_type=item.entity_type,
                start=item.start,
                end=item.end,
                corrects_finding_id=item.corrects_finding_id,
            )
            for item in stored.manual
        ],
    )


def _export(services: PhaseOneServices, part: _Prepared, staging: Path) -> ExportResult:
    target_directory = staging / part.item.id
    target_directory.mkdir(mode=0o700)
    destination = target_directory / part.plan.name
    record = part.record
    try:
        if part.plan.as_text:
            return export_plain_text(record.source_path, destination, part.result.text)
        try:
            return _export_document(services, part, destination)
        except ReadbackError:
            # Image contains a code that is not reliably readable by humans or AI;
            # fall back to plain text and note the change in the view.
            part.plan = text_fallback(part.plan)
            part.readback_failed = True
            return export_plain_text(
                record.source_path, target_directory / part.plan.name, part.result.text
            )
    except PdfVerificationError as error:
        raise SaveError("pdf_check_failed", str(error), file_id=part.item.id) from error
    except (DocumentWorkflowError, ExportError, FileExistsError) as error:
        raise SaveError("export_failed", str(error), file_id=part.item.id) from error


def _export_document(
    services: PhaseOneServices, part: _Prepared, destination: Path
) -> ExportResult:
    record = part.record
    document = record.extracted_document
    return export_processed_document(
        record.source_path,
        destination,
        document=document,
        source_text=part.text,
        processing=part.result,
        pdf_retained_spans=pdf_retained_spans(part.text, part.detection, part.result),
        ocr_backend=(
            services.extractor().ocr_backend if document is not None and document.ocr_used else None
        ),
        ocr_languages=base_profile().pipeline.ocr_languages,
        pdf_ocr_dpi=services.extractor().pdf_ocr_dpi,
    )


def _audit(
    services: PhaseOneServices,
    entry_id: str,
    spec: MaskingSpec,
    part: _Prepared,
) -> int:
    assert part.exported is not None
    detection = part.detection
    counts = Counter(
        runtime_entity_type(finding.entity_type)
        for finding in (*detection.confirmed, *detection.suspicious)
    )
    origins = _origins(part.review)
    record = services.audit_log().record_export(
        part.exported.path,
        profile_version=f"{BASE_PROFILE_ID}@{base_profile().version}",
        findings_by_entity=counts,
        decisions=audit_decisions(detection, part.review, part.text),
        context=AuditContext(
            entry_id=entry_id,
            method=spec.method,
            style=spec.style,
            categories=spec.categories,
            protection_level=int(spec.protection_level),
            decided_by_user=origins["user"],
            decided_by_ai=origins["ai"],
            masked_without_decision=part.result.report.unresolved_masked_count,
        ),
    )
    if record.export_sha256 != part.exported.sha256:
        raise SaveError(
            "audit_mismatch",
            "Audit-Hash stimmt nicht mit dem Exportergebnis ueberein.",
            file_id=part.item.id,
        )
    return record.id


def _origins(review: Any) -> dict[str, int]:
    """“y decided by you · z clarified by the local AI”.

    A boundary correction is a pair of rejection and manual entry; it counts
    once, via the rejection.
    """

    user = sum(1 for item in review.decisions if item.by == "user")
    ai = sum(1 for item in review.decisions if item.by == "ai-suggestion")
    user += sum(1 for item in review.manual if item.corrects_finding_id is None)
    return {"user": user, "ai": ai}


def _file_output(spec: MaskingSpec, part: _Prepared) -> FileOutput:
    result = part.result
    report = result.report
    facts = label_facts(spec, result)
    origins = _origins(part.review)
    exported = part.exported
    verification = exported.verification if exported is not None else None
    return FileOutput(
        status="saved" if exported is not None else "unchanged",
        name=part.plan.name if exported is not None else None,
        format=part.plan.format if exported is not None else None,
        sha256=exported.sha256 if exported is not None else None,
        size_bytes=exported.size_bytes if exported is not None else 0,
        from_scan=part.plan.as_text and exported is not None,
        readback_failed=part.readback_failed and exported is not None,
        scan_text_layer=part.plan.scan_layer and exported is not None,
        label=facts["label"] if exported is not None else None,
        contains_surrogates=bool(facts["contains_surrogates"]),
        protection_level=int(facts["protection_level"]),
        masked=len(result.applied_findings),
        masked_by_category=counts_by_category(report.applied_counts_by_entity),
        masked_by_entity=dict(report.applied_counts_by_entity),
        decided_by_user=origins["user"],
        decided_by_ai=origins["ai"],
        masked_without_decision=report.unresolved_masked_count,
        kept_here=int(facts["kept_here_count"]),
        added_by_hand=report.manual_added_count,
        adjusted=report.adjusted_span_count,
        residual_risk=result.residual_risk.status.value,
        residual_classes=tuple(str(item) for item in result.residual_risk.classes),
        anonymous_blockers=tuple(facts["anonymous_blockers"]),
        rendered_pages=verification.rendered_pages if verification is not None else 0,
        text_extraction_checked=(
            verification.text_extraction_checked if verification is not None else False
        ),
        verified_targets=verification.verified_targets if verification is not None else 0,
        audit_id=part.audit_id,
    )


def _remove_quietly(manager: Any, staging: Path) -> None:
    with suppress(OSError, RuntimeError):
        manager.remove_staging(staging)


# -- Published ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _StagedSave:
    """What a save provided until the shell published it."""

    masking: MaskingSpec
    revision: int
    outputs: dict[str, FileOutput]


def record_outputs(services: PhaseOneServices, entry_id: str, request: Any) -> Entry:
    """The shell has published: remember paths, entry counts as saved.

    Each save is added to the export history; earlier exports remain both there
    and in the target folder – the shell numbers duplicate copies instead of
    overwriting them.
    """

    entry = services.entries().get(entry_id)
    saved = {
        item.id for item in entry.files if item.output is not None and item.output.status == "saved"
    }
    paths = {item.file_id: item.path for item in request.files}
    if set(paths) != saved:
        raise WorkflowError(
            "outputs_mismatch",
            "Die gemeldeten Dateien passen nicht zum letzten Speichern.",
        )
    if not _absolute(request.directory) or not all(_absolute(path) for path in paths.values()):
        raise WorkflowError("relative_path", "Pfade muessen absolut sein.")
    now = datetime.now(UTC)
    staged = services.staged_save(entry_id)

    def change(current: Entry) -> Entry:
        files = tuple(
            item.model_copy(update={"output_path": paths.get(item.id)}) for item in current.files
        )
        # If no save was recorded (sidecar restarted), keep the current state –
        # same outputs the entry already carries.
        masking = staged.masking if isinstance(staged, _StagedSave) else current.masking
        revision = staged.revision if isinstance(staged, _StagedSave) else current.draft_revision
        outputs = (
            staged.outputs
            if isinstance(staged, _StagedSave)
            else {item.id: item.output for item in current.files if item.output is not None}
        )
        record = EntryExport(
            id=secrets.token_hex(8),
            saved_at=now,
            masking=masking,
            directory=request.directory,
            revision=revision,
            files=tuple(
                ExportedFile(
                    file_id=item.id,
                    name=item.name,
                    format=item.format,
                    path=paths.get(item.id),
                    output=outputs[item.id],
                )
                for item in current.files
                if item.id in outputs
            ),
        )
        return current.model_copy(
            update={
                "files": files,
                "status": "saved",
                "saved_at": now,
                "output_dir": request.directory,
                "saved_revision": min(revision, current.draft_revision),
                "exports": (*current.exports, record)[-MAX_EXPORTS:],
            }
        )

    entry = services.entries().update(entry_id, change)
    services.release_staging(entry_id)
    return entry


def _absolute(path: str) -> bool:
    return PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()


# -- Entry report ------------------------------------------------------------


def entry_report(
    services: PhaseOneServices,
    entry_id: str,
    export_id: str | None = None,
) -> dict[str, Any]:
    """Facts for the entry report of a save – counts only, no content.

    Without ``export_id`` use the latest save. The report describes what was
    saved, not the draft that may already have further work (spec §3.3). An
    existing entry without export history reports as before from the entry.
    """

    entry = services.entries().get(entry_id)
    if entry.kind != "documents":
        raise WorkflowError("wrong_kind", "Einen Pruefbericht gibt es nur fuer Dokumente.")
    export = _export_record(entry, export_id)
    if export is None:
        outputs = [item.output for item in entry.files if item.output is not None]
        masking = entry.masking
        saved_at = entry.saved_at
        files = [
            {
                "id": item.id,
                "name": item.output.name if item.output and item.output.name else item.name,
                "format": item.format,
                "pages": item.pages,
                "ocr_used": item.ocr_used,
                "source_changed": item.source_changed,
                "output": item.output,
            }
            for item in entry.files
        ]
    else:
        outputs = [item.output for item in export.files]
        masking = export.masking
        saved_at = export.saved_at
        files = []
        for exported in export.files:
            current = entry.file(exported.file_id)
            files.append(
                {
                    "id": exported.file_id,
                    "name": exported.output.name or exported.name,
                    "format": exported.format,
                    "pages": current.pages if current is not None else None,
                    "ocr_used": current.ocr_used if current is not None else False,
                    "source_changed": current.source_changed if current is not None else False,
                    "output": exported.output,
                }
            )
    return {
        "entry_id": entry.id,
        "export_id": export.id if export is not None else None,
        "title": entry.title,
        "created_at": entry.created_at,
        "saved_at": saved_at,
        "masking": masking,
        "protection_level": int(masking.protection_level),
        "app_version": __version__,
        "profile_version": f"{BASE_PROFILE_ID}@{base_profile().version}",
        "files": files,
        "totals": _totals(outputs),
    }


def _export_record(entry: Entry, export_id: str | None) -> EntryExport | None:
    if export_id is None:
        return entry.exports[-1] if entry.exports else None
    found = next((item for item in entry.exports if item.id == export_id), None)
    if found is None:
        raise WorkflowError("unknown_export", "Diese Speicherung gehoert nicht zum Eintrag.")
    return found


def _totals(outputs: list[FileOutput]) -> FileOutput:
    by_category: Counter[str] = Counter()
    by_entity: Counter[str] = Counter()
    for output in outputs:
        by_category.update(output.masked_by_category)
        by_entity.update(output.masked_by_entity)
    statuses = [output.residual_risk for output in outputs]
    residual = next(
        (status for status in ("failed", "warning", "not_run") if status in statuses),
        "clear" if statuses else "not_run",
    )
    return FileOutput(
        status="saved" if any(output.status == "saved" for output in outputs) else "unchanged",
        size_bytes=sum(output.size_bytes for output in outputs),
        masked=sum(output.masked for output in outputs),
        masked_by_category={
            category: by_category[category] for category in CATEGORY_IDS if by_category[category]
        },
        masked_by_entity=dict(sorted(by_entity.items())),
        decided_by_user=sum(output.decided_by_user for output in outputs),
        decided_by_ai=sum(output.decided_by_ai for output in outputs),
        masked_without_decision=sum(output.masked_without_decision for output in outputs),
        kept_here=sum(output.kept_here for output in outputs),
        added_by_hand=sum(output.added_by_hand for output in outputs),
        adjusted=sum(output.adjusted for output in outputs),
        residual_risk=residual,  # type: ignore[arg-type]
        residual_classes=tuple(
            dict.fromkeys(klass for output in outputs for klass in output.residual_classes)
        ),
        rendered_pages=sum(output.rendered_pages for output in outputs),
        verified_targets=sum(output.verified_targets for output in outputs),
        protection_level=min((output.protection_level for output in outputs), default=1),
    )


__all__ = [
    "NAME_SUFFIX",
    "OutputPlan",
    "SaveError",
    "entry_report",
    "output_plan",
    "record_outputs",
    "save_entry",
]
