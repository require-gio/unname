import { describe, expect, it } from 'vitest';
import { translate } from '#lib/i18n/translate.js';
import type { Entry, EntryFile } from '#lib/sidecar/api.gen.js';
import { entryNotice } from './disk-space.js';
import { fileStatus, entryTarget, runningStill, historyMeta } from './entry-meta.js';

/** Vitest runs with TZ=Europe/Berlin (vite.config.ts). */
const t = translate.bind(null, 'de');

describe('Restored entry destination', () => {
	const timestamp = '2026-10-23T05:00:00Z';
	it('opens processing and saved entries on their board and reviews in the document', () => {
		expect(entryTarget(makeEntry({ created_at: timestamp, status: 'processing' }))).toBe('entry');
		expect(
			entryTarget(makeEntry({ created_at: timestamp, status: 'saved' }), { active_file_id: 'f' })
		).toBe('entry');
		expect(entryTarget(makeEntry({ created_at: timestamp, status: 'review' }))).toBe('review');
	});
	it('restores a failed file in the document only when it was open there', () => {
		const entry = makeEntry({ created_at: timestamp, status: 'failed' });
		expect(entryTarget(entry)).toBe('entry');
		expect(entryTarget(entry, { active_file_id: entry.files![0].id })).toBe('review');
	});
});

const MASKING: Entry['masking'] = {
	method: 'reversible',
	style: 'codes',
	categories: ['finance_ids', 'names'],
	anonymize: false
};

function file(label: EntryFile['label'] = null): EntryFile {
	return { id: crypto.randomUUID(), name: 'x.pdf', format: 'pdf', size_bytes: 1, label };
}

function makeEntry(part: Partial<Entry> & Pick<Entry, 'created_at'>): Entry {
	return {
		id: crypto.randomUUID(),
		title: 'Rechnung',
		kind: 'documents',
		status: 'review',
		updated_at: part.created_at,
		masking: { ...MASKING, categories: [...MASKING.categories] },
		files: [file()],
		...part
	};
}

describe('Meta line', () => {
	const basis = { created_at: '2026-10-23T05:00:00Z' };

	it('includes type and status for text', () => {
		expect(
			historyMeta(makeEntry({ ...basis, kind: 'text', status: 'copied', files: [] }), t)
		).toEqual({ text: 'Text · kopiert', tone: 'normal' });
	});

	it('counts files and colors running and failed entries', () => {
		const laufend = makeEntry({ ...basis, status: 'processing', files: [file(), file(), file()] });
		expect(historyMeta(laufend, t)).toEqual({ text: '3 Dateien · wird geprüft', tone: 'running' });
		expect(historyMeta(makeEntry({ ...basis, status: 'failed' }), t)).toEqual({
			text: '1 Datei · fehlgeschlagen',
			tone: 'error'
		});
	});

	it('uses the strongest label on save only if every file has it', () => {
		const irreversible: Entry['masking'] = {
			...MASKING,
			method: 'irreversible',
			style: 'placeholder'
		};
		const saved = (files: EntryFile[], masking: Entry['masking'] = { ...MASKING }) =>
			historyMeta(makeEntry({ ...basis, status: 'saved', files: files, masking }), t).text;

		expect(saved([file('reversible')])).toBe('1 Datei · gespeichert');
		expect(saved([file('irreversible'), file('irreversible')], { ...irreversible })).toBe(
			'2 Dateien · irreversibel'
		);
		expect(saved([file('anonymous'), file('anonymous')], { ...irreversible })).toBe(
			'2 Dateien · anonym'
		);
		expect(saved([file('anonymous'), file('irreversible')], { ...irreversible })).toBe(
			'2 Dateien · irreversibel'
		);
	});

	it('counts restores instead of "saved" or "copied"', () => {
		const files = [file('reversible'), file('reversible'), file('reversible')];
		expect(
			historyMeta(makeEntry({ ...basis, status: 'saved', files: files, restores: 1 }), t).text
		).toBe('3 Dateien · 1 Rückübersetzung');
		expect(
			historyMeta(makeEntry({ ...basis, kind: 'text', status: 'copied', restores: 2 }), t).text
		).toBe('Text · 2 Rückübersetzungen');
		// An irreversibly saved entry keeps the label.
		const irreversible = makeEntry({
			...basis,
			status: 'saved',
			files: [file('irreversible')],
			masking: { ...MASKING, method: 'irreversible', style: 'placeholder' },
			restores: 1
		});
		expect(historyMeta(irreversible, t).text).toBe('1 Datei · irreversibel');
	});

	it('works without files using only the status', () => {
		expect(historyMeta(makeEntry({ ...basis, status: 'processing', files: [] }), t).text).toBe(
			'wird geprüft'
		);
	});
});

describe('Inquiries', () => {
	it('only while an entry is still running', () => {
		const basis = { created_at: '2026-10-23T05:00:00Z' };
		expect(runningStill([makeEntry({ ...basis, status: 'review' })])).toBe(false);
		expect(runningStill([makeEntry({ ...basis, status: 'processing' })])).toBe(true);
	});
});

describe('File status', () => {
	it('shows waiting, running and done and explains errors', () => {
		expect(fileStatus({ status: 'queued' })).toEqual({ status: 'waits' });
		expect(fileStatus({ status: 'processing' })).toEqual({ status: 'running' });
		expect(fileStatus({ status: 'review' })).toEqual({ status: 'done', text: 'file.ready' });
		expect(fileStatus({ status: 'failed', error_code: 'ocr_unavailable' })).toEqual({
			status: 'error',
			text: 'rejection.ocr'
		});
		expect(fileStatus({ status: 'expired' }).text).toBe('file.expired');
		expect(fileStatus({ status: 'unavailable' })).toEqual({
			status: 'error',
			text: 'file.unavailable'
		});
	});
});

describe('continue after saving', () => {
	const created_at = '2026-11-14T09:00:00Z';

	it('shows changed label when continued', () => {
		const saved = makeEntry({
			created_at,
			status: 'saved',
			draft_revision: 3,
			saved_revision: 3
		});
		expect(historyMeta(saved, t)).toEqual({ text: '1 Datei · gespeichert', tone: 'normal' });
		const changed = { ...saved, draft_revision: 4 };
		expect(historyMeta(changed, t)).toEqual({
			text: '1 Datei · seit dem Speichern geändert',
			tone: 'question'
		});
	});

	it('includes size and deletion date in tooltip', () => {
		const entry = makeEntry({ created_at });
		const storage = {
			total_bytes: 1_300_000,
			free_bytes: null,
			low_space_bytes: 1,
			retention_days: 30,
			entries: [{ entry_id: entry.id, bytes: 1_300_000, delete_at: '2026-12-14T09:00:00Z' }]
		};
		expect(entryNotice(storage, entry.id, t)).toBe(
			'1,2 MB gespeichert · wird am 14.12.2026 gelöscht'
		);
		expect(entryNotice(null, entry.id, t)).toBe(undefined);
	});
});
