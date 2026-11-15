import { describe, expect, it } from 'vitest';
import { translate } from '#lib/i18n/translate.js';
import type { CategoryId } from '#lib/masking/catalog.js';
import {
	EXAMPLE_ORIGINAL,
	SECTIONS,
	exampleSegments,
	diagnosticsText,
	errorSentence,
	size,
	istSection,
	listenState,
	backupName,
	testSegments
} from './dialog.js';

const t = (key: Parameters<typeof translate>[1], values?: Parameters<typeof translate>[2]) =>
	translate('de', key, values);

const STANDARD = new Set<CategoryId>(['finance_ids', 'names', 'addresses', 'contact', 'numbers']);
const ALL = new Set<CategoryId>([
	...STANDARD,
	'web',
	'organizations',
	'locations',
	'dates',
	'other_indirect'
]);

describe('Sections', () => {
	it('knows the nine sections of the board', () => {
		expect(SECTIONS).toHaveLength(9);
		expect(istSection('table')).toBe(true);
		expect(istSection('profile')).toBe(false);
		expect(istSection(undefined)).toBe(false);
	});
});

describe('Example sentence', () => {
	const text = (segments: ReturnType<typeof exampleSegments>) =>
		segments.map((segment) => segment.text).join('');

	it('masks only the name with the default – as code', () => {
		const segments = exampleSegments({
			method: 'reversible',
			style: 'codes',
			categories: STANDARD
		});
		expect(text(segments)).toBe(
			'Frau <<PERSON_ba903855>> aus Wien, Kundin von Holzbau Gruber, hat am 3. April 2026 bezahlt.'
		);
		expect(segments.filter((segment) => segment.kind === 'code')).toHaveLength(1);
		expect(segments.find((segment) => segment.kind === 'code')?.original).toBe('Sabine Hofer');
	});

	it('follows style, categories and app language', () => {
		expect(
			text(exampleSegments({ method: 'irreversible', style: 'placeholder', categories: ALL }))
		).toBe('Frau [Name] aus [Ort], Kundin von [Firma], hat am [Datum] bezahlt.');
		expect(
			text(exampleSegments({ method: 'irreversible', style: 'placeholder', categories: ALL }, 'en'))
		).toBe('Frau [Name] aus [Place], Kundin von [Company], hat am [Date] bezahlt.');
		expect(
			text(exampleSegments({ method: 'reversible', style: 'surrogates', categories: STANDARD }))
		).toContain('Frau Petra Lang aus Wien');
		expect(
			text(exampleSegments({ method: 'irreversible', style: 'mask', categories: STANDARD }))
		).toContain('Frau •••••••••••• aus');
		expect(
			text(exampleSegments({ method: 'irreversible', style: 'remove', categories: STANDARD }))
		).toBe('Frau  aus Wien, Kundin von Holzbau Gruber, hat am 3. April 2026 bezahlt.');
	});

	it('without selection the sentence stays unchanged and is a single segment', () => {
		const segments = exampleSegments({
			method: 'reversible',
			style: 'codes',
			categories: new Set()
		});
		expect(segments).toEqual([{ kind: 'plain', text: EXAMPLE_ORIGINAL }]);
	});
});

describe('Try out', () => {
	const sentence = 'Polizze POL-4471920 😀 im Projekt Falke.';
	const start = sentence.indexOf('POL');
	const projekt = sentence.indexOf('Projekt');

	it('places sidecar placeholders at the UTF-16 positions', () => {
		const segments = testSegments(sentence, [
			{
				start: projekt,
				end: projekt + 13,
				entity_type: 'OTHER',
				source: 'term',
				label: '[Angabe]'
			},
			{
				start,
				end: start + 11,
				entity_type: 'CUSTOMER_NUMBER',
				source: 'pattern',
				label: '[Kundennummer]'
			}
		]);
		expect(segments.map((segment) => segment.text).join('')).toBe(
			'Polizze [Kundennummer] 😀 im [Angabe].'
		);
		expect(segments.filter((segment) => segment.kind === 'pattern-hit')).toHaveLength(2);
		expect(segments[1].original).toBe('POL-4471920');
	});

	it('leaves unsuitable or overlapping hits unmarked', () => {
		const segments = testSegments(sentence, [
			{ start, end: start + 11, entity_type: 'OTHER', source: 'pattern', label: '[A]' },
			{ start: start + 2, end: start + 5, entity_type: 'OTHER', source: 'pattern', label: '[B]' },
			{ start: 0, end: 999, entity_type: 'OTHER', source: 'pattern', label: '[C]' }
		]);
		expect(segments.map((segment) => segment.text).join('')).toBe(
			'Polizze [A] 😀 im Projekt Falke.'
		);
	});
});

describe('State of a name list', () => {
	const now = new Date(2026, 9, 24, 14, 30);

	it('today, yesterday, n days ago', () => {
		expect(listenState(new Date(2026, 9, 24, 3, 0).toISOString(), now, t)).toBe(
			'Stand heute, 03:00'
		);
		expect(listenState(new Date(2026, 9, 23, 22, 15).toISOString(), now, t)).toBe(
			'Stand gestern, 22:15'
		);
		expect(listenState(new Date(2026, 9, 15, 9, 0).toISOString(), now, t)).toBe(
			'Stand vor 9 Tagen'
		);
		expect(listenState('kein Datum', now, t)).toBe('');
	});
});

describe('Errors and help messages', () => {
	it('translates codes from sidecar and shell, generic unknown', () => {
		expect(errorSentence('wrong_password', t)).toContain('Passwort passt nicht');
		expect(errorSentence('pattern_broad', t)).toContain('fast jeden Text');
		expect(errorSentence('list_format', t)).toContain('Namensspalte');
		expect(errorSentence('irgendwas', t)).toBe(t('settings.error.general'));
		expect(errorSentence(undefined, t)).toBe(t('settings.error.general'));
	});

	it('names the backup with date and decimal size', () => {
		expect(backupName(new Date(2026, 9, 4))).toBe('unname-tabelle-2026-10-04.unname-sicherung');
		expect(size(715_000_000)).toBe('715 MB');
		expect(size(1_250_000_000)).toBe('1,3 GB');
		expect(size(2_500, 'en-GB')).toBe('2.5 kB');
	});

	it('diagnostic report: runtime data, no content', () => {
		const text = diagnosticsText(
			{
				status: 'Version 0.1.0, PID 42, Port 5000, nur über 127.0.0.1 erreichbar.',
				restarts: 1,
				version: '0.1.0',
				log: 2,
				python: '3.13.1',
				platform: 'Darwin arm64',
				detection: 'gliner',
				device: 'mps',
				pakete: { 'presidio-analyzer': '2.2.360', gliner: null },
				entries: [
					{
						timestamp: '2026-10-24T10:00:00Z',
						category: 'model_load',
						severity: 'warning',
						source: 'sidecar',
						message: 'GLiNER war lokal nicht verfuegbar; spaCy-Fallback ist aktiv.'
					}
				]
			},
			new Date('2026-10-24T12:00:00Z')
		);
		expect(text).toContain('Erstellt: 2026-10-24T12:00:00.000Z');
		expect(text).toContain('Sidecar: 0.1.0 (Protokoll 2)');
		expect(text).toContain('  presidio-analyzer 2.2.360');
		expect(text).not.toContain('gliner null');
		expect(text).toContain('[warning] model_load (Sidecar): GLiNER war lokal');
	});
});
