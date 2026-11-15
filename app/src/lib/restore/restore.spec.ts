import { describe, expect, it } from 'vitest';
import { translate } from '#lib/i18n/translate.js';
import type { RestoreCounts, RestoreFileResponse, RestoredSpan } from '#lib/sidecar/api.gen.js';
import { outputSegments, fileError, fileMeta, errorCode, withAliases, count } from './restore.js';

const t = translate.bind(null, 'de');

function span(start: number, end: number, part: Partial<RestoredSpan> = {}): RestoredSpan {
	return {
		start,
		end,
		status: 'restored',
		kind: 'code',
		entity_type: 'PERSON',
		tolerant: false,
		origin: 'here',
		entry_id: 'e-1',
		...part
	};
}

function counts(part: Partial<RestoreCounts> = {}): RestoreCounts {
	return {
		restored: 0,
		here: 0,
		other: 0,
		earlier: 0,
		unknown: 0,
		unknown_unique: 0,
		tolerant: 0,
		...part
	};
}

describe('Output as segments', () => {
	it('distinguishes here, from other entries and unknown', () => {
		const text = 'Frau Hofer, Kaution Wimmer, PS: <<PERSON_8e21d7f0>>.';
		const segments = outputSegments(text, [
			span(5, 10),
			span(20, 26, { origin: 'entry', entry_id: 'e-2' }),
			span(32, 51, { status: 'unknown', origin: null, entry_id: null })
		]);

		expect(segments.map((segment) => [segment.kind, segment.text])).toEqual([
			['plain', 'Frau '],
			['restored', 'Hofer'],
			['plain', ', Kaution '],
			['restored-other', 'Wimmer'],
			['plain', ', PS: '],
			['unknown-code', '<<PERSON_8e21d7f0>>'],
			['plain', '.']
		]);
		// The concatenated result matches the sidecar text exactly.
		expect(segments.map((segment) => segment.text).join('')).toBe(text);
	});

	it('counts in UTF-16 like the sidecar and skips overlapping or too long spans', () => {
		const text = '😀 Hofer';
		const segments = outputSegments(text, [span(3, 8), span(5, 9), span(7, 40)]);
		expect(segments.map((segment) => segment.text)).toEqual(['😀 ', 'Hofer']);
	});
});

describe('Counting', () => {
	it('labels the origin as on the board: here, from …, unknown', () => {
		const status = count(
			counts({ restored: 6, here: 5, other: 1, unknown: 1, unknown_unique: 1 }),
			[{ entry_id: 'e-2', title: 'Mietvertrag (Scan)', count: 1 }],
			t
		);
		expect(status.replaced).toBe(6);
		expect(status.origin).toEqual([
			{ kind: 'here', text: '5 von hier' },
			{ kind: 'other', text: '1 aus »Mietvertrag (Scan)«' }
		]);
		expect(status.unknown).toBe(1);
		expect(status.noCode).toBe(false);
		expect(t('restore.codes', { n: status.replaced })).toBe('6 Codes');
	});

	it('aggregates later and earlier entries', () => {
		const status = count(
			counts({ restored: 9, other: 6, earlier: 3, tolerant: 2 }),
			[
				{ entry_id: 'a', title: 'Angebot', count: 3 },
				{ entry_id: 'b', title: 'Brief', count: 2 },
				{ entry_id: 'c', title: 'Notiz', count: 1 }
			],
			t
		);
		expect(status.origin.map((part) => part.text)).toEqual([
			'3 aus »Angebot«',
			'2 aus »Brief«',
			'1 aus weiteren Einträgen',
			'3 aus früheren Einträgen'
		]);
		expect(status.changed).toBe(2);
	});

	it('reports when no code is present in the response', () => {
		expect(count(counts(), [], t).noCode).toBe(true);
		expect(count(counts({ unknown: 1 }), [], t).noCode).toBe(false);
	});
});

describe('File', () => {
	it('describes the AI file with size and found codes', () => {
		const file = {
			source: { name: 'Zahlungserinnerung_Entwurf.docx', format: 'docx', size_bytes: 24 * 1024 },
			counts: counts({ restored: 7, unknown: 1 })
		} as RestoreFileResponse;
		expect(fileMeta(file, t)).toBe('von der KI · 24 KB · 8 Codes gefunden');
	});

	it('translates error codes from the shell and sidecar into sentences', () => {
		expect(fileError('same_as_source', t)).toContain('nicht überschrieben');
		expect(fileError('unsupported_format', t)).toContain('Word, Excel, Markdown und Text');
		expect(fileError('unbekannt', t)).toBe('Die Datei ließ sich nicht zurückübersetzen.');
		expect(errorCode({ code: 'expired', message: '' })).toBe('expired');
		expect(errorCode('Fehler')).toBeUndefined();
	});

	it('includes invented names only when the entry used them', () => {
		const masking = { method: 'reversible' as const, categories: [], anonymize: false };
		expect(withAliases({ masking: { ...masking, style: 'surrogates' } })).toBe(true);
		expect(withAliases({ masking: { ...masking, style: 'codes' } })).toBe(false);
		expect(withAliases(null)).toBe(false);
	});
});
