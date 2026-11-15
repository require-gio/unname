import { describe, expect, it } from 'vitest';
import { translate } from '#lib/i18n/translate.js';
import { segmentText } from '#lib/ui/segments.js';
import type { AppliedFindingOut, ApplyResult } from '#lib/sidecar/api.gen.js';
import { outputSegments, outputTitle, outputCount } from './output.js';

const t = translate.bind(null, 'de');
const ORIGINAL = 'Hallo Frau Hofer, ruf 0664 123 an.';

function place(
	part: Partial<AppliedFindingOut> &
		Pick<AppliedFindingOut, 'original_start' | 'original_end' | 'output_start' | 'output_end'>
): AppliedFindingOut {
	return {
		finding_id: `f${part.original_start}`,
		kind: 'confirmed',
		entity_type: 'PERSON',
		category: 'names',
		source: 'ner',
		mode: 'token_deterministic',
		reversibility: 'vault',
		...part
	};
}

function makeResult(text: string, places: AppliedFindingOut[], part: Partial<ApplyResult> = {}) {
	return {
		text,
		masking: {
			method: 'reversible',
			style: 'codes',
			categories: ['finance_ids'],
			anonymize: false
		},
		labels: {
			label: 'reversible',
			contains_surrogates: false,
			anonymous_blockers: ['not_requested'],
			protection_level: 2,
			kept_here_count: 0,
			masked_without_decision: 0
		},
		applied_findings: places,
		applied_by_category: {},
		residual_risk: { status: 'clear', heuristic: true, threshold: 2, classes: [], counts: {} },
		facts: {},
		report: {},
		...part
	} satisfies ApplyResult;
}

describe('Output segments', () => {
	const code = '<<PERSON_51c2e8d4>>';
	const text = `Hallo Frau ${code}, ruf [Telefon] an.`;
	const phoneStart = text.indexOf('[Telefon]');
	const result = makeResult(text, [
		place({
			original_start: 22,
			original_end: 30,
			output_start: phoneStart,
			output_end: phoneStart + '[Telefon]'.length,
			entity_type: 'PHONE_NUMBER',
			mode: 'placeholder'
		}),
		place({ original_start: 11, original_end: 16, output_start: 11, output_end: 11 + code.length })
	]);

	it('together form the exact sidecar text', () => {
		expect(segmentText(outputSegments(result, ORIGINAL))).toBe(text);
	});

	it('shows codes as code and placeholders as placeholder, with original', () => {
		const marked = outputSegments(result, ORIGINAL).filter((s) => s.kind !== 'plain');

		expect(marked.map((s) => [s.kind, s.text, s.original])).toEqual([
			['code', code, 'Hofer'],
			['placeholder', '[Telefon]', '0664 123']
		]);
	});

	it('leaves an invalid or overlapping segment unmarked instead of incorrect', () => {
		const kaputt = makeResult('kurz', [
			place({ original_start: 0, original_end: 2, output_start: 0, output_end: 3 }),
			place({ original_start: 1, original_end: 2, output_start: 1, output_end: 2 }),
			place({ original_start: 3, original_end: 9, output_start: 2, output_end: 40 })
		]);
		const segments = outputSegments(kaputt, 'kurz und gut');

		expect(segmentText(segments)).toBe('kurz');
		expect(segments.filter((s) => s.kind !== 'plain')).toHaveLength(1);
	});

	it('does not create an empty segment for removed places', () => {
		const removed = makeResult('Hallo Frau , ruf an.', [
			place({
				original_start: 11,
				original_end: 16,
				output_start: 11,
				output_end: 11,
				mode: 'remove'
			})
		]);

		expect(outputSegments(removed, ORIGINAL).every((s) => s.kind === 'plain')).toBe(true);
	});
});

describe('Title and count', () => {
	it('follows the result label, overriding the method when provided', () => {
		expect(outputTitle('reversible')).toBe('text.output.reversible');
		expect(outputTitle('irreversible')).toBe('text.output.irreversible');
		const irreversible = makeResult('x', []);
		irreversible.labels.label = 'irreversible';
		expect(outputTitle('reversible', irreversible)).toBe('text.output.irreversible');
		irreversible.labels.label = 'anonymous';
		expect(outputTitle('irreversible', irreversible)).toBe('label.level3');
	});

	it('counts masked entries and conservatively masked questions', () => {
		const result = makeResult('x', [
			place({ original_start: 0, original_end: 1, output_start: 0, output_end: 1 })
		]);
		expect(outputCount(result, t)).toBe('1 Stelle maskiert · keine Fragen');
		result.labels.masked_without_decision = 2;
		expect(outputCount(result, t)).toBe(
			'1 Stelle maskiert · 2 unsichere Stellen vorsichtshalber maskiert'
		);
	});
});
