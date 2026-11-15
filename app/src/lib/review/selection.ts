import type { Segment } from '#lib/ui/segments.js';
import type { Section } from './state.js';
import { sameText } from './segments.js';

/**
 * Convert a UI selection to a source text position.
 *
 * The UI shows paragraphs from segments; each segment has a DOM attribute
 * `data-seg="<paragraph>:<index>"`. An anchor is a location inside that:
 * paragraph, segment and offset within the segment text. This function maps it
 * to a range in the original source – the same text appears in the result view
 * as in the original.
 *
 * If a selection ends inside a code token, the whole token is counted because
 * the token contains no letters belonging to the original text.
 */

export interface Anchor {
	paragraph: number;
	index: number;
	versatz: number;
}

function comparison(a: Anchor, b: Anchor): number {
	return a.paragraph - b.paragraph || a.index - b.index || a.versatz - b.versatz;
}

/** Source position for an anchor; `null` if it has no source reference. */
export function sourcePoint(
	segment: Segment,
	versatz: number,
	page: 'start' | 'end'
): number | null {
	const source = segment.source;
	if (!source) return null;
	if (sameText(segment)) {
		return source.start + Math.max(0, Math.min(versatz, source.end - source.start));
	}
	if (page === 'start')
		return versatz >= segment.text.length && segment.text ? source.end : source.start;
	return versatz <= 0 && segment.text ? source.start : source.end;
}

/** Characters that should not be part of a selection's boundaries. */
const RAND = /[\s.,;:!?()[\]{}"'«»„“”‚‘’–—-]/u;

/**
 * The selected range in the source text – trimmed of whitespace and punctuation
 * at the edges (double-click often includes a trailing comma). Returns `null`
 * when nothing useful is selected.
 */
export function sourceSection(
	paragraphs: readonly (readonly Segment[])[],
	a: Anchor,
	b: Anchor,
	sourceText: string
): Section | null {
	const [by, until] = comparison(a, b) <= 0 ? [a, b] : [b, a];
	const segBy = paragraphs[by.paragraph]?.[by.index];
	const segUntil = paragraphs[until.paragraph]?.[until.index];
	if (!segBy || !segUntil) return null;
	const start = sourcePoint(segBy, by.versatz, 'start');
	const end = sourcePoint(segUntil, until.versatz, 'end');
	if (start === null || end === null) return null;
	return beschneiden(start, end, sourceText);
}

/** Trim whitespace and punctuation from the edges of a range – or return `null`. */
export function beschneiden(start: number, end: number, sourceText: string): Section | null {
	if (end < start) [start, end] = [end, start];
	while (start < end && RAND.test(sourceText[start])) start += 1;
	while (end > start && RAND.test(sourceText[end - 1])) end -= 1;
	return end > start ? { start, end } : null;
}
