import type { PlacementOut, PlacementText } from '#lib/sidecar/api.gen.js';
import type { Segment } from '#lib/ui/segments.js';
import { LINE, type Word, type WordIndex } from './map.js';

/**
 * Convert a segment to a page area (spec §4.3).
 *
 * A finding always covers whole words – exactly as the current export
 * edits (`_union_rect` over the word segments). One rectangle per line; a
 * finding spanning a line break yields two.
 */

export interface Box {
	page: number;
	x0: number;
	y0: number;
	x1: number;
	y1: number;
}

export interface Marking {
	segment: Segment;
	/** Line boxes in reading order; the first carries number and focus. */
	boxen: Box[];
	/** Only in the result: placement plan for the finding, all pages (spec §4.4). */
	plan?: readonly PlacementOut[];
	/** From that, the overpaint on *this* page (`aufSeite`); without a plan only a marker. */
	overpaint?: Overpaint;
}

/** Page area and replacement text for a finding – drawn, not computed. */
export interface Overpaint {
	areas: Box[];
	fill: 'white' | 'black';
	text: PlacementText | null;
}

/** Kinds that appear overpainted in the result: code and placeholder. */
const UEBERMALT = new Set<Segment['kind']>(['code', 'placeholder']);

/**
 * Placements grouped by source range. The key is the range, not the
 * finding ID: addenda have their own ID in the window, the apply uses the sidecar’s –
 * the range is shared.
 */
export function platzierungsIndex(
	placements: readonly PlacementOut[] | null | undefined
): Map<string, PlacementOut[]> {
	const index = new Map<string, PlacementOut[]>();
	for (const placement of placements ?? []) {
		const key = `${placement.start}:${placement.end}`;
		const list = index.get(key);
		if (list) list.push(placement);
		else index.set(key, [placement]);
	}
	return index;
}

/** One rectangle per (page, line), in word order. */
export function linesBoxen(words: readonly Word[]): Box[] {
	const boxen: Box[] = [];
	let key: string | null = null;
	for (const word of words) {
		const [page, x0, y0, x1, y1] = word;
		const fresh = `${page}:${word[LINE]}`;
		const last = boxen[boxen.length - 1];
		if (fresh === key && last) {
			last.x0 = Math.min(last.x0, x0);
			last.y0 = Math.min(last.y0, y0);
			last.x1 = Math.max(last.x1, x1);
			last.y1 = Math.max(last.y1, y1);
			continue;
		}
		key = fresh;
		boxen.push({ page, x0, y0, x1, y1 });
	}
	return boxen;
}

/**
 * All findings that are not `plain`, with their boxes. Segments without a word
 * on a page (e.g., whitespace only) are dropped.
 *
 * With `placements` (result) codes and placeholders get their overpaint per page.
 */
export function markings(
	segments: readonly Segment[],
	index: WordIndex,
	placements?: ReadonlyMap<string, readonly PlacementOut[]>
): Marking[] {
	const list: Marking[] = [];
	for (const segment of segments) {
		if (segment.kind === 'plain' || !segment.source) continue;
		const boxen = linesBoxen(index.in(segment.source));
		if (boxen.length === 0) continue;
		const plan =
			placements && UEBERMALT.has(segment.kind)
				? placements.get(`${segment.source.start}:${segment.source.end}`)
				: undefined;
		list.push(plan ? { segment, boxen, plan } : { segment, boxen });
	}
	return list;
}

/** The overpaint for a finding on a page, or `undefined` if no plan there. */
export function overpaintOn(plan: readonly PlacementOut[], page: number): Overpaint | undefined {
	const parts = plan.filter((placement) => placement.page === page);
	if (parts.length === 0) return undefined;
	return {
		areas: parts.flatMap((placement) =>
			placement.cover.map(([x0, y0, x1, y1]) => ({ page, x0, y0, x1, y1 }))
		),
		fill: parts.some((placement) => placement.fill === 'black') ? 'black' : 'white',
		text: parts.find((placement) => placement.text)?.text ?? null
	};
}

/** The markings for a page, omitting boxes from other pages. */
export function onPage(list: readonly Marking[], page: number): Marking[] {
	const result: Marking[] = [];
	for (const marking of list) {
		const boxen = marking.boxen.filter((box) => box.page === page);
		const overpaint = marking.plan ? overpaintOn(marking.plan, page) : undefined;
		if (boxen.length === 0 && !overpaint) continue;
		result.push(
			overpaint
				? { segment: marking.segment, boxen, overpaint }
				: { segment: marking.segment, boxen }
		);
	}
	return result;
}

/** Boxes collected per page, in their order. */
export function afterPage(boxen: Iterable<Box>): Map<number, Box[]> {
	const perPage = new Map<number, Box[]>();
	for (const box of boxen) {
		const list = perPage.get(box.page);
		if (list) list.push(box);
		else perPage.set(box.page, [box]);
	}
	return perPage;
}

/** Line boxes for ranges (e.g., invisible text), collected per page. */
export function boxenPerPage(
	sections: readonly { start: number; end: number }[],
	index: WordIndex
): Map<number, Box[]> {
	return afterPage(sections.flatMap((section) => linesBoxen(index.in(section))));
}

/**
 * Expand a box to at least `mindest` units wide and high, centered –
 * for UI controls on tiny text (spec §4.6: at least 12 px).
 */
export function atLeast(box: Box, mindest: number): Box {
	const width = Math.max(box.x1 - box.x0, mindest);
	const height = Math.max(box.y1 - box.y0, mindest);
	const mx = (box.x0 + box.x1) / 2;
	const my = (box.y0 + box.y1) / 2;
	return {
		page: box.page,
		x0: mx - width / 2,
		y0: my - height / 2,
		x1: mx + width / 2,
		y1: my + height / 2
	};
}

/** Rectangle as SVG attributes (`x`, `y`, `width`, `height`). */
export function rect(box: Box, rand = 0): { x: number; y: number; width: number; height: number } {
	return {
		x: box.x0 - rand,
		y: box.y0 - rand,
		width: box.x1 - box.x0 + 2 * rand,
		height: box.y1 - box.y0 + 2 * rand
	};
}
