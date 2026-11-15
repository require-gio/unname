/**
 * Typen der Sidecar-API v2 – ERZEUGT, nicht von Hand bearbeiten.
 *
 * Quelle: sidecar/src/presidio_sidecar/api_models.py
 * Neu erzeugen: node scripts/api-types.mjs
 */

/**
 * Typen der Sidecar-API v2 – erzeugt, nicht von Hand bearbeiten.
 */
export interface UnnameApiV2 {
	CatalogResponse: CatalogResponse;
	CreateEntryRequest: CreateEntryRequest;
	PatchEntryRequest: PatchEntryRequest;
	EntryListResponse: EntryListResponse;
	Entry: Entry;
	SubmitFilesResponse: SubmitFilesResponse;
	EntryView: EntryView;
	RetryFileResponse: RetryFileResponse;
	RedetectStatusResponse: RedetectStatusResponse;
	RedetectRequest: RedetectRequest;
	RedetectResponse: RedetectResponse;
	StorageResponse: StorageResponse;
	ProgressResponse: ProgressResponse;
	OccurrencesRequest: OccurrencesRequest;
	OccurrencesResponse: OccurrencesResponse;
	FindingsResponse: FindingsResponse;
	LayoutPagedResponse: LayoutPagedResponse;
	LayoutResponse: LayoutResponse;
	LayoutSheetRowsResponse: LayoutSheetRowsResponse;
	ApplyRequest: ApplyRequest;
	ApplyResponse: ApplyResponse;
	TextMaskRequest: TextMaskRequest;
	TextMaskResponse: TextMaskResponse;
	TextCommitRequest: TextCommitRequest;
	TextCommitResponse: TextCommitResponse;
	SettingsResponse: SettingsResponse;
	MaskingSpec: MaskingSpec;
	SaveEntryRequest: SaveEntryRequest;
	SaveEntryResponse: SaveEntryResponse;
	OutputsRequest: OutputsRequest;
	EntryReportResponse: EntryReportResponse;
	ReplaceFileResponse: ReplaceFileResponse;
	RestoreTextRequest: RestoreTextRequest;
	RestoreTextResponse: RestoreTextResponse;
	RestoreFileResponse: RestoreFileResponse;
	VaultStatusResponse: VaultStatusResponse;
	VaultConfirmRequest: VaultConfirmRequest;
	VaultBackupRequest: VaultBackupRequest;
	VaultImportRequest: VaultImportRequest;
	ListsResponse: ListsResponse;
	TermsRequest: TermsRequest;
	TermsResponse: TermsResponse;
	PatternsRequest: PatternsRequest;
	PatternsResponse: PatternsResponse;
	PatternTestRequest: PatternTestRequest;
	PatternTestResponse: PatternTestResponse;
	TemplatesResponse: TemplatesResponse;
	SaveTemplateRequest: SaveTemplateRequest;
	TemplateExportRequest: TemplateExportRequest;
	TemplateExportResponse: TemplateExportResponse;
	TemplateImportRequest: TemplateImportRequest;
	TemplateApplyResponse: TemplateApplyResponse;
	ModelsResponse: ModelsResponse;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "CatalogResponse".
 */
export interface CatalogResponse {
	categories: CategoryOut[];
	styles: {
		[k: string]: string[];
	};
	style_modes: {
		[k: string]: string;
	};
	placeholders: {
		[k: string]: {
			[k: string]: string;
		};
	};
	defaults: CatalogDefaults;
	languages: string[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "CategoryOut".
 */
export interface CategoryOut {
	id: string;
	labels: {
		[k: string]: string;
	};
	entity_types: string[];
	level: number;
	default: boolean;
	locked: boolean;
	circumstances: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "CatalogDefaults".
 */
export interface CatalogDefaults {
	method: 'reversible' | 'irreversible';
	style: 'codes' | 'surrogates' | 'placeholder' | 'mask' | 'remove';
	categories: string[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "CreateEntryRequest".
 */
export interface CreateEntryRequest {
	kind?: 'documents' | 'text';
	masking?: MaskingSpec | null;
	title?: string | null;
}
/**
 * The masking spec – identical in frontend and sidecar.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "MaskingSpec".
 */
export interface MaskingSpec {
	method: 'reversible' | 'irreversible';
	style: 'codes' | 'surrogates' | 'placeholder' | 'mask' | 'remove';
	/**
	 * Kategorie-IDs aus GET /catalog; gesperrte sind immer dabei.
	 */
	categories: string[];
	/**
	 * Abgeleitet: irreversibel und alle Kategorien. Wird geprueft, nie geraten.
	 */
	anonymize?: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "PatchEntryRequest".
 */
export interface PatchEntryRequest {
	title?: string | null;
	masking?: MaskingSpec | null;
	status?: ('processing' | 'review' | 'saved' | 'copied' | 'failed') | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "EntryListResponse".
 */
export interface EntryListResponse {
	entries: Entry[];
	count: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "Entry".
 */
export interface Entry {
	id: string;
	display_ref?: string | null;
	title: string;
	title_custom?: boolean;
	kind: 'documents' | 'text';
	status?: 'processing' | 'review' | 'saved' | 'copied' | 'failed';
	created_at: string;
	updated_at: string;
	masking: MaskingSpec;
	files?: EntryFile[];
	review?: {
		[k: string]: FileReview;
	};
	saved_at?: string | null;
	output_dir?: string | null;
	irreversible_confirmed?: boolean;
	irreversible_confirmed_for?: string | null;
	restores?: number;
	restored_at?: string | null;
	draft_revision?: number;
	saved_revision?: number | null;
	/**
	 * @maxItems 25
	 */
	exports?: EntryExport[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "EntryFile".
 */
export interface EntryFile {
	id: string;
	name: string;
	format: string;
	size_bytes: number;
	pages?: number | null;
	ocr_used?: boolean;
	status?: 'queued' | 'processing' | 'review' | 'failed' | 'cancelled' | 'expired' | 'unavailable';
	error_code?: string | null;
	counts?: {
		[k: string]: number;
	};
	label?: ('reversible' | 'irreversible' | 'anonymous') | null;
	output_path?: string | null;
	source_sha256?: string | null;
	source_changed?: boolean;
	extraction_changed?: boolean;
	output?: FileOutput | null;
}
/**
 * Data stored when saving a file – counters and check info only.
 *
 * Basis for the “Saved” view and the check report (spec §5). No value,
 * replacement, or text excerpt.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "FileOutput".
 */
export interface FileOutput {
	status: 'saved' | 'unchanged';
	name?: string | null;
	format?: string | null;
	sha256?: string | null;
	size_bytes?: number;
	from_scan?: boolean;
	readback_failed?: boolean;
	scan_text_layer?: boolean;
	label?: ('reversible' | 'irreversible' | 'anonymous') | null;
	contains_surrogates?: boolean;
	protection_level?: number;
	masked?: number;
	masked_by_category?: {
		[k: string]: number;
	};
	masked_by_entity?: {
		[k: string]: number;
	};
	decided_by_user?: number;
	decided_by_ai?: number;
	masked_without_decision?: number;
	kept_here?: number;
	added_by_hand?: number;
	adjusted?: number;
	residual_risk?: 'clear' | 'warning' | 'not_run' | 'failed';
	residual_classes?: string[];
	anonymous_blockers?: string[];
	rendered_pages?: number;
	text_extraction_checked?: boolean;
	verified_targets?: number;
	audit_id?: number | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "FileReview".
 */
export interface FileReview {
	decisions?: DecisionState[];
	manual?: ManualState[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "DecisionState".
 */
export interface DecisionState {
	finding_id: string;
	action: 'accept' | 'reject';
	by?: 'user' | 'ai-suggestion';
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ManualState".
 */
export interface ManualState {
	entity_type: string;
	start: number;
	end: number;
	corrects_finding_id?: string | null;
}
/**
 * A storage in the export history.
 *
 * Time, masking (method, style, categories), target folder, filenames,
 * labels, and review data – never a value. The last storage is additionally
 * stored as before in ``Entry.saved_at``, ``output_dir`` and
 * ``EntryFile.output``.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "EntryExport".
 */
export interface EntryExport {
	id: string;
	saved_at: string;
	masking: MaskingSpec;
	directory: string;
	revision?: number;
	files?: ExportedFile[];
}
/**
 * A file from a storage in the export history – like ``FileOutput``, but content-free.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ExportedFile".
 */
export interface ExportedFile {
	file_id: string;
	name: string;
	format: string;
	path?: string | null;
	output: FileOutput;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "SubmitFilesResponse".
 */
export interface SubmitFilesResponse {
	entry: Entry;
	accepted: EntryFile[];
	rejected: RejectedFile[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RejectedFile".
 */
export interface RejectedFile {
	name: string;
	code: string;
	message: string;
}
/**
 * View state per entry; offsets count UTF-16 code units (spec §2).
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "EntryView".
 */
export interface EntryView {
	active_file_id?: string | null;
	representation?: 'page' | 'faithful' | 'flow' | 'grid' | 'text';
	page?: number;
	anchor?: number | null;
	focused_question_id?: string | null;
	preview?: 'result' | 'original' | 'side_by_side';
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RetryFileResponse".
 */
export interface RetryFileResponse {
	entry: Entry;
	file: EntryFile;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RedetectStatusResponse".
 */
export interface RedetectStatusResponse {
	entry_id: string;
	files: RedetectFile[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RedetectFile".
 */
export interface RedetectFile {
	file_id: string;
	changes: string[];
	reextract: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RedetectRequest".
 */
export interface RedetectRequest {
	file_ids?: string[] | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RedetectResponse".
 */
export interface RedetectResponse {
	entry: Entry;
	files: RedetectFile[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "StorageResponse".
 */
export interface StorageResponse {
	total_bytes: number;
	free_bytes: number | null;
	low_space_bytes: number;
	retention_days: number | null;
	entries: StorageEntry[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "StorageEntry".
 */
export interface StorageEntry {
	entry_id: string;
	bytes: number;
	delete_at: string | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ProgressResponse".
 */
export interface ProgressResponse {
	entry_id: string;
	status: 'processing' | 'review' | 'saved' | 'copied' | 'failed';
	files: FileProgress[];
	totals: {
		[k: string]: number;
	};
	done: number;
	total: number;
	ready: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "FileProgress".
 */
export interface FileProgress {
	id: string;
	name: string;
	status: 'queued' | 'processing' | 'review' | 'failed' | 'cancelled' | 'expired' | 'unavailable';
	phase: string | null;
	progress: number;
	counts: {
		[k: string]: number;
	};
	questions: number | null;
	error_code: string | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "OccurrencesRequest".
 */
export interface OccurrencesRequest {
	text: string;
	ignore_case?: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "OccurrencesResponse".
 */
export interface OccurrencesResponse {
	entry_id: string;
	offset_encoding?: 'utf-16';
	matches: Occurrence[];
	count: number;
	file_count: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "Occurrence".
 */
export interface Occurrence {
	file_id: string;
	start: number;
	end: number;
	covered: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "FindingsResponse".
 */
export interface FindingsResponse {
	job_id: string;
	offset_encoding?: 'utf-16';
	text: string;
	confirmed: FindingOut[];
	suspicious: FindingOut[];
	findings: FindingOut[];
	blocks: TextBlock[];
	counts: FindingsCounts;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "FindingOut".
 */
export interface FindingOut {
	id: string;
	kind: 'confirmed' | 'suspicious';
	type: string;
	category: string | null;
	score: number;
	start: number;
	end: number;
	text: string;
	context: FindingContext;
	source: string;
	detector: string | null;
	review_required: boolean;
	auto_accepted: boolean;
	question: FindingQuestion | null;
	assessment?: FindingAssessment | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "FindingContext".
 */
export interface FindingContext {
	before: string;
	match: string;
	after: string;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "FindingQuestion".
 */
export interface FindingQuestion {
	kind: 'suspicion' | 'uncertain';
	n: number;
}
/**
 * Assessment by the local AI regarding a question (second opinion).
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "FindingAssessment".
 */
export interface FindingAssessment {
	label: 'person' | 'organization_or_product' | 'location' | 'term';
	confidence: number;
	protect: number;
	suggestion: ('accept' | 'reject') | null;
	decided: boolean;
	model: string;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TextBlock".
 */
export interface TextBlock {
	index: number;
	start: number;
	end: number;
	kind?: 'paragraph';
	page: number | null;
	language?: ('de' | 'en') | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "FindingsCounts".
 */
export interface FindingsCounts {
	confirmed: number;
	suspicious: number;
	total: number;
	by_entity: {
		[k: string]: number;
	};
	by_category: {
		[k: string]: number;
	};
	questions: number;
}
/**
 * Page-faithful Word view: pages from the PDF of the Office engine.
 *
 * ``original`` is the cleaned copy, ``result`` is the DOCX that would
 * write the save for the apply state ``revision``. The words
 * carry ranges in the **source text** – in the result, replacement text lies
 * over the range of its position.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "LayoutPagedResponse".
 */
export interface LayoutPagedResponse {
	job_id: string;
	side: 'original' | 'result';
	revision: number;
	engine: LayoutPagedEngine;
	offset_encoding?: 'utf-16';
	pages?: LayoutPage[];
	words?: [number, number, number, number, number, number, number, number][];
	unplaced?: [number, number][];
}
/**
 * Office engine that generated the pages.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "LayoutPagedEngine".
 */
export interface LayoutPagedEngine {
	name: string;
	version: string;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "LayoutPage".
 */
export interface LayoutPage {
	index: number;
	width: number;
	height: number;
	unit: 'pt' | 'px';
	source: 'text' | 'ocr';
	scales: number[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "LayoutResponse".
 */
export interface LayoutResponse {
	job_id: string;
	kind: 'pages' | 'flow' | 'grid' | 'text';
	offset_encoding?: 'utf-16';
	pages?: LayoutPage[];
	words?: [number, number, number, number, number, number, number, number][];
	hidden?: LayoutHidden[];
	format?: ('docx' | 'xlsx' | 'csv') | null;
	document?: string | null;
	units?: LayoutUnit[];
	sheets?: LayoutSheet[];
	styles?: LayoutCellStyle[];
	paged?: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "LayoutHidden".
 */
export interface LayoutHidden {
	page: number;
	start: number;
	end: number;
	reason: 'white' | 'tiny' | 'invisible' | 'outside';
}
/**
 * A text unit (paragraph) in the flow of a Word document.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "LayoutUnit".
 */
export interface LayoutUnit {
	anchor: string;
	start: number;
	end: number;
}
/**
 * A sheet in the grid: dimensions and features; cells are handled separately.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "LayoutSheet".
 */
export interface LayoutSheet {
	index: number;
	name: string;
	name_range?: [number, number] | null;
	hidden?: boolean;
	conditional?: boolean;
	rows: number;
	cols: number;
	/**
	 * @minItems 2
	 * @maxItems 2
	 */
	frozen?: [number, number];
	col_widths?: number[];
	default_row_height?: number;
	row_heights?: {
		[k: string]: number;
	};
	hidden_rows?: number[];
	hidden_cols?: number[];
	merges?: [number, number, number, number][];
	cells: string;
}
/**
 * A cell style of the table model; missing fields mean default values.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "LayoutCellStyle".
 */
export interface LayoutCellStyle {
	bold?: boolean;
	italic?: boolean;
	underline?: boolean;
	size?: number | null;
	font?: string | null;
	color?: string | null;
	fill?: string | null;
	align?: 'left' | 'center' | 'right' | 'justify';
	valign?: 'top' | 'middle' | 'bottom';
	wrap?: boolean;
	border?: LayoutBorders | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "LayoutBorders".
 */
export interface LayoutBorders {
	top?: [string, string] | null;
	right?: [string, string] | null;
	bottom?: [string, string] | null;
	left?: [string, string] | null;
}
/**
 * Cells of a sheet for a row range (``GET /jobs/{id}/layout/sheets/{i}``).
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "LayoutSheetRowsResponse".
 */
export interface LayoutSheetRowsResponse {
	job_id: string;
	sheet: number;
	first: number;
	last: number;
	cells?: [number, number, string, number, number, number][];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ApplyRequest".
 */
export interface ApplyRequest {
	masking: MaskingSpec;
	review?: ReviewIn;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ReviewIn".
 */
export interface ReviewIn {
	decisions?: ReviewDecisionIn[];
	manual?: ManualFindingIn[];
	unresolved?: 'mask' | 'keep';
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ReviewDecisionIn".
 */
export interface ReviewDecisionIn {
	finding_id: string;
	action: 'accept' | 'reject';
	by?: 'user' | 'ai-suggestion';
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ManualFindingIn".
 */
export interface ManualFindingIn {
	entity_type: string;
	start: number;
	end: number;
	corrects_finding_id?: string | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ApplyResponse".
 */
export interface ApplyResponse {
	offset_encoding?: 'utf-16';
	text: string;
	masking: MaskingSpec;
	labels: LabelFacts;
	applied_findings: AppliedFindingOut[];
	applied_by_category: {
		[k: string]: number;
	};
	residual_risk: ResidualRiskOut;
	facts: {
		[k: string]: unknown;
	};
	report: {
		[k: string]: unknown;
	};
	job_id: string;
	entry_id: string | null;
	placements?: PlacementOut[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "LabelFacts".
 */
export interface LabelFacts {
	label: 'reversible' | 'irreversible' | 'anonymous';
	contains_surrogates: boolean;
	anonymous_blockers: (
		'not_requested' | 'modes' | 'kept_here' | 'plaintext' | 'residual_risk' | 'nothing_masked'
	)[];
	protection_level: number;
	kept_here_count: number;
	masked_without_decision: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "AppliedFindingOut".
 */
export interface AppliedFindingOut {
	finding_id: string;
	kind: 'confirmed' | 'suspicious';
	entity_type: string;
	category: string | null;
	source: string;
	mode: string;
	reversibility: 'none' | 'vault' | 'key';
	original_start: number;
	original_end: number;
	output_start: number;
	output_end: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ResidualRiskOut".
 */
export interface ResidualRiskOut {
	status: 'clear' | 'warning' | 'not_run' | 'failed';
	heuristic: boolean;
	threshold: number;
	classes: string[];
	counts: {
		[k: string]: number;
	};
}
/**
 * How the export draws a placement.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "PlacementOut".
 */
export interface PlacementOut {
	finding_id: string;
	start: number;
	end: number;
	page: number;
	cover: [number, number, number, number][];
	fill: 'white' | 'black';
	text: PlacementText | null;
}
/**
 * Replacement text as the export sets it – anchors in the display space.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "PlacementText".
 */
export interface PlacementText {
	value: string;
	x: number;
	baseline: number;
	size: number;
	font: 'sans' | 'serif' | 'mono';
	bold: boolean;
	color: string;
	width: number;
	rotate: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TextMaskRequest".
 */
export interface TextMaskRequest {
	text: string;
	masking: MaskingSpec;
	review?: ReviewIn;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TextMaskResponse".
 */
export interface TextMaskResponse {
	findings: FindingOut[];
	counts: FindingsCounts;
	result: ApplyResult;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ApplyResult".
 */
export interface ApplyResult {
	offset_encoding?: 'utf-16';
	text: string;
	masking: MaskingSpec;
	labels: LabelFacts;
	applied_findings: AppliedFindingOut[];
	applied_by_category: {
		[k: string]: number;
	};
	residual_risk: ResidualRiskOut;
	facts: {
		[k: string]: unknown;
	};
	report: {
		[k: string]: unknown;
	};
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TextCommitRequest".
 */
export interface TextCommitRequest {
	text: string;
	masking: MaskingSpec;
	review?: ReviewIn;
	title?: string | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TextCommitResponse".
 */
export interface TextCommitResponse {
	entry: Entry;
	result: ApplyResult;
	committed_mappings: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "SettingsResponse".
 */
export interface SettingsResponse {
	schema_version?: 3;
	general?: GeneralSettings;
	masking?: MaskingDefaults;
	document_languages?: DocumentLanguageSettings;
	second_opinion?: SecondOpinionSettings;
	names_terms?: NamesTermsSettings;
	history?: HistorySettings;
	vault?: VaultSettings;
	technical?: TechnicalSettings;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "GeneralSettings".
 */
export interface GeneralSettings {
	language?: 'de' | 'en';
	theme?: 'system' | 'light' | 'dark';
}
/**
 * Default masking for new entries.
 *
 * Without ``anonymize``: derived and never stored.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "MaskingDefaults".
 */
export interface MaskingDefaults {
	method?: 'reversible' | 'irreversible';
	style?: 'codes' | 'surrogates' | 'placeholder' | 'mask' | 'remove';
	categories?: string[];
}
/**
 * Document languages and countries.
 *
 * ``languages``: languages to check documents in; the first is the default
 * for sections without a detectable language. ``per_paragraph``:
 * detect language per paragraph instead of once per document.
 * ``countries``: numeric IDs and IDs of countries to recognize –
 * independent of language. ``placeholders_in_document_language``:
 * placeholders and invented names in the paragraph's language instead of the app's.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "DocumentLanguageSettings".
 */
export interface DocumentLanguageSettings {
	/**
	 * @minItems 1
	 */
	languages?: ['de' | 'en', ...('de' | 'en')[]];
	per_paragraph?: boolean;
	/**
	 * @minItems 1
	 */
	countries?: ['DE' | 'AT', ...('DE' | 'AT')[]];
	placeholders_in_document_language?: boolean;
}
/**
 * Second opinion (local classifier).
 *
 * Active only if the model is installed (``/models``: ``classifier``).
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "SecondOpinionSettings".
 */
export interface SecondOpinionSettings {
	enabled?: boolean;
	policy?: 'protect' | 'protect_and_keep' | 'suggest';
	keep_threshold?: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "NamesTermsSettings".
 */
export interface NamesTermsSettings {
	template?: string | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "HistorySettings".
 */
export interface HistorySettings {
	retention_days?: (7 | 30 | 90) | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "VaultSettings".
 */
export interface VaultSettings {
	unused_retention_months?: (6 | 12) | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TechnicalSettings".
 */
export interface TechnicalSettings {
	developer_mode?: boolean;
}
/**
 * “Save all n files”: the final state shown by the UI.
 *
 * Open questions are always masked regardless of ``review.unresolved``.
 * If a file is missing in ``files``, its last recorded review state applies.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "SaveEntryRequest".
 */
export interface SaveEntryRequest {
	masking: MaskingSpec;
	/**
	 * @maxItems 200
	 */
	files?: SaveFileIn[];
	confirm_irreversible?: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "SaveFileIn".
 */
export interface SaveFileIn {
	file_id: string;
	review?: ReviewIn;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "SaveEntryResponse".
 */
export interface SaveEntryResponse {
	entry: Entry;
	staging_directory: string;
	staged: StagedFile[];
	unchanged: string[];
	committed_mappings: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "StagedFile".
 */
export interface StagedFile {
	file_id: string;
	name: string;
	path: string;
	sha256: string;
	size_bytes: number;
}
/**
 * The shell reports where it has published. Only now does “saved” apply.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "OutputsRequest".
 */
export interface OutputsRequest {
	directory: string;
	/**
	 * @maxItems 200
	 */
	files: PublishedFile[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "PublishedFile".
 */
export interface PublishedFile {
	file_id: string;
	path: string;
}
/**
 * Report of a saved entry: counters and review data, never the content.
 *
 * The wording matches the UI surface (``export/pruefbericht.ts``); only factual data is
 * provided here.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "EntryReportResponse".
 */
export interface EntryReportResponse {
	entry_id: string;
	export_id?: string | null;
	title: string;
	created_at: string;
	saved_at: string | null;
	masking: MaskingSpec;
	protection_level: number;
	app_version: string;
	profile_version: string;
	files: ReportFile[];
	totals: FileOutput;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ReportFile".
 */
export interface ReportFile {
	id: string;
	name: string;
	format: string;
	pages: number | null;
	ocr_used: boolean;
	source_changed: boolean;
	output: FileOutput | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ReplaceFileResponse".
 */
export interface ReplaceFileResponse {
	entry: Entry;
	file: EntryFile;
	changed: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RestoreTextRequest".
 */
export interface RestoreTextRequest {
	text: string;
	entry_id?: string | null;
	surrogates?: boolean;
	record?: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RestoreTextResponse".
 */
export interface RestoreTextResponse {
	offset_encoding?: 'utf-16';
	text: string;
	spans: RestoredSpan[];
	counts: RestoreCounts;
	origins: RestoreOrigin[];
	recorded: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RestoredSpan".
 */
export interface RestoredSpan {
	start: number;
	end: number;
	status: 'restored' | 'unknown';
	kind: 'code' | 'surrogate';
	entity_type: string;
	tolerant: boolean;
	origin: ('here' | 'entry' | 'earlier') | null;
	entry_id: string | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RestoreCounts".
 */
export interface RestoreCounts {
	restored: number;
	here: number;
	other: number;
	earlier: number;
	unknown: number;
	unknown_unique: number;
	tolerant: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RestoreOrigin".
 */
export interface RestoreOrigin {
	entry_id: string;
	title: string;
	count: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RestoreFileResponse".
 */
export interface RestoreFileResponse {
	source: RestoreSource;
	output: RestoredFileOut | null;
	preview: RestorePreview;
	counts: RestoreCounts;
	origins: RestoreOrigin[];
	unreplaced: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RestoreSource".
 */
export interface RestoreSource {
	name: string;
	format: 'docx' | 'xlsx' | 'md' | 'txt' | 'csv';
	size_bytes: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RestoredFileOut".
 */
export interface RestoredFileOut {
	id: string;
	name: string;
	format: 'docx' | 'xlsx' | 'md' | 'txt' | 'csv';
	sha256: string;
	size_bytes: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "RestorePreview".
 */
export interface RestorePreview {
	offset_encoding?: 'utf-16';
	text: string;
	spans: RestoredSpan[];
	truncated: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "VaultStatusResponse".
 */
export interface VaultStatusResponse {
	state: 'ready' | 'unavailable';
	key_fingerprint: string | null;
	warning: string;
	created_at?: string | null;
	mapping_count: number;
	entry_count?: number;
	mapping_persisted?: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "VaultConfirmRequest".
 */
export interface VaultConfirmRequest {
	confirm?: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "VaultBackupRequest".
 */
export interface VaultBackupRequest {
	password: string;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "VaultImportRequest".
 */
export interface VaultImportRequest {
	password: string;
	backup: string;
	confirm?: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ListsResponse".
 */
export interface ListsResponse {
	lists: NameListOut[];
	configured: ConfiguredNames | null;
	created?: string | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "NameListOut".
 */
export interface NameListOut {
	id: string;
	kind: 'persons' | 'organizations';
	source_name: string;
	count: number;
	imported_at: string;
	updated_at: string;
	stale: boolean;
}
/**
 * Master data from configuration (nightly export) – display only.
 *
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ConfiguredNames".
 */
export interface ConfiguredNames {
	count: number;
	updated_at: string | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TermsRequest".
 */
export interface TermsRequest {
	terms?: TermIn[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TermIn".
 */
export interface TermIn {
	text: string;
	entity_type: 'PERSON' | 'ORGANIZATION' | 'LOCATION' | 'CUSTOMER_NUMBER' | 'CASE_NUMBER' | 'OTHER';
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TermsResponse".
 */
export interface TermsResponse {
	terms: Term[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "Term".
 */
export interface Term {
	id: string;
	text: string;
	entity_type: 'PERSON' | 'ORGANIZATION' | 'LOCATION' | 'CUSTOMER_NUMBER' | 'CASE_NUMBER' | 'OTHER';
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "PatternsRequest".
 */
export interface PatternsRequest {
	patterns?: PatternIn[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "PatternIn".
 */
export interface PatternIn {
	id?: string | null;
	name: string;
	regex: string;
	entity_type?:
		'PERSON' | 'ORGANIZATION' | 'LOCATION' | 'CUSTOMER_NUMBER' | 'CASE_NUMBER' | 'OTHER';
	enabled?: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "PatternsResponse".
 */
export interface PatternsResponse {
	patterns: Pattern[];
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "Pattern".
 */
export interface Pattern {
	id: string;
	name: string;
	regex: string;
	entity_type?:
		'PERSON' | 'ORGANIZATION' | 'LOCATION' | 'CUSTOMER_NUMBER' | 'CASE_NUMBER' | 'OTHER';
	enabled?: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "PatternTestRequest".
 */
export interface PatternTestRequest {
	text: string;
	patterns?: PatternIn[] | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "PatternTestResponse".
 */
export interface PatternTestResponse {
	offset_encoding?: 'utf-16';
	hits: PatternTestHit[];
	count: number;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "PatternTestHit".
 */
export interface PatternTestHit {
	start: number;
	end: number;
	entity_type: 'PERSON' | 'ORGANIZATION' | 'LOCATION' | 'CUSTOMER_NUMBER' | 'CASE_NUMBER' | 'OTHER';
	source: 'pattern' | 'term' | 'list';
	label: string;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TemplatesResponse".
 */
export interface TemplatesResponse {
	templates: TemplateOut[];
	active: string | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TemplateOut".
 */
export interface TemplateOut {
	id: string;
	title: string;
	example: boolean;
	terms: number;
	patterns: number;
	masking: MaskingDefaults | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "SaveTemplateRequest".
 */
export interface SaveTemplateRequest {
	title: string;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TemplateExportRequest".
 */
export interface TemplateExportRequest {
	id?: string | null;
	title?: string | null;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TemplateExportResponse".
 */
export interface TemplateExportResponse {
	name: string;
	content: string;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TemplateImportRequest".
 */
export interface TemplateImportRequest {
	content: string;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "TemplateApplyResponse".
 */
export interface TemplateApplyResponse {
	templates: TemplatesResponse;
	terms: Term[];
	patterns: Pattern[];
	settings: SettingsResponse;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ModelsResponse".
 */
export interface ModelsResponse {
	components: ModelComponent[];
	total_bytes: number;
	setup_required: boolean;
}
/**
 * This interface was referenced by `UnnameApiV2`'s JSON-Schema
 * via the `definition` "ModelComponent".
 */
export interface ModelComponent {
	id: 'ner' | 'de' | 'en' | 'ocr' | 'classifier';
	state: 'ready' | 'missing';
	source: 'download' | 'bundle' | 'local' | 'system';
	size_bytes: number | null;
	languages: string[];
}
