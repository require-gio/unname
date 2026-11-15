"""API v2 request and response models – source of truth for TypeScript types.

The UI no longer maintains its types for API v2 manually.
``scripts/export_api_schema.py`` writes a JSON schema from these models
to ``app/src/lib/sidecar/api.schema.json``; ``json-schema-to-typescript``
generates ``api.gen.ts`` from it. A contract test on each side fails if
the model, schema, and generated types diverge.

The endpoints of the old UI (``/profiles``, ``/jobs`` with
``profile_id``, ``/deanonymize`` …) keep their manually written types.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .drafts import EntryView
from .entries import Entry, EntryFile, EntryKind, EntryStatus, FileOutput, FileStatus
from .masking import AnonymousBlocker, MaskingSpec, ResultLabel
from .settings_store import AppSettings, MaskingDefaults
from .vocabulary import ListKind, Pattern, PatternIn, Term, TermIn, TermType


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Response(BaseModel):
    model_config = ConfigDict(frozen=True)


# -- Catalog -----------------------------------------------------------------


class CategoryOut(_Response):
    id: str
    labels: dict[str, str]
    entity_types: list[str]
    level: int
    default: bool
    locked: bool
    circumstances: bool


class CatalogDefaults(_Response):
    method: Literal["reversible", "irreversible"]
    style: Literal["codes", "surrogates", "placeholder", "mask", "remove"]
    categories: list[str]


class CatalogResponse(_Response):
    categories: list[CategoryOut]
    styles: dict[str, list[str]]
    style_modes: dict[str, str]
    placeholders: dict[str, dict[str, str]]
    defaults: CatalogDefaults
    languages: list[str]


class CreateEntryRequest(_Request):
    kind: EntryKind = "documents"
    #: If unspecified, the default masking from settings applies.
    masking: MaskingSpec | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)


class PatchEntryRequest(_Request):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    masking: MaskingSpec | None = None
    status: EntryStatus | None = None


class EntryListResponse(_Response):
    entries: list[Entry]
    count: int


class RejectedFile(_Response):
    name: str
    #: ``format_not_allowed`` · ``unsupported_file_format`` ·
    #: ``missing_file_extension`` · ``file_too_large`` · ``empty`` · ``unreadable``
    code: str
    message: str


class SubmitFilesResponse(_Response):
    entry: Entry
    accepted: list[EntryFile]
    rejected: list[RejectedFile]


class RetryFileResponse(_Response):
    entry: Entry
    file: EntryFile


# Changes since detection: ``languages``,
# ``second_opinion``, ``ocr_engine``, ``ner``. Empty if explicitly
# redetected without changed settings.


class RedetectFile(_Response):
    file_id: str
    #: Changes since detection: ``languages``, ``second_opinion``, ``ocr_engine``,
    #: ``ner``. Empty if explicitly re-detected without changed settings.
    changes: list[str]
    #: Only files whose detection no longer matches the settings.
    reextract: bool


class RedetectStatusResponse(_Response):
    entry_id: str
    #: If unspecified: all files whose detection no longer matches.
    files: list[RedetectFile]


class RedetectRequest(_Request):
    #: When the retention period deletes the entry; ``None`` means "never".
    file_ids: list[str] | None = Field(default=None, max_length=200)


class RedetectResponse(_Response):
    entry: Entry
    files: list[RedetectFile]


class StorageEntry(_Response):
    entry_id: str
    #: Belegter Platz des Arbeitsstands (Originale, Auslesung, Erkennung).
    bytes: int
    #: When the retention period deletes the entry; ``None`` for never.
    delete_at: datetime | None


class StorageResponse(_Response):
    #: All under ``drafts.noindex`` — including remnants the cleaner has not yet
    #: removed.
    total_bytes: int
    #: Free space on the working state's drive; ``None`` if unknown.
    free_bytes: int | None
    low_space_bytes: int
    retention_days: int | None
    entries: list[StorageEntry]


class FileProgress(_Response):
    id: str
    name: str
    status: FileStatus
    phase: str | None
    progress: int
    counts: dict[str, int]
    #: Questions about masking the entry, only when the file is complete ("2 brauchen Ihren Blick").
    questions: int | None
    error_code: str | None


class ProgressResponse(_Response):
    entry_id: str
    status: EntryStatus
    files: list[FileProgress]
    #: "Bisher gefunden" per category, across all files.
    totals: dict[str, int]
    done: int
    total: int
    #: At least one file is complete – "Pruefen" may appear.
    ready: bool


class OccurrencesRequest(_Request):
    text: str = Field(min_length=2, max_length=500)
    ignore_case: bool = False


class Occurrence(_Response):
    file_id: str
    start: int
    end: int
    #: The location is already (partially) within a detected match.
    covered: bool


class OccurrencesResponse(_Response):
    entry_id: str
    offset_encoding: Literal["utf-16"] = "utf-16"
    matches: list[Occurrence]
    count: int
    file_count: int


# -- Findings ----------------------------------------------------------------


class FindingContext(_Response):
    before: str
    match: str
    after: str


class FindingQuestion(_Response):
    #: ``suspicion``: "Looks like a name – but was not recognized".
    #: ``uncertain``: "Recognized as a name – but uncertain".
    kind: Literal["suspicion", "uncertain"]
    #: Number in reading order per file, starting at 1.
    n: int


class FindingAssessment(_Response):
    """Assessment by the local AI regarding a question (second opinion)."""

    label: Literal["person", "organization_or_product", "location", "term"]
    #: Confidence of the label, 0…1.
    confidence: float
    #: Probability that the location should be masked (person, organization, location).
    protect: float
    #: `accept` mask, `reject` keep, `None` uncertain.
    suggestion: Literal["accept", "reject"] | None
    #: The AI made its own decision; the UI shows it under "Locally resolved",
    #: reversible.
    decided: bool
    model: str


class FindingOut(_Response):
    id: str
    kind: Literal["confirmed", "suspicious"]
    type: str
    category: str | None
    score: float
    start: int
    end: int
    text: str
    context: FindingContext
    source: str
    detector: str | None
    review_required: bool
    auto_accepted: bool
    question: FindingQuestion | None
    assessment: FindingAssessment | None = None


class TextBlock(_Response):
    index: int
    start: int
    end: int
    kind: Literal["paragraph"] = "paragraph"
    #: Page from 1, if the source knows pages (PDF, scan).
    page: int | None
    #: Document language at the start of the paragraph; ``None`` if no language
    #: detection is available.
    language: Literal["de", "en"] | None = None


class FindingsCounts(_Response):
    confirmed: int
    suspicious: int
    total: int
    by_entity: dict[str, int]
    by_category: dict[str, int]
    questions: int


class FindingsResponse(_Response):
    job_id: str
    offset_encoding: Literal["utf-16"] = "utf-16"
    text: str
    confirmed: list[FindingOut]
    suspicious: list[FindingOut]
    findings: list[FindingOut]
    blocks: list[TextBlock]
    counts: FindingsCounts


# -- Layout map -------------------------------------------


class LayoutPage(_Response):
    index: int
    #: Size in the display space: the page image at scale 1 – PDF points after page
    #: rotation, pixels for images after ``exif_transpose``.
    width: float
    height: float
    unit: Literal["pt", "px"]
    #: ``ocr``: The words originate from text recognition (scan, image).
    source: Literal["text", "ocr"]
    #: Scales at which ``GET /jobs/{id}/pages/{n}`` returns the page.
    scales: list[float]


class LayoutHidden(_Response):
    page: int
    #: UTF-16-Offsets im Quelltext.
    start: int
    end: int
    reason: Literal["white", "tiny", "invisible", "outside"]


#: ``[Seite, x0, y0, x1, y1, start, end, Zeile]`` – box in display space, UTF-16
#: offsets, running line number; compact because there are thousands.
LayoutWord = tuple[int, float, float, float, float, int, int, int]


class LayoutUnit(_Response):
    """A text unit (paragraph) in the flow of a Word document."""

    #: Bookmark ID at the start of the unit in the preview copy.
    anchor: str
    #: UTF-16 range of the unit in the source text.
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class LayoutBorders(_Response):
    #: ``[LineStyle, #RRGGBB]`` per page, line style like in Excel (``thin`` …).
    top: tuple[str, str] | None = None
    right: tuple[str, str] | None = None
    bottom: tuple[str, str] | None = None
    left: tuple[str, str] | None = None


class LayoutCellStyle(_Response):
    """A cell style of the table model; missing fields mean default values."""

    bold: bool = False
    italic: bool = False
    underline: bool = False
    #: Font size in pt; defaults to 11 pt if omitted.
    size: float | None = None
    #: Font family from the file, containing only letters, digits, spaces, and hyphens.
    font: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-F]{6}$")
    #: Nur einfarbige Fuellungen.
    fill: str | None = Field(default=None, pattern=r"^#[0-9A-F]{6}$")
    align: Literal["left", "center", "right", "justify"] = "left"
    valign: Literal["top", "middle", "bottom"] = "bottom"
    wrap: bool = False
    border: LayoutBorders | None = None


class LayoutSheet(_Response):
    """A sheet in the grid: dimensions and features; cells are handled separately."""

    index: int = Field(ge=0)
    name: str
    #: UTF-16 range of the sheet name in the source text; sheet names are also replaced.
    name_range: tuple[int, int] | None = None
    #: Hidden sheet: The file contains it, Excel does not show it.
    hidden: bool = False
    #: The sheet has conditional formatting that the view does not show.
    conditional: bool = False
    rows: int = Field(ge=0)
    cols: int = Field(ge=0)
    #: Fixed rows and columns (``[Zeilen, Spalten]``).
    frozen: tuple[int, int] = (0, 0)
    #: Column widths in px, per column.
    col_widths: list[int] = Field(default_factory=list)
    default_row_height: int = 20
    #: Non-default row heights in px, per row (zero-based).
    row_heights: dict[int, int] = Field(default_factory=dict)
    hidden_rows: list[int] = Field(default_factory=list)
    hidden_cols: list[int] = Field(default_factory=list)
    #: Connected cells ``[line0, column0, line1, column1]``, inclusive.
    merges: list[tuple[int, int, int, int]] = Field(default_factory=list)
    #: Path of the cells in this sheet (``?rows=a-b``).
    cells: str


#: ``[Zeile, Spalte, Anzeige, Stil, start, end]`` – zero-based, style as index in
#: ``styles``, UTF-16 range in source text (−1 without source text).
LayoutCell = tuple[int, int, str, int, int, int]


class LayoutSheetRowsResponse(_Response):
    """Cells of a sheet for a row range (``GET /jobs/{id}/layout/sheets/{i}``)."""

    job_id: str
    sheet: int
    #: Rows ``first..last``, inclusive.
    first: int
    last: int
    cells: list[LayoutCell] = Field(default_factory=list)


class LayoutResponse(_Response):
    job_id: str
    #: ``pages``: fixed pages (PDF, scan, image). ``flow``: Word, rendered in the
    #: WebView from the preview copy. ``grid``: Excel and CSV as a grid from the
    #: spreadsheet model. ``text``: no layout map; the view shows the linear text.
    kind: Literal["pages", "flow", "grid", "text"]
    offset_encoding: Literal["utf-16"] = "utf-16"
    pages: list[LayoutPage] = Field(default_factory=list)
    #: Words in reading order (order in source code).
    words: list[LayoutWord] = Field(default_factory=list)
    #: Text that no one sees in the original but that is present in the file.
    hidden: list[LayoutHidden] = Field(default_factory=list)
    #: ``flow``: Word; ``grid``: Excel or CSV.
    format: Literal["docx", "xlsx", "csv"] | None = None
    #: Only for ``flow``: path of the preview copy with anchors.
    document: str | None = None
    #: Only for ``flow``: text units in source code order.
    units: list[LayoutUnit] = Field(default_factory=list)
    #: Only for ``grid``: sheets in file order.
    sheets: list[LayoutSheet] = Field(default_factory=list)
    #: Only for ``grid``: deduplicated cell styles; index 0 is the default.
    styles: list[LayoutCellStyle] = Field(default_factory=list)
    #: Only for ``flow``: an Office engine is present – the view can show page-faithful
    #: output (``GET /jobs/{id}/layout/paged``).
    paged: bool = False


class LayoutPagedEngine(_Response):
    """Office engine that generated the pages."""

    name: str
    version: str


class LayoutPagedResponse(_Response):
    """Page-faithful Word view: pages from the PDF of the Office engine.

    ``original`` is the cleaned copy, ``result`` is the DOCX that would
    write the save for the apply state ``revision``. The words
    carry ranges in the **source text** – in the result, replacement text lies
    over the range of its position.
    """

    job_id: str
    side: Literal["original", "result"]
    #: Apply state from which the pages originate; 0 in the original.
    revision: int = Field(ge=0)
    engine: LayoutPagedEngine
    offset_encoding: Literal["utf-16"] = "utf-16"
    pages: list[LayoutPage] = Field(default_factory=list)
    #: Words in source text order; headers and footers only on the first page.
    words: list[LayoutWord] = Field(default_factory=list)
    #: UTF-16 ranges of text units that could not be found in the PDF.
    unplaced: list[tuple[int, int]] = Field(default_factory=list)


# -- Apply -------------------------------------------------------------------


class ReviewDecisionIn(_Request):
    finding_id: str = Field(min_length=1, max_length=64)
    action: Literal["accept", "reject"]
    by: Literal["user", "ai-suggestion"] = "user"


class ManualFindingIn(_Request):
    entity_type: str = Field(min_length=1, max_length=64)
    #: UTF-16 offsets in the file's source text.
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    corrects_finding_id: str | None = Field(default=None, max_length=64)


class ReviewIn(_Request):
    decisions: list[ReviewDecisionIn] = Field(default_factory=list)
    manual: list[ManualFindingIn] = Field(default_factory=list)
    #: ``mask``: default – mask when uncertain. ``keep``: show open questions in clear text for
    #: preview; saving always masks.
    unresolved: Literal["mask", "keep"] = "mask"


class ApplyRequest(_Request):
    masking: MaskingSpec
    review: ReviewIn = Field(default_factory=ReviewIn)


class AppliedFindingOut(_Response):
    finding_id: str
    kind: Literal["confirmed", "suspicious"]
    entity_type: str
    category: str | None
    source: str
    mode: str
    reversibility: Literal["none", "vault", "key"]
    original_start: int
    original_end: int
    output_start: int
    output_end: int


class ResidualRiskOut(_Response):
    status: Literal["clear", "warning", "not_run", "failed"]
    heuristic: bool
    threshold: int
    classes: list[str]
    counts: dict[str, int]


class LabelFacts(_Response):
    label: ResultLabel
    contains_surrogates: bool
    #: Why not "Anonymous" – empty exactly when ``label = anonymous``.
    anonymous_blockers: list[AnonymousBlocker]
    #: Derived level, only for audit and review report.
    protection_level: int
    #: Count of entries kept here and also kept overall.
    kept_here_count: int
    #: Open questions that were replaced by “mask if in doubt”.
    masked_without_decision: int


class ApplyResult(_Response):
    offset_encoding: Literal["utf-16"] = "utf-16"
    text: str
    masking: MaskingSpec
    labels: LabelFacts
    applied_findings: list[AppliedFindingOut]
    applied_by_category: dict[str, int]
    residual_risk: ResidualRiskOut
    #: Complete result facts of processing (spec §7.3).
    facts: dict[str, Any]
    #: Content-free review report for this state.
    report: dict[str, Any]


class PlacementText(_Response):
    """Replacement text as the export sets it – anchors in the display space."""

    value: str
    #: Start of baseline.
    x: float
    baseline: float
    size: float
    font: Literal["sans", "serif", "mono"]
    bold: bool
    color: str
    #: Width to which the view draws the text (``textLength``).
    width: float
    #: Rotation of the text in the display space (page rotation), clockwise.
    rotate: int


#: ``[x0, y0, x1, y1]`` in display space.
PlacementRect = tuple[float, float, float, float]


class PlacementOut(_Response):
    """How the export draws a placement."""

    finding_id: str
    #: UTF-16 range in the source text, like ``original_start``/``original_end``.
    start: int
    end: int
    page: int
    #: Filled areas, one per line.
    cover: list[PlacementRect]
    fill: Literal["white", "black"]
    #: ``None`` for blackouts and removals – or if there was no space.
    text: PlacementText | None


class ApplyResponse(ApplyResult):
    job_id: str
    entry_id: str | None
    #: Only fixed pages (PDF, scan, image): Geometry of the overpainting, from the same
    #: function that the export draws with.
    placements: list[PlacementOut] = Field(default_factory=list)


# -- Save -----------------------------------------------------


class SaveFileIn(_Request):
    file_id: str = Field(min_length=1, max_length=128)
    review: ReviewIn = Field(default_factory=ReviewIn)


class SaveEntryRequest(_Request):
    """“Save all n files”: the final state shown by the UI.

    Open questions are always masked regardless of ``review.unresolved``.
    If a file is missing in ``files``, its last recorded review state applies.
    """

    masking: MaskingSpec
    files: list[SaveFileIn] = Field(default_factory=list, max_length=200)
    #: Confirm irreversible saving once per entry.
    confirm_irreversible: bool = False


class StagedFile(_Response):
    file_id: str
    #: Filename in the target folder, e.g. ``Rechnung_geschützt.pdf``.
    name: str
    #: Path in the private deployment folder; the harness checks and copies.
    path: str
    sha256: str
    size_bytes: int


class SaveEntryResponse(_Response):
    entry: Entry
    staging_directory: str
    staged: list[StagedFile]
    #: Files without masked spots do not get a copy.
    unchanged: list[str]
    #: Various abbreviations or pseudonyms currently in the table.
    committed_mappings: int


class PublishedFile(_Request):
    file_id: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=4096)


class OutputsRequest(_Request):
    """The shell reports where it has published. Only now does “saved” apply."""

    directory: str = Field(min_length=1, max_length=4096)
    files: list[PublishedFile] = Field(max_length=200)


class ReportFile(_Response):
    id: str
    name: str
    format: str
    pages: int | None
    ocr_used: bool
    source_changed: bool
    output: FileOutput | None


class EntryReportResponse(_Response):
    """Report of a saved entry: counters and review data, never the content.

    The wording matches the UI surface (``export/pruefbericht.ts``); only factual data is
    provided here.
    """

    entry_id: str
    #: The save from the export history; ``None`` for legacy data.
    export_id: str | None = None
    title: str
    created_at: datetime
    saved_at: datetime | None
    masking: MaskingSpec
    protection_level: int
    app_version: str
    profile_version: str
    files: list[ReportFile]
    totals: FileOutput


class ReplaceFileResponse(_Response):
    entry: Entry
    file: EntryFile
    #: The newly read original differs from the first (hash).
    changed: bool


# -- Text Mode --------------------------------------------------------------


class TextMaskRequest(_Request):
    text: str = Field(min_length=1)
    masking: MaskingSpec
    review: ReviewIn = Field(default_factory=ReviewIn)


class TextMaskResponse(_Response):
    findings: list[FindingOut]
    counts: FindingsCounts
    result: ApplyResult


class TextCommitRequest(TextMaskRequest):
    title: str | None = Field(default=None, min_length=1, max_length=200)


class TextCommitResponse(_Response):
    entry: Entry
    result: ApplyResult
    #: Various abbreviations or pseudonyms currently in the table.
    committed_mappings: int


# -- Restore ------------------------------------------------------


class RestoreTextRequest(_Request):
    text: str = Field(min_length=1)
    #: The entry to restore from – for "from here".
    entry_id: str | None = Field(default=None, min_length=1, max_length=128)
    #: Also restore invented names (style "Invented Names").
    surrogates: bool = False
    #: "Copy" or "Save as file": the entry counts a restore, the table records code usage.
    record: bool = False


class RestoredSpan(_Response):
    #: UTF-16 offsets in the output.
    start: int
    end: int
    #: ``unknown``: looks like a code but is not in the table.
    status: Literal["restored", "unknown"]
    kind: Literal["code", "surrogate"]
    entity_type: str
    #: The spelling in the answer differed from the code (brackets, spelling, spaces)
    #: but was still unambiguous.
    tolerant: bool
    #: ``here``: from this entry · ``entry``: from a different entry in the history ·
    #: ``earlier``: from an entry that no longer exists.
    origin: Literal["here", "entry", "earlier"] | None
    entry_id: str | None


class RestoreCounts(_Response):
    restored: int
    here: int
    other: int
    earlier: int
    unknown: int
    unknown_unique: int
    tolerant: int


class RestoreOrigin(_Response):
    entry_id: str
    #: Entry title – it never contains a name.
    title: str
    count: int


class RestoreTextResponse(_Response):
    offset_encoding: Literal["utf-16"] = "utf-16"
    text: str
    spans: list[RestoredSpan]
    counts: RestoreCounts
    #: Other entries providing codes, most frequent first.
    origins: list[RestoreOrigin]
    recorded: bool


class RestoreSource(_Response):
    name: str
    format: Literal["docx", "xlsx", "md", "txt", "csv"]
    size_bytes: int


class RestoredFileOut(_Response):
    #: For retrieval by the harness (``GET /restore/file/{id}``).
    id: str
    #: ``<name>_mit_Namen.<ext>`` – the AI's file remains unchanged.
    name: str
    format: Literal["docx", "xlsx", "md", "txt", "csv"]
    sha256: str
    size_bytes: int


class RestorePreview(_Response):
    offset_encoding: Literal["utf-16"] = "utf-16"
    text: str
    spans: list[RestoredSpan]
    #: The preview shows only the beginning; the file contains everything.
    truncated: bool


class RestoreFileResponse(_Response):
    source: RestoreSource
    #: Missing if no code was replaced – then no file is created.
    output: RestoredFileOut | None
    preview: RestorePreview
    counts: RestoreCounts
    origins: list[RestoreOrigin]
    #: Known code at locations that cannot be reliably replaced (fields, change
    #: tracking, sheet names …).
    unreplaced: int


# -- Settings -----------------------------------------------------------


class SettingsResponse(AppSettings):
    pass


# -- Translation table -------------------------------------------------


class VaultStatusResponse(_Response):
    state: Literal["ready", "unavailable"]
    key_fingerprint: str | None
    warning: str
    #: When the key was created (e.g., “Key from August 2, 2026”).
    created_at: str | None = None
    mapping_count: int
    #: How many history entries the mappings originate from.
    entry_count: int = 0
    mapping_persisted: bool = False


class VaultConfirmRequest(_Request):
    confirm: bool = False


class VaultBackupRequest(_Request):
    password: str = Field(min_length=1, max_length=1024)


class VaultImportRequest(_Request):
    password: str = Field(min_length=1, max_length=1024)
    #: The backup, Base64-encoded; the harness read it from the selected file.
    backup: str = Field(min_length=1)
    #: The key and table for this device will be replaced.
    confirm: bool = False


class NameListOut(_Response):
    id: str
    kind: ListKind
    source_name: str
    count: int
    imported_at: datetime
    #: Source state: file modification time, otherwise the import time.
    updated_at: datetime
    #: Older than a week – “please update”.
    stale: bool


class ConfiguredNames(_Response):
    """Master data from configuration (nightly export) – display only."""

    count: int
    updated_at: datetime | None


class ListsResponse(_Response):
    lists: list[NameListOut]
    configured: ConfiguredNames | None
    #: After ``POST /lists``: the new list – the harness remembers the source file for
    #: “Update now”.
    created: str | None = None


class TermsRequest(_Request):
    terms: list[TermIn] = Field(default_factory=list)


class TermsResponse(_Response):
    terms: list[Term]


class PatternsRequest(_Request):
    patterns: list[PatternIn] = Field(default_factory=list)


class PatternsResponse(_Response):
    patterns: list[Pattern]


class PatternTestRequest(_Request):
    text: str = Field(min_length=1, max_length=4000)
    #: Check these patterns instead of the saved ones (a pattern just being created);
    #: terms and lists always apply.
    patterns: list[PatternIn] | None = None


class PatternTestHit(_Response):
    #: UTF-16-Offsets im Testtext.
    start: int
    end: int
    entity_type: TermType
    source: Literal["pattern", "term", "list"]
    #: Placeholder in the app language, e.g. ``[CustomerNumber]``.
    label: str


class PatternTestResponse(_Response):
    offset_encoding: Literal["utf-16"] = "utf-16"
    hits: list[PatternTestHit]
    count: int


class TemplateOut(_Response):
    id: str
    title: str
    #: Example template from existing profiles – not overwritable.
    example: bool
    terms: int
    patterns: int
    masking: MaskingDefaults | None


class TemplatesResponse(_Response):
    templates: list[TemplateOut]
    #: Selected template; ``None`` means „Ohne Vorlage“.
    active: str | None


class SaveTemplateRequest(_Request):
    title: str = Field(min_length=1, max_length=120)


class TemplateExportRequest(_Request):
    #: Without ID: the current state (terms, patterns, masking defaults).
    id: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=120)


class TemplateExportResponse(_Response):
    #: Suggested filename, e.g. ``Versicherung.unname-vorlage.yaml``.
    name: str
    content: str


class TemplateImportRequest(_Request):
    content: str = Field(min_length=1)


class TemplateApplyResponse(_Response):
    templates: TemplatesResponse
    terms: list[Term]
    patterns: list[Pattern]
    settings: SettingsResponse


# -- Models & storage (loaded via the harness) ------------------


class ModelComponent(_Response):
    #: ``ner`` name detection · ``de``/``en`` language packs · ``ocr`` text recognition
    #: language data · ``classifier`` second opinion – see
    #: ``app/src-tauri/models.lock.json``.
    id: Literal["ner", "de", "en", "ocr", "classifier"]
    state: Literal["ready", "missing"]
    #: ``download``: loaded and checked by the harness · ``bundle``: in the signed
    #: package · ``local``: development environment · ``system``: part of the operating
    #: system (text recognition with Apple Vision).
    source: Literal["download", "bundle", "local", "system"]
    size_bytes: int | None
    #: Languages covered by the component (text recognition: deu, eng or with Apple
    #: Vision de-DE, en-US).
    languages: list[str]


class ModelsResponse(_Response):
    components: list[ModelComponent]
    total_bytes: int
    #: Name detection or any language pack is missing – the initial setup is pending.
    setup_required: bool


#: All models that form the schema for ``api.gen.ts``.
SCHEMA_MODELS: tuple[type[BaseModel], ...] = (
    CatalogResponse,
    CreateEntryRequest,
    PatchEntryRequest,
    EntryListResponse,
    Entry,
    SubmitFilesResponse,
    EntryView,
    RetryFileResponse,
    RedetectStatusResponse,
    RedetectRequest,
    RedetectResponse,
    StorageResponse,
    ProgressResponse,
    OccurrencesRequest,
    OccurrencesResponse,
    FindingsResponse,
    LayoutPagedResponse,
    LayoutResponse,
    LayoutSheetRowsResponse,
    ApplyRequest,
    ApplyResponse,
    TextMaskRequest,
    TextMaskResponse,
    TextCommitRequest,
    TextCommitResponse,
    SettingsResponse,
    MaskingSpec,
    SaveEntryRequest,
    SaveEntryResponse,
    OutputsRequest,
    EntryReportResponse,
    ReplaceFileResponse,
    RestoreTextRequest,
    RestoreTextResponse,
    RestoreFileResponse,
    VaultStatusResponse,
    VaultConfirmRequest,
    VaultBackupRequest,
    VaultImportRequest,
    ListsResponse,
    TermsRequest,
    TermsResponse,
    PatternsRequest,
    PatternsResponse,
    PatternTestRequest,
    PatternTestResponse,
    TemplatesResponse,
    SaveTemplateRequest,
    TemplateExportRequest,
    TemplateExportResponse,
    TemplateImportRequest,
    TemplateApplyResponse,
    ModelsResponse,
)


__all__ = [name for name in dir() if not name.startswith("_")]
