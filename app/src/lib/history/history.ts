import { sinceTheSaveChanged } from '#lib/saved/saved.js';
import type { Locale, Translator } from '#lib/i18n/translate.js';
import type { Entry, EntryFile } from '#lib/sidecar/api.gen.js';
import { entryTarget } from '#lib/history/entry-meta.js';

/**
 * The history model behind the sidebar navigation and the history view
 * (sidebar/history plan, sections 3.2–3.5).
 *
 * Everything here is pure: workflow classification, counts, safe metadata,
 * local search, sorting and the recent entries. Nothing is derived from
 * document contents, original file names or findings — only from the entry
 * metadata the sidecar already returns.
 */

export type HistoryScope = 'open' | 'completed' | 'all';
export type HistorySort = 'newest' | 'oldest';
export type HistoryStatus = 'processing' | 'review' | 'failed' | 'changed' | 'saved' | 'copied';
/** Visual treatment; color never carries the status alone (the label always shows). */
export type HistoryTone = 'quiet' | 'warning' | 'progress' | 'error';

export const HISTORY_SCOPES: readonly HistoryScope[] = ['open', 'completed', 'all'];
export const RECENT_LIMIT = 3;
export const PAGE_SIZE = 50;

export interface CreatedLabel {
	/** "Heute", "Gestern" or a short localized date, with the year when needed. */
	day: string;
	time: string;
	/** Date and time spelled out, for accessible names and disclosures. */
	full: string;
}

export interface HistoryRow {
	id: string;
	entry: Entry;
	title: string;
	/** The stored short reference, or the full entry ID for legacy data. */
	reference: string;
	referenceIsFallback: boolean;
	extent: string;
	created: CreatedLabel;
	createdAt: number;
	status: HistoryStatus;
	scope: Exclude<HistoryScope, 'all'>;
	tone: HistoryTone;
	target: 'entry' | 'review';
	/** Normalized search fields: title, reference, extent/format, dates. */
	searchFields: readonly string[];
	normalizedReference: string;
}

// -- Workflow classification -------------------------------------------------

/** One classifier for navigation counts, filters, badges and tests (plan 3.2). */
export function historyStatus(
	entry: Pick<Entry, 'status' | 'draft_revision' | 'saved_revision'>
): HistoryStatus {
	switch (entry.status ?? 'processing') {
		case 'processing':
			return 'processing';
		case 'review':
			return 'review';
		case 'failed':
			return 'failed';
		case 'copied':
			return 'copied';
		case 'saved':
			return sinceTheSaveChanged(entry) ? 'changed' : 'saved';
	}
}

export function statusScope(status: HistoryStatus): Exclude<HistoryScope, 'all'> {
	return status === 'saved' || status === 'copied' ? 'completed' : 'open';
}

export function statusTone(status: HistoryStatus): HistoryTone {
	switch (status) {
		case 'processing':
			return 'progress';
		case 'review':
		case 'changed':
			return 'warning';
		case 'failed':
			return 'error';
		default:
			return 'quiet';
	}
}

export function inScope(
	entry: Pick<Entry, 'status' | 'draft_revision' | 'saved_revision'>,
	scope: HistoryScope
): boolean {
	return scope === 'all' || statusScope(historyStatus(entry)) === scope;
}

/** Counts cover the whole collection, independent of search and rendered page size. */
export function historyCounts(
	entries: readonly Pick<Entry, 'status' | 'draft_revision' | 'saved_revision'>[]
): Record<HistoryScope, number> {
	let open = 0;
	for (const entry of entries) if (statusScope(historyStatus(entry)) === 'open') open++;
	return { open, completed: entries.length - open, all: entries.length };
}

// -- URL state ---------------------------------------------------------------

export function parseScope(value: string | null | undefined): HistoryScope {
	return value === 'open' || value === 'completed' ? value : 'all';
}

export function parseSort(value: string | null | undefined): HistorySort {
	return value === 'oldest' ? 'oldest' : 'newest';
}

/** Query string of a history destination; defaults are left out. */
export function historyQuery(scope: HistoryScope, sort: HistorySort = 'newest'): string {
	const params = new URLSearchParams({ view: scope });
	if (sort !== 'newest') params.set('sort', sort);
	return `?${params}`;
}

// -- Metadata ----------------------------------------------------------------

const LOCALE: Record<Locale, string> = { de: 'de-AT', en: 'en-GB' };

/** "PDF", "DOCX" — or the localized scan indicator when OCR read the file. */
export function formatLabel(file: Pick<EntryFile, 'format' | 'ocr_used'>, t: Translator): string {
	const format = (file.format || '?').toUpperCase();
	return file.ocr_used ? t('history.format.scan', { format }) : format;
}

function knownPages(file: Pick<EntryFile, 'pages'>): number | null {
	return typeof file.pages === 'number' && file.pages > 0 ? file.pages : null;
}

/**
 * "PDF-Scan · 8 Seiten", "3 Dateien · PDF, DOCX +1 · 12 Seiten", "Text".
 * Unknown page counts are left out; a batch total appears only when every
 * file has a known count.
 */
export function entryExtent(entry: Pick<Entry, 'kind' | 'files'>, t: Translator): string {
	if (entry.kind === 'text') return t('historyList.text');
	const files = entry.files ?? [];
	if (files.length === 0) return t('history.extent.empty');
	if (files.length === 1) {
		const pages = knownPages(files[0]);
		const format = formatLabel(files[0], t);
		return pages === null ? format : `${format} · ${t('history.pages', { n: pages })}`;
	}
	const formats = [...new Set(files.map((file) => formatLabel(file, t)))];
	let summary = formats.slice(0, 2).join(', ');
	if (formats.length > 2) summary += ` ${t('history.format.more', { n: formats.length - 2 })}`;
	const parts = [t('historyList.files', { n: files.length }), summary];
	const pages = files.map(knownPages);
	if (pages.every((value) => value !== null)) {
		parts.push(t('history.pages', { n: pages.reduce<number>((sum, value) => sum + value!, 0) }));
	}
	return parts.join(' · ');
}

function dayStart(time: Date): number {
	return new Date(time.getFullYear(), time.getMonth(), time.getDate()).getTime();
}

/** Calendar days in local time; a clock in the future counts as today. */
function daysAgo(time: Date, now: Date): number {
	return Math.round((dayStart(now) - dayStart(time)) / 86_400_000);
}

export function createdLabel(
	createdAt: string,
	now: Date,
	language: Locale,
	t: Translator
): CreatedLabel {
	const time = new Date(createdAt);
	const locale = LOCALE[language];
	const clock = new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(
		time
	);
	const full = new Intl.DateTimeFormat(locale, { dateStyle: 'long', timeStyle: 'short' }).format(
		time
	);
	const ago = daysAgo(time, now);
	let day: string;
	if (ago <= 0) day = t('historyList.today');
	else if (ago === 1) day = t('historyList.yesterday');
	else {
		const sameYear = time.getFullYear() === now.getFullYear();
		day = new Intl.DateTimeFormat(locale, {
			day: 'numeric',
			month: 'short',
			...(sameYear ? {} : { year: 'numeric' })
		}).format(time);
	}
	return { day, time: clock, full };
}

/** Local calendar date as ISO (`2026-11-14`) and as numeric dates in both languages. */
function dateSearchForms(createdAt: string): string[] {
	const time = new Date(createdAt);
	const pad = (value: number) => String(value).padStart(2, '0');
	const year = time.getFullYear();
	const month = time.getMonth() + 1;
	const day = time.getDate();
	return [
		`${year}-${pad(month)}-${pad(day)}`,
		`${day}.${month}.${year}`,
		`${pad(day)}.${pad(month)}.${year}`,
		`${pad(day)}/${pad(month)}/${year}`
	];
}

/** The reference users see; legacy data without one falls back to the full ID. */
export function displayReference(entry: Pick<Entry, 'id' | 'display_ref'>): string {
	return entry.display_ref || entry.id;
}

// -- Rows, search and sorting ------------------------------------------------

/** Case- and accent-insensitive, Unicode-normalized. */
export function normalize(text: string): string {
	return text.normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase('de').replace(/ß/g, 'ss');
}

function normalizeTerm(term: string): string {
	return normalize(term.startsWith('#') ? term.slice(1) : term);
}

export function historyRow(entry: Entry, now: Date, language: Locale, t: Translator): HistoryRow {
	const status = historyStatus(entry);
	const reference = displayReference(entry);
	const extent = entryExtent(entry, t);
	const created = createdLabel(entry.created_at, now, language, t);
	const formats = (entry.files ?? []).map((file) => formatLabel(file, t));
	const parsed = Date.parse(entry.created_at);
	return {
		id: entry.id,
		entry,
		title: entry.title,
		reference,
		referenceIsFallback: !entry.display_ref,
		extent,
		created,
		createdAt: Number.isNaN(parsed) ? 0 : parsed,
		status,
		scope: statusScope(status),
		tone: statusTone(status),
		target: entryTarget(entry),
		searchFields: [
			entry.title,
			reference,
			extent,
			...formats,
			created.day,
			created.time,
			created.full,
			...dateSearchForms(entry.created_at)
		].map(normalize),
		normalizedReference: normalize(reference)
	};
}

export function historyRows(
	entries: readonly Entry[],
	now: Date,
	language: Locale,
	t: Translator
): HistoryRow[] {
	return entries.map((entry) => historyRow(entry, now, language, t));
}

export function compareRows(sort: HistorySort): (a: HistoryRow, b: HistoryRow) => number {
	const direction = sort === 'oldest' ? 1 : -1;
	return (a, b) => {
		if (a.createdAt !== b.createdAt) return (a.createdAt - b.createdAt) * direction;
		return a.id < b.id ? -direction : a.id > b.id ? direction : 0;
	};
}

export function searchTerms(query: string): string[] {
	return query
		.split(/\s+/u)
		.map(normalizeTerm)
		.filter((term) => term.length > 0);
}

/** Every term must appear in at least one allowed field. */
export function matchesTerms(row: HistoryRow, terms: readonly string[]): boolean {
	return terms.every((term) => row.searchFields.some((field) => field.includes(term)));
}

export interface HistoryResult {
	rows: HistoryRow[];
	/** Rows whose reference equals one of the terms exactly; listed first. */
	exact: ReadonlySet<string>;
	/** Rows in the scope before searching. */
	scopeTotal: number;
}

/**
 * Scope, search and sort over the whole collection — before any rendering
 * limit. An exact reference match comes first so it is easy to spot even when
 * its characters also occur in other rows.
 */
export function queryHistory(
	rows: readonly HistoryRow[],
	options: { scope: HistoryScope; query: string; sort: HistorySort }
): HistoryResult {
	const scoped = rows.filter((row) => options.scope === 'all' || row.scope === options.scope);
	const terms = searchTerms(options.query);
	const matching = terms.length ? scoped.filter((row) => matchesTerms(row, terms)) : [...scoped];
	matching.sort(compareRows(options.sort));
	const exact = new Set(
		matching.filter((row) => terms.includes(row.normalizedReference)).map((row) => row.id)
	);
	const rowsOut = exact.size
		? [
				...matching.filter((row) => exact.has(row.id)),
				...matching.filter((row) => !exact.has(row.id))
			]
		: matching;
	return { rows: rowsOut, exact, scopeTotal: scoped.length };
}

/** The sidebar's recent entries: by creation time, never reordered by opening or renaming. */
export function recentEntries(entries: readonly Entry[], limit = RECENT_LIMIT): Entry[] {
	const time = (entry: Entry) => {
		const parsed = Date.parse(entry.created_at);
		return Number.isNaN(parsed) ? 0 : parsed;
	};
	return [...entries]
		.sort((a, b) => time(b) - time(a) || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0))
		.slice(0, limit);
}
