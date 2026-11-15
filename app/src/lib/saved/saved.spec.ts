import { describe, expect, it } from 'vitest';
import { translate } from '#lib/i18n/translate.js';
import type { Entry, EntryFile, EntryReportResponse, FileOutput } from '#lib/sidecar/api.gen.js';
import { entryReport, entryReportAsText } from '#lib/export/entry-report.js';
import {
	expired,
	needingOriginal,
	outputNotices,
	reportFileName,
	confirmationKey,
	labelFrom,
	savedNum,
	savedLines,
	lastSaving,
	methodText,
	folderShort,
	returnPath,
	sinceTheSaveChanged,
	savings,
	summary
} from './saved.js';

const t = (key: Parameters<typeof translate>[1], values?: Record<string, string | number>) =>
	translate('de', key, values);

function output(part: Partial<FileOutput> = {}): FileOutput {
	return {
		status: 'saved',
		name: 'Rechnung_geschützt.pdf',
		format: 'pdf',
		label: 'reversible',
		masked: 13,
		decided_by_user: 2,
		decided_by_ai: 0,
		masked_without_decision: 1,
		...part
	};
}

function file(id: string, name: string, output: FileOutput | null, part: Partial<EntryFile> = {}) {
	return { id, name, format: 'pdf', size_bytes: 1, status: 'review', output, ...part } as EntryFile;
}

function entry(files: EntryFile[], method: 'reversible' | 'irreversible' = 'reversible') {
	return {
		id: 'e-1',
		title: 'Rechnung & Angebot',
		kind: 'documents',
		status: 'saved',
		created_at: '2026-10-23T09:00:00Z',
		updated_at: '2026-10-23T09:00:00Z',
		masking: {
			method: method,
			style: method === 'reversible' ? 'codes' : 'placeholder',
			categories: ['finance_ids', 'names'],
			anonymize: false
		},
		files: files,
		output_dir: '/Users/giorgi/Dokumente/Kunden/geschützt'
	} as Entry;
}

describe('Saved view', () => {
	const saved = entry([
		file('j-1', 'Rechnung.pdf', output()),
		file(
			'j-2',
			'Vollmacht_Scan.jpg',
			output({
				name: 'Vollmacht_Scan_geschützt.txt',
				format: 'txt',
				from_scan: true,
				readback_failed: true,
				masked: 6,
				decided_by_user: 0,
				decided_by_ai: 1,
				masked_without_decision: 0,
				contains_surrogates: true
			})
		),
		file(
			'j-3',
			'Notiz.txt',
			output({
				status: 'unchanged',
				name: null,
				label: null,
				masked: 0,
				decided_by_user: 0,
				masked_without_decision: 0
			})
		)
	]);

	it('shows protected copies with label and the file without a copy', () => {
		const lines = savedLines(saved, t);
		expect(lines.map((line) => [line.name, line.meta, line.label, line.number])).toEqual([
			['Rechnung_geschützt.pdf', '13 Stellen maskiert', 'reversible', 1],
			[
				'Vollmacht_Scan_geschützt.txt',
				'6 Stellen maskiert · Als Text gespeichert – Code im Bild nicht sicher lesbar',
				'reversible',
				2
			],
			['Notiz.txt', 'Keine Stelle zu maskieren – keine Kopie nötig', null, 3]
		]);
		expect(lines[1].inventedNames).toBe(true);
		expect(savedNum(saved)).toBe(2);
	});

	it('aggregates positions and decisions by origin', () => {
		expect(summary(saved, t)).toEqual([
			'19 Stellen maskiert',
			'2 von Ihnen entschieden',
			'1 von der lokalen KI geklärt',
			'1 im Zweifel maskiert'
		]);
		const withoutDecision = entry([
			file('j-1', 'a.pdf', output({ decided_by_user: 0, masked_without_decision: 0 }))
		]);
		expect(summary(withoutDecision, t)).toEqual(['13 Stellen maskiert']);
	});

	it('shows restore only when something can be restored', () => {
		expect(returnPath(saved)).toBe('restore');
		const irreversible = (label: FileOutput['label']) =>
			entry([file('j-1', 'a.pdf', output({ label }))], 'irreversible');
		expect(returnPath(irreversible('irreversible'))).toBe('irreversible');
		expect(returnPath(irreversible('anonymous'))).toBe('level3');
		// only the highest level if every saved file supports it.
		expect(
			returnPath(
				entry(
					[
						file('j-1', 'a.pdf', output({ label: 'anonymous' })),
						file('j-2', 'b.pdf', output({ label: 'irreversible' }))
					],
					'irreversible'
				)
			)
		).toBe('irreversible');
		expect(labelFrom('anonymous')).toBe('anonymous');
	});

	it('shortens the target folder and suggests a report name without path separators', () => {
		expect(folderShort('/Users/giorgi/Dokumente/Kunden/geschützt')).toBe(
			'Dokumente › Kunden › geschützt'
		);
		expect(folderShort('C:\\Users\\Ana\\Kunden\\geschützt')).toBe('Ana › Kunden › geschützt');
		expect(reportFileName({ title: 'Rechnung/Angebot: März' }, t)).toBe(
			'Prüfbericht – Rechnung Angebot März.txt'
		);
	});

	it('lists expired files for reloading (E9)', () => {
		expect(
			expired([
				{ id: 'j-1', name: 'a.pdf', status: 'expired' },
				{ id: 'j-2', name: 'b.pdf', status: 'review' },
				{ id: 'j-3', name: 'c.pdf', status: 'unavailable' }
			])
		).toEqual([{ id: 'j-1', name: 'a.pdf' }]);
	});

	it('lists all files that need their original again (E9, draft)', () => {
		expect(
			needingOriginal([
				{ id: 'j-1', name: 'a.pdf', status: 'expired' },
				{ id: 'j-2', name: 'b.pdf', status: 'review' },
				{ id: 'j-3', name: 'c.pdf', status: 'unavailable' }
			])
		).toEqual([
			{ id: 'j-1', name: 'a.pdf' },
			{ id: 'j-3', name: 'c.pdf' }
		]);
	});
});

describe('Export history (draft AS3)', () => {
	const anonymous = {
		method: 'irreversible' as const,
		style: 'placeholder' as const,
		categories: ['finance_ids', 'names'],
		anonymize: true
	};
	const pseudonym = {
		method: 'reversible' as const,
		style: 'surrogates' as const,
		categories: ['finance_ids', 'names'],
		anonymize: false
	};
	const history = {
		...entry([file('j-1', 'Rechnung.pdf', output())], 'reversible'),
		draft_revision: 5,
		saved_revision: 4,
		exports: [
			{
				id: 'x-1',
				saved_at: '2026-11-14T09:00:00Z',
				masking: anonymous,
				directory: '/Daten/alt',
				files: [
					{
						file_id: 'j-1',
						name: 'Rechnung.pdf',
						format: 'pdf',
						path: '/Daten/alt/Rechnung_geschützt.pdf',
						output: output({ label: 'anonymous', masked: 20 })
					}
				]
			},
			{
				id: 'x-2',
				saved_at: '2026-11-14T10:00:00Z',
				masking: pseudonym,
				directory: '/Daten/neu',
				files: [
					{
						file_id: 'j-1',
						name: 'Rechnung.pdf',
						format: 'pdf',
						path: '/Daten/neu/Rechnung_geschützt (2).pdf',
						output: output({ name: 'Rechnung_geschützt (2).pdf', masked: 9 })
					}
				]
			}
		]
	} as Entry;

	it('shows the latest save on top, earlier ones below', () => {
		expect(savings(history).map((saving) => saving.id)).toEqual(['x-2', 'x-1']);
		const last = lastSaving(history);
		expect(last.folder).toBe('/Daten/neu');
		expect(savedLines(last, t, history)[0]).toMatchObject({
			number: 1,
			name: 'Rechnung_geschützt (2).pdf'
		});
		expect(summary(savings(history)[1], t)[0]).toBe('20 Stellen maskiert');
		expect(savedNum(history)).toBe(1);
	});

	it('recognizes legacy data without export history as a single save', () => {
		const alt = entry([file('j-1', 'Rechnung.pdf', output())]);
		expect(savings(alt)).toHaveLength(1);
		expect(savings(alt)[0]).toMatchObject({ id: null, folder: alt.output_dir });
		expect(savings(entry([file('j-1', 'a.pdf', null)]))).toEqual([]);
	});

	it('provides restore while a save was reversible', () => {
		expect(returnPath(history)).toBe('restore');
		const onlyAnonymous = { ...history, exports: history.exports!.slice(0, 1) } as Entry;
		expect(returnPath(onlyAnonymous)).toBe('level3');
	});

	it('tracks whether work continued after save', () => {
		expect(sinceTheSaveChanged(history)).toBe(true);
		expect(sinceTheSaveChanged({ draft_revision: 4, saved_revision: 4 })).toBe(false);
		expect(sinceTheSaveChanged({ draft_revision: 4, saved_revision: null })).toBe(false);
	});

	it('ties confirmation to method and style like the sidecar', () => {
		expect(confirmationKey(anonymous)).toBe('irreversible:placeholder:anonymous');
		expect(confirmationKey({ ...anonymous, style: 'mask', anonymize: false })).toBe(
			'irreversible:mask:-'
		);
		expect(methodText(anonymous, t)).toBe('Anonymisieren');
		expect(methodText(pseudonym, t)).toBe('Reversibel · Erfundene Namen');
	});
});

describe('entry report', () => {
	const facts: EntryReportResponse = {
		entry_id: 'e-1',
		title: 'Rechnung',
		created_at: '2026-10-23T09:00:00Z',
		saved_at: '2026-10-23T09:30:00Z',
		masking: {
			method: 'irreversible',
			style: 'placeholder',
			categories: ['finance_ids', 'names', 'locations'],
			anonymize: false
		},
		protection_level: 2,
		app_version: '0.1.0',
		profile_version: 'grundprofil@1.0.0',
		files: [
			{
				id: 'j-1',
				name: 'Rechnung_geschützt.pdf',
				format: 'pdf',
				pages: 2,
				ocr_used: false,
				source_changed: true,
				output: output({
					label: 'irreversible',
					masked: 4,
					masked_by_category: { names: 3, locations: 1 },
					rendered_pages: 2,
					text_extraction_checked: true,
					sha256: 'a'.repeat(64),
					audit_id: 7,
					residual_risk: 'warning',
					residual_classes: ['date_time', 'location_region'],
					anonymous_blockers: ['not_requested', 'residual_risk']
				})
			},
			{
				id: 'j-2',
				name: 'Notiz.txt',
				format: 'txt',
				pages: null,
				ocr_used: false,
				source_changed: false,
				output: output({ status: 'unchanged', label: null, masked: 0 })
			}
		],
		totals: output({
			masked: 4,
			masked_by_category: { names: 3, locations: 1 },
			decided_by_user: 2,
			masked_without_decision: 1,
			residual_risk: 'warning',
			residual_classes: ['date_time', 'location_region']
		})
	};

	it('includes technical term, counters, origin and review data in UI language', () => {
		const text = entryReportAsText(entryReport(facts, t, (iso) => iso.slice(0, 10)));
		expect(text).toContain('Prüfbericht – Rechnung');
		expect(text).toContain('Methode: Irreversibel');
		expect(text).toContain('Ersatz-Stil: Platzhalter');
		expect(text).toContain('Kategorien: Konto-, Ausweis- & Zugangsdaten, Namen, Orte');
		expect(text).toContain('Maskierte Stellen: 4');
		expect(text).toContain('  Namen: 3');
		expect(text).toContain('Beim Speichern ohne Entscheidung maskiert: 1');
		expect(text).toContain(
			'Irreversibel maskiert (Schwärzung – noch nicht anonym) · 4 Stellen maskiert · PDF-Prüfung: 2 Seiten gerendert'
		);
		expect(text).toContain('Befund – Datums- und Zeitangaben, Orts- und Regionsangaben');
		expect(text).toContain('Auditeintrag: #7');
		expect(text).toContain('2. Notiz.txt: keine Stelle zu maskieren – keine Kopie');
		expect(text).toContain('neu eingelesen und hatte sich seit dem ersten Einlesen geändert');
		expect(text).toContain('Die Originale wurden nicht verändert');
	});

	it('describes what happened to scans and images', () => {
		expect(outputNotices(output({ scan_text_layer: true }), t)).toEqual([
			'Scan übermalt, mit Textebene'
		]);
		expect(outputNotices(output({ from_scan: true, readback_failed: true }), t)).toEqual([
			'Als Text gespeichert – Code im Bild nicht sicher lesbar'
		]);
		// Older entries only have the legacy label "Text from image".
		expect(outputNotices(output({ from_scan: true }), t)).toEqual(['Text aus dem Bild']);
		expect(outputNotices(output(), t)).toEqual([]);
	});
});
