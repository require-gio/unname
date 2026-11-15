import { historyRow, type HistoryRow } from '#lib/history/history.js';
import type { Locale, Translator } from '#lib/i18n/translate.js';
import type { Entry } from '#lib/sidecar/api.gen.js';

/** Example entries for “Recently created” – fixed timestamps for the reference image. */
const NOW = new Date('2026-11-14T15:00:00+01:00');

function example(part: Partial<Entry> & Pick<Entry, 'id' | 'title' | 'created_at'>): Entry {
	return {
		kind: 'documents',
		status: 'review',
		updated_at: part.created_at,
		masking: { method: 'reversible', style: 'codes', categories: ['names'], anonymize: false },
		files: [],
		...part
	};
}

export function lastExamples(language: Locale, t: Translator): HistoryRow[] {
	return [
		example({
			id: '1',
			display_ref: 'K7M2QX',
			title: 'Dokument',
			created_at: '2026-11-14T14:32:00+01:00',
			files: [{ id: 'a', name: 'a', format: 'pdf', size_bytes: 1, pages: 2, ocr_used: true }]
		}),
		example({
			id: '2',
			display_ref: 'N4P8RT',
			title: 'Dokument',
			created_at: '2026-11-14T10:18:00+01:00',
			status: 'saved',
			files: [{ id: 'b', name: 'b', format: 'pdf', size_bytes: 1, pages: 8, ocr_used: true }]
		}),
		example({
			id: '3',
			display_ref: 'R6W3HZ',
			title: 'E-Mail-Entwurf',
			kind: 'text',
			status: 'copied',
			created_at: '2026-11-13T16:05:00+01:00'
		})
	].map((entry) => historyRow(entry, NOW, language, t));
}
