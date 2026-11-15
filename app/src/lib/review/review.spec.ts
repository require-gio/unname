import { describe, expect, it, vi } from 'vitest';
import { translate } from '#lib/i18n/translate.js';
import { segmentText, type Segment } from '#lib/ui/segments.js';
import type { AppliedFindingOut, FindingOut } from '#lib/sidecar/api.gen.js';
import { sourceSection } from './selection.js';
import {
	previousFound,
	fileMeta,
	detectionFinding,
	protectedName,
	maskingLine
} from './detection.js';
import { codeMenu } from './menu.js';
import {
	resultSegments,
	inParagraphs,
	maskedPlaces,
	withSelection,
	maskedShare,
	originalSegments,
	percent,
	type SegmentInput
} from './segments.js';
import {
	EMPTY_STATE,
	asReview,
	fromSaved,
	touchedMatch,
	decide,
	questions,
	correctBoundary,
	removeManualEntry,
	aiView,
	aiSuggestion,
	addLater,
	nextOpen,
	openQuestions,
	addLaterOccurrence,
	undo,
	type ReviewState
} from './state.js';
import { ApplyQueue } from './queue.js';

const t = (key: Parameters<typeof translate>[1], values?: Record<string, string | number>) =>
	translate('de', key, values);

const TEXT =
	'Frau Sabine Hofer\nMozartgasse 7\n\n' +
	'Wie mit Herrn Markus Leitner besprochen, liefern die Gebrüdern Wachtelhuber ein Muster nach Linz.';

function finding(
	id: string,
	value: string,
	type: string,
	category: string,
	question?: FindingOut['question']
): FindingOut {
	const start = TEXT.indexOf(value);
	if (start < 0) throw new Error(value);
	return {
		id,
		kind: question?.kind === 'suspicion' ? 'suspicious' : 'confirmed',
		type: type,
		category: category,
		score: question ? 0.5 : 0.97,
		start,
		end: start + value.length,
		text: value,
		context: { before: '', match: value, after: '' },
		source: question?.kind === 'suspicion' ? 'suspicion' : 'ner',
		detector: 'test',
		review_required: question?.kind === 'uncertain',
		auto_accepted: !question,
		question: question ?? null
	};
}

const HOFER = finding('f_hofer', 'Sabine Hofer', 'PERSON', 'names');
const ADDRESS = finding('f_adresse', 'Mozartgasse 7', 'ADDRESS', 'addresses');
const LEITNER = finding('f_leitner', 'Markus Leitner', 'PERSON', 'names');
const WACHTEL = finding('f_wachtel', 'Gebrüdern Wachtelhuber', 'PERSON', 'names', {
	kind: 'suspicion',
	n: 1
});
const PATTERN = finding('f_sample', 'Muster', 'PERSON', 'names', { kind: 'uncertain', n: 2 });
const LINZ = finding('f_linz', 'Linz', 'LOCATION', 'locations');
const FINDINGS = [HOFER, ADDRESS, LEITNER, WACHTEL, PATTERN, LINZ];

const STANDARD = new Set(['finance_ids', 'names', 'addresses', 'contact', 'numbers']);
const active = (category: string) => STANDARD.has(category);

/** Build an Apply response like the sidecar: replace the specified spans from the end. */
function apply(
	replacements: { id: string; start: number; end: number; type: string; text: string }[]
): { text: string; applied_findings: AppliedFindingOut[] } {
	const sorted = [...replacements].sort((a, b) => a.start - b.start);
	let output = '';
	let cursor = 0;
	const applied: AppliedFindingOut[] = [];
	for (const item of sorted) {
		output += TEXT.slice(cursor, item.start);
		const start = output.length;
		output += item.text;
		applied.push({
			finding_id: item.id,
			kind: 'confirmed',
			entity_type: item.type,
			category: null,
			source: 'ner',
			mode: item.text.startsWith('<<') ? 'token_deterministic' : 'placeholder',
			reversibility: 'vault',
			original_start: item.start,
			original_end: item.end,
			output_start: start,
			output_end: output.length
		});
		cursor = item.end;
	}
	return { text: output + TEXT.slice(cursor), applied_findings: applied };
}

const code = (f: FindingOut, text = `<<${f.type}_${f.id.slice(2, 10).padEnd(8, '0')}>>`) => ({
	id: f.id,
	start: f.start,
	end: f.end,
	type: f.type,
	text
});

function input(status: ReviewState, result: SegmentInput['result']): SegmentInput {
	return {
		text: TEXT,
		findings: FINDINGS,
		status,
		result,
		questions: questions(FINDINGS, status, active),
		categoryActive: active
	};
}

describe('Review state', () => {
	it('restores saved state and discards foreign finding IDs', () => {
		const status = fromSaved(
			{
				decisions: [
					{ finding_id: 'f_sample', action: 'reject', by: 'user' },
					{ finding_id: 'f_alt', action: 'accept' }
				],
				manual: [
					{ entity_type: 'PERSON', start: 90, end: 94 },
					{ entity_type: 'PERSON', start: 1, end: 2, corrects_finding_id: 'f_alt' }
				]
			},
			FINDINGS
		);
		expect(status.decisions).toEqual({ f_sample: { action: 'reject', by: 'user' } });
		expect(status.manual).toEqual([{ id: 'm_90_94_PERSON', type: 'PERSON', start: 90, end: 94 }]);
	});

	it('sends decisions with source and masks when uncertain', () => {
		const status = decide(EMPTY_STATE, 'f_wachtel', 'accept', 'ai-suggestion');
		expect(asReview(status)).toEqual({
			decisions: [{ finding_id: 'f_wachtel', action: 'accept', by: 'ai-suggestion' }],
			manual: [],
			unresolved: 'mask'
		});
	});

	it('adds an entry but never over an occupied span', () => {
		const lena = TEXT.indexOf('nach Linz') + 'nach '.length;
		const toMany = addLater(EMPTY_STATE, FINDINGS, {
			type: 'LOCATION',
			start: lena - 2,
			end: lena + 2
		});
		expect(toMany).toMatchObject({ ok: false, reason: 'addendum.overlaps' });

		const herrn = TEXT.indexOf('Herrn');
		const ok = addLater(EMPTY_STATE, FINDINGS, {
			type: 'OTHER',
			start: herrn,
			end: herrn + 5
		});
		expect(ok.ok).toBe(true);
		expect(ok.status.manual).toHaveLength(1);
		expect(asReview(ok.status).manual).toEqual([
			{ entity_type: 'OTHER', start: herrn, end: herrn + 5 }
		]);
	});

	it('skips already occupied spans when adding all occurrences', () => {
		const herrn = TEXT.indexOf('Herrn');
		const { status, fresh } = addLaterOccurrence(EMPTY_STATE, FINDINGS, 'OTHER', [
			{ start: herrn, end: herrn + 5 },
			{ start: HOFER.start, end: HOFER.end }
		]);
		expect(fresh).toBe(1);
		expect(status.manual.map((entry) => entry.start)).toEqual([herrn]);
	});

	it('corrects boundaries as a pair and then restores them', () => {
		const frau = TEXT.indexOf('Frau');
		const result = correctBoundary(EMPTY_STATE, FINDINGS, 'f_hofer', {
			start: frau,
			end: HOFER.end
		});
		expect(result.ok).toBe(true);
		expect(result.status.decisions.f_hofer).toEqual({ action: 'reject', by: 'user' });
		expect(asReview(result.status).manual).toEqual([
			{ entity_type: 'PERSON', start: frau, end: HOFER.end, corrects_finding_id: 'f_hofer' }
		]);
		// Re-apply replaces the previous correction instead of stacking.
		const still = correctBoundary(result.status, FINDINGS, 'f_hofer', {
			start: frau + 1,
			end: HOFER.end
		});
		expect(still.status.manual).toHaveLength(1);

		const back = removeManualEntry(still.status, still.status.manual[0].id);
		expect(back).toEqual(EMPTY_STATE);
		expect(undo(still.status, 'f_hofer')).toEqual(EMPTY_STATE);
	});

	it('rejects a correction that spans two places and one that is unchanged', () => {
		const aboutTwo = correctBoundary(EMPTY_STATE, FINDINGS, 'f_hofer', {
			start: HOFER.start,
			end: ADDRESS.end
		});
		expect(aboutTwo).toMatchObject({ ok: false, reason: 'addendum.several' });
		const same = correctBoundary(EMPTY_STATE, FINDINGS, 'f_hofer', HOFER);
		expect(same).toMatchObject({ ok: false, reason: 'addendum.unchanged' });
	});

	it('lists the findings touched by a marking', () => {
		const touched = touchedMatch(EMPTY_STATE, FINDINGS, {
			start: HOFER.start - 5,
			end: HOFER.start + 3
		});
		expect(touched.findings.map((f) => f.id)).toEqual(['f_hofer']);
	});
});

describe('Questions', () => {
	it('only asks about masked categories, stable numbering', () => {
		const place = finding('f_ort', 'Linz', 'LOCATION', 'locations', { kind: 'suspicion', n: 3 });
		const list = questions([...FINDINGS, place], EMPTY_STATE, active);
		expect(list.map((question) => [question.finding.id, question.number, question.kind])).toEqual([
			['f_wachtel', 1, 'suspicion'],
			['f_sample', 2, 'uncertain']
		]);
		const entschieden = questions(FINDINGS, decide(EMPTY_STATE, 'f_wachtel', 'reject'), active);
		expect(openQuestions(entschieden).map((question) => question.number)).toEqual([2]);
	});

	it('jumps to next open question and loops', () => {
		const list = questions(FINDINGS, EMPTY_STATE, active);
		expect(nextOpen(list, null)?.finding.id).toBe('f_wachtel');
		expect(nextOpen(list, 'f_wachtel')?.finding.id).toBe('f_sample');
		expect(nextOpen(list, 'f_sample')?.finding.id).toBe('f_wachtel');
		const a = questions(FINDINGS, decide(EMPTY_STATE, 'f_wachtel', 'accept'), active);
		expect(nextOpen(a, 'f_wachtel')?.finding.id).toBe('f_sample');
		const no = questions(
			FINDINGS,
			decide(decide(EMPTY_STATE, 'f_wachtel', 'accept'), 'f_sample', 'reject'),
			active
		);
		expect(nextOpen(no, null)).toBeNull();
	});
});

describe('Second opinion', () => {
	const assessment = (
		suggestion: 'accept' | 'reject' | null,
		decided: boolean,
		label: NonNullable<FindingOut['assessment']>['label'] = 'person',
		protect = 0.97
	): FindingOut['assessment'] => ({
		label,
		confidence: label === 'term' ? 1 - protect : protect,
		protect,
		suggestion,
		decided,
		model: 'test'
	});
	const WACHTEL_AI = { ...WACHTEL, assessment: assessment('accept', true) };
	const PATTERN_AI = { ...PATTERN, assessment: assessment('reject', false, 'term', 0.04) };

	it('starts without saved state using AI decision', () => {
		const status = fromSaved(null, [HOFER, WACHTEL_AI, PATTERN_AI]);
		expect(status.decisions).toEqual({
			f_wachtel: { action: 'accept', by: 'ai-suggestion' }
		});
		// A saved state overrides everything – even if AI was reversed.
		const umgekehrt = fromSaved(
			{ decisions: [{ finding_id: 'f_wachtel', action: 'reject', by: 'user' }], manual: [] },
			[WACHTEL_AI]
		);
		expect(umgekehrt.decisions).toEqual({ f_wachtel: { action: 'reject', by: 'user' } });
		expect(fromSaved(null, FINDINGS)).toBe(EMPTY_STATE);
	});

	it('reads suggestion, safety and rationale from assessment', () => {
		expect(aiSuggestion(WACHTEL_AI)).toBe('accept');
		expect(aiSuggestion(PATTERN_AI)).toBe('reject');
		expect(aiSuggestion(WACHTEL)).toBeUndefined();
		expect(aiSuggestion({ ...PATTERN, assessment: assessment(null, false) })).toBeUndefined();

		const term = aiView(PATTERN_AI)!;
		expect(term.suggestion).toBe('reject');
		expect(term.safety).toBeCloseTo(0.96);
		expect(t(term.sentence)).toBe('Wahrscheinlich ein Begriff, kein Name');
		expect(t('ai.rationale', { short: t(term.short), percent: 96 })).toBe(
			'Begriff, kein Name · 96 %'
		);
		const uncertain = aiView({
			...PATTERN,
			assessment: assessment(null, false, 'location', 0.55)
		})!;
		expect(uncertain.suggestion).toBeUndefined();
		expect(t(uncertain.sentence)).toBe('Unsicher – bitte selbst entscheiden');
		expect(aiView(HOFER)).toBeUndefined();
	});

	it('shows AI decisions as decisions with origin', () => {
		const status = fromSaved(null, [WACHTEL_AI, PATTERN_AI]);
		const list = questions([WACHTEL_AI, PATTERN_AI], status, active);
		expect(list.map((question) => question.decision?.by)).toEqual(['ai-suggestion', undefined]);
		expect(asReview(status).decisions).toEqual([
			{ finding_id: 'f_wachtel', action: 'accept', by: 'ai-suggestion' }
		]);
	});
});

describe('Segments of the review view', () => {
	// In doubt masked: both open questions also appear in the answer.
	const result = apply([code(HOFER), code(ADDRESS), code(LEITNER), code(WACHTEL), code(PATTERN)]);

	it('shows codes in result, open questions in original and retained categories', () => {
		const segments = resultSegments(input(EMPTY_STATE, result));
		const kinds = segments.filter((s) => s.kind !== 'plain').map((s) => [s.kind, s.text]);
		expect(kinds).toEqual([
			['code', '<<PERSON_hofer000>>'],
			['code', '<<ADDRESS_adresse0>>'],
			['code', '<<PERSON_leitner0>>'],
			['question', 'Gebrüdern Wachtelhuber'],
			['question', 'Muster'],
			['kept-category', 'Linz']
		]);
		const question = segments.find((s) => s.id === 'f_wachtel')!;
		expect(question).toMatchObject({ number: 1, operable: false });
		const hofer = segments.find((s) => s.id === 'f_hofer')!;
		expect(hofer).toMatchObject({ original: 'Sabine Hofer', source: { start: HOFER.start } });
		// Except at the open questions the exact saved text is shown.
		const withoutQuestions = segments.map((s) =>
			s.kind === 'question' ? result.text.slice(0, 0) : s.text
		);
		expect(withoutQuestions.join('')).toBe(
			result.text.replace('<<PERSON_wachtel0>>', '').replace('<<PERSON_sample00>>', '')
		);
	});

	it('shows "Keep only here" and decided questions', () => {
		const status = decide(decide(EMPTY_STATE, 'f_leitner', 'reject'), 'f_wachtel', 'reject');
		const answer = apply([code(HOFER), code(ADDRESS), code(PATTERN)]);
		const segments = resultSegments(input(status, answer));
		expect(segments.find((s) => s.id === 'f_leitner')).toMatchObject({
			kind: 'kept-here',
			text: 'Markus Leitner',
			operable: true
		});
		expect(segments.find((s) => s.id === 'f_wachtel')?.kind).toBe('kept-here');
		expect(segments.find((s) => s.id === 'f_sample')?.kind).toBe('question');
		expect(segmentText(segments)).toBe(answer.text.replace('<<PERSON_sample00>>', 'Muster'));
	});

	it('assigns later-added entries to their range', () => {
		const herrn = TEXT.indexOf('Herrn');
		const status = addLater(EMPTY_STATE, FINDINGS, {
			type: 'OTHER',
			start: herrn,
			end: herrn + 5
		}).status;
		const answer = apply([
			code(HOFER),
			{ id: 'f_sidecar_eigen', start: herrn, end: herrn + 5, type: 'OTHER', text: '[Angabe]' }
		]);
		const segments = resultSegments(input(status, answer));
		expect(segments.find((s) => s.text === '[Angabe]')).toMatchObject({
			kind: 'placeholder',
			id: status.manual[0].id,
			manual: true,
			category: 'other_indirect'
		});
	});

	it('assigns additional occurrences of a later-added value to its marking', () => {
		// The sidecar replaces every occurrence of a manually marked value;
		// marking "Do not mask" on one of them removes that marking.
		const first = TEXT.indexOf('Wie') + 1;
		const weiteres = TEXT.indexOf('liefern') + 1;
		expect(TEXT.slice(weiteres, weiteres + 2)).toBe(TEXT.slice(first, first + 2));
		const status = addLater(EMPTY_STATE, FINDINGS, {
			type: 'OTHER',
			start: first,
			end: first + 2
		}).status;
		const answer = apply([
			{ id: 'f_eigen', start: first, end: first + 2, type: 'OTHER', text: '[A]' },
			{ id: 'f_wiederholung', start: weiteres, end: weiteres + 2, type: 'OTHER', text: '[B]' }
		]);
		answer.applied_findings[1].source = 'manual';
		const segments = resultSegments(input(status, answer));
		expect(segments.find((s) => s.text === '[A]')).toMatchObject({
			id: status.manual[0].id,
			hand: status.manual[0].id,
			manual: true
		});
		expect(segments.find((s) => s.text === '[B]')).toMatchObject({
			id: 'f_wiederholung',
			hand: status.manual[0].id,
			manual: true
		});
		// IDs are keys for rendering: duplicates would crash the view (regression "500 Internal Error" after "only this entry").
		const ids = segments.flatMap((s) => (s.id ? [s.id] : []));
		expect(new Set(ids).size).toBe(ids.length);
		// A foreign match with the same value must not be treated as manual.
		answer.applied_findings[1].source = 'ner';
		const fremd = resultSegments(input(status, answer));
		expect(fremd.find((s) => s.text === '[B]')?.hand).toBeUndefined();
	});

	it('marks in the original what will be masked, and shows prediction without answer', () => {
		const segments = originalSegments(input(EMPTY_STATE, null));
		expect(segmentText(segments)).toBe(TEXT);
		expect(segments.find((s) => s.id === 'f_hofer')?.kind).toBe('masked-original');
		expect(segments.find((s) => s.id === 'f_sample')).toMatchObject({
			kind: 'question',
			number: 2
		});
		expect(segments.find((s) => s.id === 'f_linz')?.kind).toBe('kept-category');
		for (const segment of segments) {
			expect(TEXT.slice(segment.source!.start, segment.source!.end)).toBe(segment.text);
		}
	});

	it('splits into paragraphs according to sidecar blocks', () => {
		const second = TEXT.indexOf('Wie');
		const blocks = [
			{ start: 0, end: second - 2 },
			{ start: second, end: TEXT.length }
		];
		const paragraphs = inParagraphs(resultSegments(input(EMPTY_STATE, result)), blocks);
		expect(paragraphs).toHaveLength(2);
		expect(segmentText(paragraphs[0])).toBe('Frau <<PERSON_hofer000>>\n<<ADDRESS_adresse0>>');
		expect(segmentText(paragraphs[1]).startsWith('Wie mit Herrn <<PERSON_leitner0>>')).toBe(true);
		expect(inParagraphs([], [])).toEqual([[]]);
	});

	it('counts masked places without open questions and calculates share of text', () => {
		const list = questions(FINDINGS, EMPTY_STATE, active);
		expect(maskedPlaces(result, list)).toBe(3);
		const share = maskedShare(result, TEXT.length);
		const erwartet =
			['Sabine Hofer', 'Mozartgasse 7', 'Markus Leitner', 'Gebrüdern Wachtelhuber', 'Muster']
				.map((value) => value.length)
				.reduce((a, b) => a + b) / TEXT.length;
		expect(share).toBeCloseTo(erwartet);
		expect(percent(share)).toBe(String(Math.round(erwartet * 100)));
		expect(percent(0.004)).toBe('< 1');
		expect(percent(0)).toBe('0');
	});
});

describe('Highlighted marking', () => {
	it('splits flow text at the marking and leaves the text unchanged', () => {
		const segments = originalSegments(input(EMPTY_STATE, null));
		const herrn = TEXT.indexOf('Herrn');
		const marked = withSelection(segments, { start: herrn, end: herrn + 5 });
		expect(segmentText(marked)).toBe(TEXT);
		expect(marked.filter((s) => s.kind === 'selection')).toEqual([
			{ kind: 'selection', text: 'Herrn', source: { start: herrn, end: herrn + 5 } }
		]);
		expect(withSelection(segments, null)).toEqual(segments);
	});
});

describe('Marking to source', () => {
	const result = apply([code(HOFER)]);
	const paragraphs = [resultSegments(input(EMPTY_STATE, result))];
	const index = (kind: string) => paragraphs[0].findIndex((s: Segment) => s.kind === kind);

	it('converts flow text one-to-one without punctuation at the edges', () => {
		const plain = paragraphs[0].findIndex((s) => s.text.includes('Herrn'));
		const versatz = paragraphs[0][plain].text.indexOf('Herrn');
		const section = sourceSection(
			paragraphs,
			{ paragraph: 0, index: plain, versatz: versatz - 1 },
			{ paragraph: 0, index: plain, versatz: versatz + 'Herrn '.length },
			TEXT
		);
		expect(TEXT.slice(section!.start, section!.end)).toBe('Herrn');
	});

	it('takes a truncated code fully', () => {
		const before = 0;
		const codeIndex = index('code');
		const section = sourceSection(
			paragraphs,
			{ paragraph: 0, index: before, versatz: 0 },
			{ paragraph: 0, index: codeIndex, versatz: 3 },
			TEXT
		);
		expect(TEXT.slice(section!.start, section!.end)).toBe('Frau Sabine Hofer');
		expect(
			sourceSection(
				paragraphs,
				{ paragraph: 0, index: 0, versatz: 4 },
				{ paragraph: 0, index: 0, versatz: 5 },
				TEXT
			)
		).toBeNull();
	});
});

describe('Apply queue', () => {
	it('has a pending call and collapses to latest state', async () => {
		const freigaben: (() => void)[] = [];
		const run = vi.fn(
			(job: number) => new Promise<number>((next) => freigaben.push(() => next(job * 10)))
		);
		const ergebnisse: number[] = [];
		const running: boolean[] = [];
		const schlange = new ApplyQueue(run, {
			result: (value) => ergebnisse.push(value),
			error: () => undefined,
			running: (value) => running.push(value)
		});
		schlange.places(1);
		schlange.places(2);
		schlange.places(3);
		expect(run).toHaveBeenCalledTimes(1);
		freigaben.shift()!();
		await vi.waitFor(() => expect(run).toHaveBeenCalledTimes(2));
		expect(run).toHaveBeenLastCalledWith(3);
		freigaben.shift()!();
		await vi.waitFor(() => expect(ergebnisse).toEqual([30]));
		expect(schlange.running).toBe(false);
		expect(running.at(-1)).toBe(false);
	});

	it('ignores a late response after discard', async () => {
		let release: () => void = () => undefined;
		const error = vi.fn();
		const result = vi.fn();
		const schlange = new ApplyQueue(
			() => new Promise<number>((_, ab) => (release = () => ab(new Error('x')))),
			{ result, error }
		);
		schlange.places(1);
		schlange.discard();
		release();
		await vi.waitFor(() => expect(schlange.running).toBe(false));
		expect(error).not.toHaveBeenCalled();
		expect(result).not.toHaveBeenCalled();
	});
});

describe('Menu at a single location', () => {
	const context = { question: false, locked: false, places: 3, correction: false };

	it('offers "Only here" and "Keep all names" for a code', () => {
		const menu = codeMenu({ kind: 'code', text: '<<X>>', category: 'names' }, context, t);
		expect(menu).toEqual({
			title: 'Name · 3 Stellen',
			entries: [
				{ label: 'Nur hier behalten', action: 'onlyHere' },
				{ label: 'Alle Namen behalten', action: 'categoryFrom' }
			]
		});
	});

	it('never shows locked categories as whole (E3)', () => {
		const menu = codeMenu(
			{ kind: 'code', text: '<<IBAN>>', category: 'finance_ids' },
			{ ...context, locked: true, places: 1 },
			t
		);
		expect(menu?.entries.map((e) => e.action)).toEqual(['onlyHere']);
		expect(menu?.title).toBe('Konto- oder Ausweisdaten · 1 Stelle');
	});

	it('recognizes kept places, questions, categories and addenda', () => {
		const keep = codeMenu({ kind: 'kept-here', text: 'x', category: 'names' }, context, t);
		expect(keep?.entries).toEqual([{ label: 'Wieder maskieren', action: 'undo' }]);
		const question = codeMenu(
			{ kind: 'code', text: 'x', category: 'names' },
			{ ...context, question: true, decision: { action: 'accept', by: 'user' } },
			t
		);
		expect(question).toEqual({
			title: 'Von Ihnen maskiert',
			entries: [{ label: 'Trotzdem behalten', action: 'keep' }]
		});
		const place = codeMenu(
			{ kind: 'kept-category', text: 'Linz', category: 'locations' },
			context,
			t
		);
		expect(place).toEqual({
			title: 'Orte bleiben stehen',
			entries: [{ label: 'Alle Orte maskieren', action: 'categoryOn' }]
		});
		const contact = codeMenu({ kind: 'code', text: 'x', category: 'contact' }, context, t);
		expect(contact?.entries[1].label).toBe('Alle Telefonnummern & E-Mails behalten');
		const hand = codeMenu({ kind: 'placeholder', text: '[Name]', manual: true }, context, t);
		expect(hand?.entries).toEqual([{ label: 'Nicht mehr maskieren', action: 'removeHand' }]);
		expect(codeMenu({ kind: 'question', text: 'x' }, context, t)).toBeNull();
	});
});

describe('Detection runs', () => {
	it('describes files with size, findings and questions', () => {
		const pdf = { name: 'Rechnung.pdf', pages: 1, format: 'pdf' };
		expect(
			fileMeta(pdf, { status: 'review', counts: { names: 12, addresses: 3 }, questions: 2 }, t)
		).toEqual({
			text: '1 Seite · 15 Stellen gefunden',
			questions: '2 brauchen Ihren Blick'
		});
		expect(fileMeta(pdf, { status: 'review', counts: {}, questions: 0 }, t).text).toBe(
			'1 Seite · 0 Stellen gefunden · keine Fragen'
		);
		const scan = { name: 'Vollmacht_Scan.jpg', pages: null, format: 'jpg' };
		expect(
			fileMeta(scan, { status: 'processing', counts: { names: 3 }, questions: null }, t)
		).toEqual({
			text: 'Foto eines Dokuments · bisher 3 Stellen gefunden'
		});
	});

	it('counts "Bisher gefunden" in catalog order without zeros', () => {
		expect(previousFound({ names: 12, finance_ids: 3, dates: 0 }, t)).toEqual([
			{ num: 3, label: 'Konto-, Ausweis- & Zugangsdaten' },
			{ num: 12, label: 'Namen' }
		]);
	});

	it('names masking and protected copy name', () => {
		expect(maskingLine('reversible', false, true, t)).toBe('Maskierung: reversibel (Ihr Standard)');
		expect(maskingLine('irreversible', false, false, t)).toBe('Maskierung: irreversibel');
		expect(protectedName('Rechnung.pdf')).toBe('Rechnung_geschützt.pdf');
	});

	it('names the copy like the sidecar when saving (`saving.output_plan`)', () => {
		// E-L3/E-L4: scans and images keep their format, including codes.
		expect(protectedName('Scan.jpg')).toBe('Scan_geschützt.jpg');
		expect(protectedName('Foto.png')).toBe('Foto_geschützt.png');
		expect(protectedName('Vollmacht.pdf')).toBe('Vollmacht_geschützt.pdf');
		expect(protectedName('notiz')).toBe('notiz_geschützt');
		expect(protectedName('Brief.docx', { addition: '_protected' })).toBe('Brief_protected.docx');
	});
});

describe('detectionFinding', () => {
	const file = (
		status: 'queued' | 'processing' | 'review' | 'failed',
		counts: Record<string, number> = {},
		questions: number | null = null
	) => ({ status, counts, questions });

	it('polls while detection runs or entry is still loading', () => {
		expect(detectionFinding(true, [file('review', { person: 2 }, 1)])).toBe('running');
		expect(detectionFinding(false, [])).toBe('running');
	});

	it('shows questions before findings', () => {
		expect(detectionFinding(false, [file('review', { person: 3 }, 0), file('review', {}, 2)])).toBe(
			'questions'
		);
	});

	it('distinguishes findings from an empty result', () => {
		expect(detectionFinding(false, [file('review', { person: 3 }, 0)])).toBe('found');
		expect(detectionFinding(false, [file('review', { person: 0 }, 0)])).toBe('nothing');
	});

	it('reports an error when no file completed', () => {
		expect(detectionFinding(false, [file('failed')])).toBe('error');
	});

	it('skips failed files next to completed ones', () => {
		expect(detectionFinding(false, [file('failed'), file('review', { iban: 1 }, 0)])).toBe('found');
	});
});
