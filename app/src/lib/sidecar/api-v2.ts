import { request, requestBlob } from './client.js';
import type {
	ApplyRequest,
	ApplyResponse,
	CreateEntryRequest,
	Entry,
	EntryView,
	EntryListResponse,
	EntryReportResponse,
	FindingsResponse,
	LayoutPagedResponse,
	LayoutResponse,
	LayoutSheetRowsResponse,
	ListsResponse,
	ModelsResponse,
	OccurrencesRequest,
	OccurrencesResponse,
	PatchEntryRequest,
	PatternIn,
	PatternsResponse,
	PatternTestResponse,
	ProgressResponse,
	RedetectRequest,
	RedetectResponse,
	RedetectStatusResponse,
	RestoreTextRequest,
	RestoreTextResponse,
	RetryFileResponse,
	SettingsResponse,
	StorageResponse,
	TemplateApplyResponse,
	TemplateExportResponse,
	TemplatesResponse,
	TermIn,
	TermsResponse,
	TextCommitRequest,
	TextCommitResponse,
	TextMaskRequest,
	TextMaskResponse,
	VaultStatusResponse
} from './api.gen.js';
import type { SidecarConnection } from './types.js';

/**
 * API v2 calls using types from `api.gen.ts`.
 *
 * Address and token are provided by the shell as in `client.ts` for each call.
 * Files are supplied by the shell (`eingang.ts`), not the UI.
 */

const JSON_HEADER = { 'Content-Type': 'application/json' };

function json(method: string, body: unknown, signal?: AbortSignal): RequestInit {
	return { method: method, headers: JSON_HEADER, body: JSON.stringify(body), signal };
}

const path = (id: string) => `/entries/${encodeURIComponent(id)}`;
const jobPath = (id: string) => `/jobs/${encodeURIComponent(id)}`;

export function fetchEntries(connection: SidecarConnection): Promise<EntryListResponse> {
	return request<EntryListResponse>(connection, '/entries');
}

export function createEntry(
	connection: SidecarConnection,
	payload: CreateEntryRequest
): Promise<Entry> {
	return request<Entry>(connection, '/entries', json('POST', payload));
}

/** Reads the entry; sidecar extends the TTL of its jobs. */
export function fetchEntry(connection: SidecarConnection, id: string): Promise<Entry> {
	return request<Entry>(connection, path(id));
}

export function fetchEntryView(
	connection: SidecarConnection,
	id: string
): Promise<EntryView | null> {
	return request<EntryView | null>(connection, `${path(id)}/view`);
}

export function updateEntryView(
	connection: SidecarConnection,
	id: string,
	view: EntryView,
	keepalive = false
): Promise<EntryView> {
	return request<EntryView>(connection, `${path(id)}/view`, {
		...json('PUT', view),
		keepalive
	});
}

export function retryEntryFile(
	connection: SidecarConnection,
	id: string,
	fileId: string
): Promise<RetryFileResponse> {
	return request<RetryFileResponse>(
		connection,
		`${path(id)}/files/${encodeURIComponent(fileId)}/retry`,
		{
			method: 'POST'
		}
	);
}

/** Files offered for "Neu erkennen". */
export function fetchRedetectStatus(
	connection: SidecarConnection,
	id: string
): Promise<RedetectStatusResponse> {
	return request<RedetectStatusResponse>(connection, `${path(id)}/redetect`);
}

/** "Neu erkennen" from saved state; without `file_ids` returns all that no longer match. */
export function redetectEntry(
	connection: SidecarConnection,
	id: string,
	payload: RedetectRequest = {}
): Promise<RedetectResponse> {
	return request<RedetectResponse>(connection, `${path(id)}/redetect`, json('POST', payload));
}

/** Occupied storage of the state, total and per entry. */
export function fetchStorage(connection: SidecarConnection): Promise<StorageResponse> {
	return request<StorageResponse>(connection, '/storage');
}

export function removeEntryFile(
	connection: SidecarConnection,
	id: string,
	fileId: string
): Promise<Entry> {
	return request<Entry>(connection, `${path(id)}/files/${encodeURIComponent(fileId)}`, {
		method: 'DELETE'
	});
}

export function deleteEntry(connection: SidecarConnection, id: string): Promise<void> {
	return request<void>(connection, path(id), { method: 'DELETE' });
}

/** Rename, change status, or update masking of an entry. */
export function patchEntry(
	connection: SidecarConnection,
	id: string,
	payload: PatchEntryRequest
): Promise<Entry> {
	return request<Entry>(connection, path(id), json('PATCH', payload));
}

export function fetchEntryProgress(
	connection: SidecarConnection,
	id: string
): Promise<ProgressResponse> {
	return request<ProgressResponse>(connection, `${path(id)}/progress`);
}

/**
 * All occurrences of a highlighted string across the entry's files
 * ("All 4 matches in 3 files"). Whole words only; offsets in UTF-16.
 */
export function findOccurrences(
	connection: SidecarConnection,
	id: string,
	payload: OccurrencesRequest,
	signal?: AbortSignal
): Promise<OccurrencesResponse> {
	return request<OccurrencesResponse>(
		connection,
		`${path(id)}/occurrences`,
		json('POST', payload, signal)
	);
}

/** Source text, matches, questions and paragraphs of a finished file (file ID = job ID). */
export function fetchFindings(
	connection: SidecarConnection,
	jobId: string
): Promise<FindingsResponse> {
	return request<FindingsResponse>(connection, `${jobPath(jobId)}/findings`);
}

/**
 * Where each word appears in the rendered document. `kind: 'text'`
 * means: no fixed pages, the view shows linear text.
 */
export function fetchLayout(connection: SidecarConnection, jobId: string): Promise<LayoutResponse> {
	return request<LayoutResponse>(connection, `${jobPath(jobId)}/layout`);
}

/**
 * Image of a fixed page at scale `stufe` (one of the `scales`).
 * Returned as `ImageBitmap` and drawn onto a canvas.
 */
export async function fetchPageImage(
	connection: SidecarConnection,
	jobId: string,
	page: number,
	level: number,
	signal?: AbortSignal
): Promise<ImageBitmap> {
	const blob = await requestBlob(connection, `${jobPath(jobId)}/pages/${page}?scale=${level}`, {
		signal
	});
	return createImageBitmap(blob);
}

/**
 * Preview copy of a Word file: the DOCX with an anchor per text unit,
 * without links or metadata. Rendered in a WebView; kept in memory only.
 */
export function fetchLayoutDocument(
	connection: SidecarConnection,
	jobId: string,
	signal?: AbortSignal
): Promise<Blob> {
	return requestBlob(connection, `${jobPath(jobId)}/layout/document`, { signal });
}

/**
 * Page-faithful Word view: pages and words from the PDF generated by the Office engine.
 * `original` comes from the cleaned copy, `result` from the DOCX that would be saved for the last apply state.
 * Only provided when the layout indicates a paged view.
 */
export function fetchPageFaithful(
	connection: SidecarConnection,
	jobId: string,
	page: 'original' | 'result',
	signal?: AbortSignal
): Promise<LayoutPagedResponse> {
	return request<LayoutPagedResponse>(connection, `${jobPath(jobId)}/layout/paged?side=${page}`, {
		signal
	});
}

/** Image of the page-faithful view for the exact state named by the layout. */
export async function fetchPageFaithfulImage(
	connection: SidecarConnection,
	jobId: string,
	page: 'original' | 'result',
	revision: number,
	number: number,
	level: number,
	signal?: AbortSignal
): Promise<ImageBitmap> {
	const blob = await requestBlob(
		connection,
		`${jobPath(jobId)}/layout/paged/${number}?side=${page}&revision=${revision}&scale=${level}`,
		{ signal }
	);
	return createImageBitmap(blob);
}

/**
 * Cells of a spreadsheet for rows `von..bis`, formatted in the view's language.
 * Maximum 2000 rows per request.
 */
export function fetchTablesExcerpt(
	connection: SidecarConnection,
	jobId: string,
	sheet: number,
	by: number,
	until: number,
	language: 'de' | 'en',
	signal?: AbortSignal
): Promise<LayoutSheetRowsResponse> {
	return request<LayoutSheetRowsResponse>(
		connection,
		`${jobPath(jobId)}/layout/sheets/${sheet}?rows=${by}-${until}&lang=${language}`,
		{ signal }
	);
}

/**
 * Apply masking and review to a file as a dry run: the sidecar computes codes
 * but does not write to the translation table.
 */
export function applyMasking(
	connection: SidecarConnection,
	jobId: string,
	payload: ApplyRequest
): Promise<ApplyResponse> {
	return request<ApplyResponse>(connection, `${jobPath(jobId)}/apply`, json('POST', payload));
}

/**
 * Facts for the audit report of a save – counters only, never content.
 * Without `exportId` the latest save is used.
 */
export function fetchEntryReport(
	connection: SidecarConnection,
	id: string,
	exportId?: string
): Promise<EntryReportResponse> {
	const query = exportId ? `?export_id=${encodeURIComponent(exportId)}` : '';
	return request<EntryReportResponse>(connection, `${path(id)}/report${query}`);
}

/** "Cancel": abort a running detection and delete its data. */
export function cancelJob(connection: SidecarConnection, jobId: string): Promise<void> {
	return request<void>(connection, jobPath(jobId), { method: 'DELETE' });
}

export function fetchSettings(connection: SidecarConnection): Promise<SettingsResponse> {
	return request<SettingsResponse>(connection, '/settings');
}

/** Updates only the specified sections. */
export function updateSettings(
	connection: SidecarConnection,
	part: SettingsResponse
): Promise<SettingsResponse> {
	return request<SettingsResponse>(connection, '/settings', json('PUT', part));
}

/** Mask text on the fly; sidecar does not store anything. */
export function maskText(
	connection: SidecarConnection,
	payload: TextMaskRequest,
	signal?: AbortSignal
): Promise<TextMaskResponse> {
	return request<TextMaskResponse>(connection, '/text/mask', json('POST', payload, signal));
}

/** "Copy": Write mappings and add history entry "Text · copied". */
export function commitText(
	connection: SidecarConnection,
	payload: TextCommitRequest
): Promise<TextCommitResponse> {
	return request<TextCommitResponse>(connection, '/text/commit', json('POST', payload));
}

/**
 * Restore a KI answer in memory only. The `record` ("Copy", "Save as file")
 * entry counts as a restore; the answer is never persisted.
 */
export function restoreText(
	connection: SidecarConnection,
	payload: RestoreTextRequest,
	signal?: AbortSignal
): Promise<RestoreTextResponse> {
	return request<RestoreTextResponse>(connection, '/restore/text', json('POST', payload, signal));
}

/** "Remove": The restored file disappears from sidecar memory. */
export function discardRestoredFile(connection: SidecarConnection, id: string): Promise<void> {
	return request<void>(connection, `/restore/file/${encodeURIComponent(id)}`, {
		method: 'DELETE'
	});
}

/** "Clear history…": removes all entries and file lists; the table stays. */
export function clearEntries(connection: SidecarConnection): Promise<void> {
	return request<void>(connection, '/entries?confirm=true', { method: 'DELETE' });
}

// -- Translation table ---------------------------------------------------------

export function fetchVault(connection: SidecarConnection): Promise<VaultStatusResponse> {
	return request<VaultStatusResponse>(connection, '/vault/status');
}

/** "New key…": All existing codes become unreadable. */
export function rotateVault(connection: SidecarConnection): Promise<VaultStatusResponse> {
	return request<VaultStatusResponse>(
		connection,
		'/vault/rotate',
		json('POST', { confirm_key_change: true })
	);
}

/** "Clear table…": The key stays, mappings are removed. */
export function clearVault(connection: SidecarConnection): Promise<VaultStatusResponse> {
	return request<VaultStatusResponse>(connection, '/vault/clear', json('POST', { confirm: true }));
}

// -- Names & Terms -------------------------------------------------------------

export function fetchLists(connection: SidecarConnection): Promise<ListsResponse> {
	return request<ListsResponse>(connection, '/lists');
}

export function deleteList(connection: SidecarConnection, id: string): Promise<void> {
	return request<void>(connection, `/lists/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function fetchTerms(connection: SidecarConnection): Promise<TermsResponse> {
	return request<TermsResponse>(connection, '/terms');
}

/** Replaces all custom terms. */
export function updateTerms(
	connection: SidecarConnection,
	terms: TermIn[]
): Promise<TermsResponse> {
	return request<TermsResponse>(connection, '/terms', json('PUT', { terms }));
}

export function fetchPatterns(connection: SidecarConnection): Promise<PatternsResponse> {
	return request<PatternsResponse>(connection, '/patterns');
}

/** Replaces all custom patterns; unsuitable ones are rejected with code and index. */
export function updatePatterns(
	connection: SidecarConnection,
	patterns: PatternIn[]
): Promise<PatternsResponse> {
	return request<PatternsResponse>(connection, '/patterns', json('PUT', { patterns }));
}

/**
 * "Try out" with the sidecar's regex engine—the same used later for documents.
 * Supplied `patterns` override stored ones.
 */
export function testPatterns(
	connection: SidecarConnection,
	text: string,
	patterns?: PatternIn[],
	signal?: AbortSignal
): Promise<PatternTestResponse> {
	return request<PatternTestResponse>(
		connection,
		'/patterns/test',
		json('POST', patterns ? { text, patterns } : { text }, signal)
	);
}

export function fetchTemplates(connection: SidecarConnection): Promise<TemplatesResponse> {
	return request<TemplatesResponse>(connection, '/templates');
}

/** "Save as …": store terms, patterns and masking standard under a name. */
export function saveTemplate(
	connection: SidecarConnection,
	title: string
): Promise<TemplatesResponse> {
	return request<TemplatesResponse>(connection, '/templates', json('POST', { title }));
}

/** Choose template: replace current terms and patterns with yours. */
export function applyTemplate(
	connection: SidecarConnection,
	id: string
): Promise<TemplateApplyResponse> {
	return request<TemplateApplyResponse>(connection, `/templates/${encodeURIComponent(id)}/apply`, {
		method: 'POST'
	});
}

/** Without `id`: current state saved under `title`. */
export function exportTemplate(
	connection: SidecarConnection,
	template: { id: string } | { title: string }
): Promise<TemplateExportResponse> {
	return request<TemplateExportResponse>(connection, '/templates/export', json('POST', template));
}

export function importTemplate(
	connection: SidecarConnection,
	content: string
): Promise<TemplateApplyResponse> {
	return request<TemplateApplyResponse>(connection, '/templates/import', json('POST', { content }));
}

/** Models available on this device (pre-installed up to initial release). */
export function fetchModels(connection: SidecarConnection): Promise<ModelsResponse> {
	return request<ModelsResponse>(connection, '/models');
}
