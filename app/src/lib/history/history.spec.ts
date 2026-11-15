import { describe, expect, it } from 'vitest';
import { translate } from '#lib/i18n/translate.js';
import type { Entry, EntryFile } from '#lib/sidecar/api.gen.js';
import {
	createdLabel,
	displayReference,
	entryExtent,
	formatLabel,
	historyCounts,
	historyQuery,
	historyRows,
	historyStatus,
	inScope,
	parseScope,
	parseSort,
	queryHistory,
	recentEntries,
	statusScope,
	statusTone,
	type HistoryScope
} from './history.js';

/** Vitest runs with TZ=Europe/Berlin (vite.config.ts). */
const t = translate.bind(null, 'de');
const tEn = translate.bind(null, 'en');
const NOW = new Date('2026-11-14T15:00:00+01:00');

let counter = 0;

function file(part: Partial<EntryFile> = {}): EntryFile {
	counter++;
	return {
		id: `f-${counter}`,
		name: `Angebot_Hofer_${counter}.pdf`,
		format: 'pdf',
		size_bytes: 1,
		pages: 2,
		ocr_used: false,
		...part
	};
}

function entry(part: Partial<Entry> = {}): Entry {
	counter++;
	return {
		id: `e-${String(counter).padStart(4, '0')}`,
		display_ref: `R${String(counter).padStart(5, '2').replace(/[01]/g, '2')}`,
		title: 'Dokument',
		kind: 'documents',
		status: 'review',
		created_at: '2026-11-14T10:00:00+01:00',
		updated_at: '2026-11-14T10:00:00+01:00',
		masking: { method: 'reversible', style: 'codes', categories: ['names'], anonymize: false },
		files: [file()],
		...part
	};
}

describe('workflow classification', () => {
	const cases: [Partial<Entry>, string, HistoryScope, string][] = [
		[{ status: 'processing' }, 'processing', 'open', 'Wird verarbeitet'],
		[{ status: undefined }, 'processing', 'open', 'Wird verarbeitet'],
		[{ status: 'review' }, 'review', 'open', 'Zu prüfen'],
		[{ status: 'failed' }, 'failed', 'open', 'Fehler'],
		[
			{ status: 'saved', draft_revision: 4, saved_revision: 3 },
			'changed',
			'open',
			'Seit dem Speichern geändert'
		],
		[
			{ status: 'saved', draft_revision: 3, saved_revision: 3 },
			'saved',
			'completed',
			'Gespeichert'
		],
		// Legacy saved entries without revision data count as unchanged.
		[{ status: 'saved' }, 'saved', 'completed', 'Gespeichert'],
		[{ status: 'copied', kind: 'text', files: [] }, 'copied', 'completed', 'Kopiert']
	];

	it.each(cases)('%o is %s in %s', (part, status, scope, label) => {
		const value = historyStatus(entry(part));
		expect(value).toBe(status);
		expect(statusScope(value)).toBe(scope);
		expect(t(`history.status.${value}` as Parameters<typeof t>[0])).toBe(label);
	});

	it('an irreversible saved result is still just saved', () => {
		const saved = entry({
			status: 'saved',
			draft_revision: 1,
			saved_revision: 1,
			masking: { method: 'irreversible', style: 'placeholder', categories: [], anonymize: true },
			files: [file({ label: 'anonymous' })]
		});
		expect(historyStatus(saved)).toBe('saved');
	});

	it('gives every status a tone and puts each entry in exactly one scope', () => {
		const entries = cases.map(([part]) => entry(part));
		const counts = historyCounts(entries);
		expect(counts).toEqual({ open: 5, completed: 3, all: 8 });
		for (const item of entries) {
			expect(inScope(item, 'all')).toBe(true);
			expect(inScope(item, 'open')).not.toBe(inScope(item, 'completed'));
		}
		expect(statusTone('saved')).toBe('quiet');
		expect(statusTone('changed')).toBe('warning');
		expect(statusTone('processing')).toBe('progress');
		expect(statusTone('failed')).toBe('error');
	});
});

describe('URL state', () => {
	it('falls back to all entries and newest first', () => {
		expect(parseScope(null)).toBe('all');
		expect(parseScope('bogus')).toBe('all');
		expect(parseScope('open')).toBe('open');
		expect(parseSort(null)).toBe('newest');
		expect(parseSort('oldest')).toBe('oldest');
		expect(historyQuery('completed')).toBe('?view=completed');
		expect(historyQuery('all', 'oldest')).toBe('?view=all&sort=oldest');
	});
});

describe('metadata', () => {
	it('labels scans only when OCR read the file', () => {
		expect(formatLabel({ format: 'pdf', ocr_used: true }, t)).toBe('PDF-Scan');
		expect(formatLabel({ format: 'pdf', ocr_used: false }, t)).toBe('PDF');
		expect(formatLabel({ format: 'png', ocr_used: true }, t)).toBe('PNG-Scan');
		expect(formatLabel({ format: 'pdf', ocr_used: true }, tEn)).toBe('PDF scan');
	});

	it('describes single files, batches and texts without inventing counts', () => {
		expect(entryExtent(entry({ files: [file({ ocr_used: true, pages: 8 })] }), t)).toBe(
			'PDF-Scan · 8 Seiten'
		);
		expect(entryExtent(entry({ files: [file({ pages: 1 })] }), t)).toBe('PDF · 1 Seite');
		expect(entryExtent(entry({ files: [file({ format: 'docx', pages: null })] }), t)).toBe('DOCX');
		expect(entryExtent(entry({ files: [file({ pages: 0 })] }), t)).toBe('PDF');
		expect(entryExtent(entry({ kind: 'text', files: [] }), t)).toBe('Text');
		expect(entryExtent(entry({ files: [] }), t)).toBe('Keine Dateien');

		const complete = entry({ files: [file({ pages: 2 }), file({ format: 'docx', pages: 3 })] });
		expect(entryExtent(complete, t)).toBe('2 Dateien · PDF, DOCX · 5 Seiten');

		// One unknown page count: no total that only looks complete.
		const partial = entry({ files: [file({ pages: 2 }), file({ format: 'docx', pages: null })] });
		expect(entryExtent(partial, t)).toBe('2 Dateien · PDF, DOCX');

		const mixed = entry({
			files: [
				file({ format: 'pdf' }),
				file({ format: 'docx' }),
				file({ format: 'xlsx' }),
				file({ format: 'png', ocr_used: true })
			]
		});
		expect(entryExtent(mixed, t)).toBe('4 Dateien · PDF, DOCX +2 weitere · 8 Seiten');
	});

	it('never uses file names', () => {
		const item = entry({ files: [file({ name: 'Angebot_Hofer.pdf' })] });
		const [row] = historyRows([item], NOW, 'de', t);
		expect(row.searchFields.join(' ')).not.toContain('hofer');
		expect(row.extent).not.toContain('Hofer');
	});

	it('shows today, yesterday and dates with the year when it differs', () => {
		expect(createdLabel('2026-11-14T09:05:00+01:00', NOW, 'de', t)).toMatchObject({
			day: 'Heute',
			time: '09:05'
		});
		expect(createdLabel('2026-11-13T23:30:00+01:00', NOW, 'de', t).day).toBe('Gestern');
		expect(createdLabel('2026-11-02T12:00:00+01:00', NOW, 'de', t).day).toBe('2. Nov.');
		expect(createdLabel('2025-11-02T12:00:00+01:00', NOW, 'de', t).day).toBe('2. Nov. 2025');
		expect(createdLabel('2026-11-14T09:05:00+01:00', NOW, 'en', tEn).day).toBe('Today');
	});

	it('counts calendar days across the DST change and treats the future as today', () => {
		const afterChange = new Date('2026-10-26T09:00:00+01:00');
		expect(createdLabel('2026-10-25T01:00:00+02:00', afterChange, 'de', t).day).toBe('Gestern');
		expect(createdLabel('2026-11-15T10:00:00+01:00', NOW, 'de', t).day).toBe('Heute');
	});

	it('falls back to the full ID without a stored reference', () => {
		expect(displayReference({ id: 'abc123', display_ref: 'K7M2QX' })).toBe('K7M2QX');
		expect(displayReference({ id: 'abc123', display_ref: null })).toBe('abc123');
		expect(displayReference({ id: 'abc123' })).toBe('abc123');
	});
});

describe('search and sorting', () => {
	const entries = [
		entry({
			id: 'e-a',
			display_ref: 'K7M2QX',
			title: 'Vertrag',
			created_at: '2026-11-14T09:00:00+01:00',
			files: [file({ format: 'docx', pages: 6 })]
		}),
		entry({
			id: 'e-b',
			display_ref: 'N4P8RT',
			title: 'Dokument',
			created_at: '2026-11-12T11:42:00+01:00',
			files: [file({ ocr_used: true, pages: 8 })],
			status: 'saved',
			draft_revision: 1,
			saved_revision: 1
		}),
		entry({
			id: 'e-c',
			display_ref: 'K7MAAA',
			title: 'Dokument K7M2QX-ähnlich',
			created_at: '2026-11-10T08:00:00+01:00'
		}),
		entry({
			id: 'e-d',
			display_ref: null,
			title: 'Größe Übersicht',
			kind: 'text',
			files: [],
			status: 'copied',
			created_at: '2025-03-01T08:00:00+01:00'
		})
	];
	const rows = historyRows(entries, NOW, 'de', t);
	const search = (query: string, scope: HistoryScope = 'all', sort = 'newest' as const) =>
		queryHistory(rows, { scope, query, sort }).rows.map((row) => row.id);

	it('matches title, reference with or without #, format and dates', () => {
		expect(search('vertrag')).toEqual(['e-a']);
		expect(search('#n4p8rt')).toEqual(['e-b']);
		expect(search('pdf-scan')).toEqual(['e-b']);
		expect(search('docx')).toEqual(['e-a']);
		expect(search('2026-11-12')).toEqual(['e-b']);
		expect(search('12.11.2026')).toEqual(['e-b']);
		expect(search('heute')).toEqual(['e-a']);
		expect(search('text')).toEqual(['e-d']);
	});

	it('normalizes case and accents and requires every term', () => {
		expect(search('GROSSE')).toEqual(['e-d']);
		expect(search('größe')).toEqual(['e-d']);
		expect(search('übersicht')).toEqual(['e-d']);
		expect(search('UBERSICHT')).toEqual(['e-d']);
		expect(search('dokument scan')).toEqual(['e-b']);
		expect(search('dokument xyz')).toEqual([]);
	});

	it('puts an exact reference match first', () => {
		const result = queryHistory(rows, { scope: 'all', query: 'K7M2QX', sort: 'oldest' });
		expect(result.rows.map((row) => row.id)).toEqual(['e-a', 'e-c']);
		expect([...result.exact]).toEqual(['e-a']);
	});

	it('searches within the scope before limiting', () => {
		expect(search('dokument', 'completed')).toEqual(['e-b']);
		expect(search('', 'open')).toEqual(['e-a', 'e-c']);
		expect(queryHistory(rows, { scope: 'open', query: 'vertrag', sort: 'newest' }).scopeTotal).toBe(
			2
		);
	});

	it('sorts by creation time with the ID as tie-breaker', () => {
		expect(search('')).toEqual(['e-a', 'e-b', 'e-c', 'e-d']);
		expect(search('', 'all', 'oldest' as never)).toEqual(['e-d', 'e-c', 'e-b', 'e-a']);
		const same = historyRows(
			[
				entry({ id: 'x-2', created_at: '2026-11-14T09:00:00+01:00' }),
				entry({ id: 'x-1', created_at: '2026-11-14T09:00:00+01:00' })
			],
			NOW,
			'de',
			t
		);
		const order = (sort: 'newest' | 'oldest') =>
			queryHistory(same, { scope: 'all', query: '', sort }).rows.map((row) => row.id);
		expect(order('newest')).toEqual(['x-2', 'x-1']);
		expect(order('oldest')).toEqual(['x-1', 'x-2']);
	});

	it('does not move a row after renaming or status changes', () => {
		const renamed = entries.map((item) =>
			item.id === 'e-c'
				? { ...item, title: 'Zzz', status: 'saved' as const, updated_at: NOW.toISOString() }
				: item
		);
		const after = queryHistory(historyRows(renamed, NOW, 'de', t), {
			scope: 'all',
			query: '',
			sort: 'newest'
		});
		expect(after.rows.map((row) => row.id)).toEqual(['e-a', 'e-b', 'e-c', 'e-d']);
	});
});

describe('recent entries', () => {
	it('shows at most three, by creation time only', () => {
		const many = Array.from({ length: 20 }, (_, index) =>
			entry({
				id: `r-${String(index).padStart(2, '0')}`,
				title: 'Dokument',
				created_at: new Date(Date.UTC(2026, 10, 1, index)).toISOString(),
				// Recently updated entries do not move up.
				updated_at: new Date(Date.UTC(2026, 10, 14, 20 - index)).toISOString()
			})
		);
		expect(recentEntries(many).map((item) => item.id)).toEqual(['r-19', 'r-18', 'r-17']);
		const rows = historyRows(many, NOW, 'de', t);
		expect(
			queryHistory(rows, { scope: 'all', query: 'dokument', sort: 'newest' }).rows
		).toHaveLength(20);
	});
});
