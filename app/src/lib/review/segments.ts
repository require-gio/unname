import { categoryForType } from '#lib/masking/catalog.js';
import type { Segment, SegmentKind } from '#lib/ui/segments.js';
import type { AppliedFindingOut, ApplyResult, FindingOut } from '#lib/sidecar/api.gen.js';
import { categoryBy, type Section, type Question, type ReviewState } from './state.js';

/**
 * Text views for the review view (spec §2, board “3 · review”).
 *
 * - **Result:** the text as stored — codes and placeholders from the sidecar’s
 *   Apply response. Open questions appear in amber with their original text:
 *   they are masked on save (“mask when in doubt”) but remain readable beforehand.
 * - **Original:** the source text, masked indigo background.
 * - **Side-by-side:** both, paragraph by paragraph from the sidecar’s `blocks`.
 *
 * Each segment knows its range in the source (`quelle`). That creates a
 * highlight in the window for the original position — in both views.
 *
 * No calculations are performed here; the sidecar already provides which
 * segment is masked and where it appears in the result via `applied_findings`.
 */

export type View = 'result' | 'original';

export interface SegmentInput {
	text: string;
	findings: readonly FindingOut[];
	status: ReviewState;
	/** The latest Apply response; without it we fall back to a prediction from the current state. */
	result: Pick<ApplyResult, 'text' | 'applied_findings'> | null;
	questions: readonly Question[];
	categoryActive: (category: string) => boolean;
}

/** Modes whose replacement appears as code (same width); everything else as placeholder. */
const CODE_MODI = new Set(['token_deterministic', 'encrypt', 'hash']);

interface Mark {
	start: number;
	end: number;
	id: string;
	type: string;
	category: string;
	kind: SegmentKind;
	number?: number;
	operable: boolean;
	manual?: boolean;
}

function openNumbers(questions: readonly Question[]): Map<string, number> {
	return new Map(
		questions
			.filter((question) => !question.decision)
			.map((question) => [question.finding.id, question.number])
	);
}

function korrigierte(status: ReviewState): Set<string> {
	return new Set(status.manual.flatMap((entry) => (entry.corrected ? [entry.corrected] : [])));
}

/**
 * Visual representation of a non-replaced segment: keep (dashed for the category,
 * dotted for “only here”), shown as a question—or unmarked.
 */
function notReplaced(
	finding: FindingOut,
	input: SegmentInput,
	numbers: ReadonlyMap<string, number>
): Pick<Mark, 'kind' | 'number' | 'operable'> | null {
	const decision = input.status.decisions[finding.id];
	if (decision?.action === 'reject') {
		return { kind: decision.by === 'ai-suggestion' ? 'ai-kept' : 'kept-here', operable: true };
	}
	const number = numbers.get(finding.id);
	if (number !== undefined) return { kind: 'question', number, operable: false };
	if (!input.categoryActive(categoryBy(finding))) {
		return { kind: 'kept-category', operable: true };
	}
	return null;
}

/**
 * Is the segment replaced? Use the Apply response when available; otherwise rely
 * on the prediction from the current state and categories — the view is then
 * marked as outdated.
 */
function istReplaced(
	finding: FindingOut,
	input: SegmentInput,
	angewandt: ReadonlySet<string> | null
): boolean {
	if (angewandt) return angewandt.has(finding.id);
	const decision = input.status.decisions[finding.id];
	if (decision) return decision.action === 'accept';
	return input.categoryActive(categoryBy(finding));
}

function pad(text: string, marken: Mark[], by: number, until: number): Segment[] {
	const sorted = [...marken].sort((a, b) => a.start - b.start || b.end - a.end);
	const segments: Segment[] = [];
	let cursor = by;
	for (const mark of sorted) {
		if (mark.start < cursor || mark.end > until) continue;
		if (mark.start > cursor) {
			segments.push({
				kind: 'plain',
				text: text.slice(cursor, mark.start),
				source: { start: cursor, end: mark.start }
			});
		}
		segments.push({
			kind: mark.kind,
			text: text.slice(mark.start, mark.end),
			id: mark.id,
			type: mark.type,
			category: mark.category,
			operable: mark.operable,
			source: { start: mark.start, end: mark.end },
			...(mark.number !== undefined ? { number: mark.number } : {}),
			...(mark.manual ? { manual: true } : {})
		});
		cursor = mark.end;
	}
	if (cursor < until) {
		segments.push({
			kind: 'plain',
			text: text.slice(cursor, until),
			source: { start: cursor, end: until }
		});
	}
	return segments;
}

/** Original: source text with markings. */
export function originalSegments(input: SegmentInput): Segment[] {
	const numbers = openNumbers(input.questions);
	const without = korrigierte(input.status);
	const angewandt = input.result
		? new Set(input.result.applied_findings.map((item) => item.finding_id))
		: null;
	const marken: Mark[] = [];
	for (const finding of input.findings) {
		if (without.has(finding.id)) continue;
		const basis = {
			start: finding.start,
			end: finding.end,
			id: finding.id,
			type: finding.type,
			category: categoryBy(finding)
		};
		const number = numbers.get(finding.id);
		if (number !== undefined) {
			marken.push({ ...basis, kind: 'question', number, operable: false });
		} else if (istReplaced(finding, input, angewandt)) {
			marken.push({ ...basis, kind: 'masked-original', operable: true });
		} else {
			const kind = notReplaced(finding, input, numbers);
			if (kind) marken.push({ ...basis, ...kind });
		}
	}
	for (const entry of input.status.manual) {
		marken.push({
			start: entry.start,
			end: entry.end,
			id: entry.id,
			type: entry.type,
			category: categoryForType(entry.type),
			kind: 'masked-original',
			operable: true,
			manual: true
		});
	}
	return pad(input.text, marken, 0, input.text.length);
}

/**
 * Result: the text from the Apply response. Unchanged source text appears between
 * replacements; retained segments and questions are marked there. If an
 * intermediate piece does not match the original, it stays unmarked and without
 * a source reference — better no marking than an incorrect one.
 */
export function resultSegments(input: SegmentInput): Segment[] {
	const result = input.result;
	if (!result) return [];
	const numbers = openNumbers(input.questions);
	const without = korrigierte(input.status);
	const afterId = new Map(input.findings.map((finding) => [finding.id, finding]));
	const handAfterSection = new Map(
		input.status.manual.map((entry) => [`${entry.start}:${entry.end}`, entry])
	);
	// A manually marked value applies to all its occurrences; the sidecar
	// replaces the others. These positions belong to the marking of that value
	// (`Segment.hand`): “Do not mask” restores them with the value.
	const handAfterValue = new Map<string, { id: string }>();
	for (const entry of input.status.manual) {
		const value = valueKey(input.text.slice(entry.start, entry.end));
		if (value && !handAfterValue.has(value)) handAfterValue.set(value, entry);
	}
	const angewandt = [...result.applied_findings].sort(
		(a, b) => a.output_start - b.output_start || a.original_start - b.original_start
	);
	const angewandtIds = new Set(angewandt.map((item) => item.finding_id));

	const rest: Mark[] = [];
	for (const finding of input.findings) {
		if (without.has(finding.id) || angewandtIds.has(finding.id)) continue;
		const kind = notReplaced(finding, input, numbers);
		if (!kind) continue;
		rest.push({
			start: finding.start,
			end: finding.end,
			id: finding.id,
			type: finding.type,
			category: categoryBy(finding),
			...kind
		});
	}

	const segments: Segment[] = [];
	const gap = (fromBy: number, fromUntil: number, sourceBy: number, sourceUntil: number) => {
		if (fromUntil <= fromBy && sourceUntil <= sourceBy) return;
		const piece = result.text.slice(fromBy, fromUntil);
		if (piece !== input.text.slice(sourceBy, sourceUntil)) {
			if (piece) segments.push({ kind: 'plain', text: piece });
			return;
		}
		segments.push(...pad(input.text, rest, sourceBy, sourceUntil));
	};

	let fromCursor = 0;
	let sourceCursor = 0;
	for (const item of angewandt) {
		if (item.output_start < fromCursor || item.original_start < sourceCursor) continue;
		gap(fromCursor, item.output_start, sourceCursor, item.original_start);
		segments.push(
			replacement(item, input, result.text, afterId, handAfterSection, handAfterValue, numbers)
		);
		fromCursor = item.output_end;
		sourceCursor = item.original_end;
	}
	gap(fromCursor, result.text.length, sourceCursor, input.text.length);
	return segments;
}

function replacement(
	item: AppliedFindingOut,
	input: SegmentInput,
	output: string,
	afterId: ReadonlyMap<string, FindingOut>,
	handAfterSection: ReadonlyMap<string, { id: string }>,
	handAfterValue: ReadonlyMap<string, { id: string }>,
	numbers: ReadonlyMap<string, number>
): Segment {
	const finding = afterId.get(item.finding_id);
	const source = { start: item.original_start, end: item.original_end };
	const original = input.text.slice(source.start, source.end);
	const own = finding
		? undefined
		: handAfterSection.get(`${item.original_start}:${item.original_end}`);
	// Further occurrences keep their own ID: multiple segments with the same ID would break keys when rendering.
	const hand =
		own ??
		(!finding && item.source === 'manual' ? handAfterValue.get(valueKey(original)) : undefined);
	const basis = {
		id: finding?.id ?? own?.id ?? item.finding_id,
		type: item.entity_type,
		category: finding ? categoryBy(finding) : (item.category ?? categoryForType(item.entity_type)),
		source
	};
	const number = finding ? numbers.get(finding.id) : undefined;
	if (number !== undefined) {
		return { ...basis, kind: 'question', text: original, number, operable: false };
	}
	return {
		...basis,
		kind: CODE_MODI.has(item.mode) ? 'code' : 'placeholder',
		text: output.slice(item.output_start, item.output_end),
		original,
		operable: true,
		...(finding ? {} : { manual: true }),
		...(hand ? { hand: hand.id } : {})
	};
}

/** Same value regardless of case and whitespace. */
function valueKey(text: string): string {
	return text.normalize('NFKC').toLowerCase().replace(/\s+/gu, '');
}

export function segmentsFor(view: View, input: SegmentInput): Segment[] {
	if (view === 'original' || !input.result) return originalSegments(input);
	return resultSegments(input);
}

/** Does the segment contain exactly the source text of its range (no code or placeholder)? */
export function sameText(segment: Segment): boolean {
	return (
		segment.source !== undefined &&
		segment.kind !== 'code' &&
		segment.kind !== 'placeholder' &&
		segment.text.length === segment.source.end - segment.source.start
	);
}

/**
 * Split segments into paragraphs according to the sidecar `blocks` (source ranges).
 *
 * Plain text is split at paragraph boundaries; code stays within the paragraph
 * where it starts. Empty lines between paragraphs are omitted – paragraphs are
 * separate elements. Without blocks everything is a single paragraph.
 */
export function inParagraphs(
	segments: readonly Segment[],
	blocks: readonly Section[]
): Segment[][] {
	if (blocks.length === 0) return [[...segments]];
	const sorted = [...blocks].sort((a, b) => a.start - b.start);
	const paragraphs: Segment[][] = sorted.map(() => []);
	let last = 0;
	const blockFor = (position: number): number => {
		const index = sorted.findIndex((block) => position < block.end);
		return index < 0 ? sorted.length - 1 : index;
	};
	for (const segment of segments) {
		const source = segment.source;
		if (!source) {
			paragraphs[last].push(segment);
			continue;
		}
		if (!sameText(segment)) {
			last = blockFor(source.start);
			paragraphs[last].push(segment);
			continue;
		}
		sorted.forEach((block, index) => {
			const start = Math.max(source.start, block.start);
			const end = Math.min(source.end, block.end);
			if (end <= start) return;
			last = index;
			paragraphs[index].push({
				...segment,
				text: segment.text.slice(start - source.start, end - source.start),
				source: { start, end }
			});
		});
	}
	return paragraphs;
}

/* ---------------------------------------------------------------------------
 * Metrics for toolbar and masking menu
 * ------------------------------------------------------------------------- */

/**
 * Masked places excluding open questions — shown in the view as questions,
 * even though they are masked on save.
 */
export function maskedPlaces(
	result: Pick<ApplyResult, 'applied_findings'> | null,
	questions: readonly Question[]
): number {
	if (!result) return 0;
	const open = new Set(questions.filter((question) => !question.decision).map((f) => f.finding.id));
	return result.applied_findings.filter((item) => !open.has(item.finding_id)).length;
}

/**
 * Share of text that will be masked (E6, spec §9.2) — based on original
 * sections of the apply response, counting overlaps only once.
 */
export function maskedShare(
	result: Pick<ApplyResult, 'applied_findings'> | null,
	textLength: number
): number {
	if (!result || textLength <= 0) return 0;
	const sections = result.applied_findings
		.map((item) => ({ start: item.original_start, end: item.original_end }))
		.sort((a, b) => a.start - b.start);
	let sum = 0;
	let boundary = 0;
	for (const section of sections) {
		const start = Math.max(section.start, boundary);
		if (section.end > start) sum += section.end - start;
		boundary = Math.max(boundary, section.end);
	}
	return Math.min(1, sum / textLength);
}

/** "8"; under one percent "< 1", so a remainder never becomes 0. */
export function percent(share: number): string {
	if (share <= 0) return '0';
	const value = share * 100;
	if (value < 1) return '< 1';
	return String(Math.round(value));
}

/**
 * Highlight a selection in plain text while the selection menu is open:
 * focus is in the menu, so the browser selection would be invisible.
 * Code and retained places keep their appearance.
 */
export function withSelection(segments: readonly Segment[], section: Section | null): Segment[] {
	if (!section) return [...segments];
	return segments.flatMap((segment): Segment[] => {
		const source = segment.source;
		if (segment.kind !== 'plain' || !source) return [segment];
		const start = Math.max(source.start, section.start);
		const end = Math.min(source.end, section.end);
		if (end <= start) return [segment];
		const part = (kind: SegmentKind, by: number, until: number): Segment => ({
			kind,
			text: segment.text.slice(by - source.start, until - source.start),
			source: { start: by, end: until }
		});
		return [
			...(start > source.start ? [part('plain', source.start, start)] : []),
			part('selection', start, end),
			...(end < source.end ? [part('plain', end, source.end)] : [])
		];
	});
}
