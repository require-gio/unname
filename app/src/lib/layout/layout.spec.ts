import { describe, expect, it } from 'vitest';
import type { LayoutResponse, PlacementOut } from '#lib/sidecar/api.gen.js';
import type { Segment } from '#lib/ui/segments.js';
import {
	onPage,
	boxenPerPage,
	markings,
	atLeast,
	platzierungsIndex,
	rect,
	linesBoxen
} from './boxen.js';
import { pagesMap, WordIndex } from './map.js';
import { displaySize, imageLevel, inUnits } from './scale.js';
import { sectionBetween, WordMatch, lineAround } from './word-selection.js';

// "MAX MUSTERMANN\nMUSTERGASSE 12\fSeite zwei"
const WORDS: [number, number, number, number, number, number, number, number][] = [
	[0, 59, 172, 80, 184, 0, 3, 0],
	[0, 82, 172, 144, 184, 4, 14, 0],
	[0, 59, 185, 120, 197, 15, 26, 1],
	[0, 122, 185, 134, 197, 27, 29, 1],
	[1, 59, 100, 90, 112, 30, 35, 2],
	[1, 92, 100, 110, 112, 36, 40, 2]
];

describe('Layout map', () => {
	it('accepts only fixed pages', () => {
		expect(pagesMap(null)).toBeNull();
		expect(pagesMap({ job_id: 'j', kind: 'text' })).toBeNull();
		expect(pagesMap({ job_id: 'j', kind: 'pages', pages: [] })).toBeNull();
		const map = pagesMap({
			job_id: 'j',
			kind: 'pages',
			pages: [{ index: 0, width: 595, height: 842, unit: 'pt', source: 'text', scales: [1, 2] }],
			words: WORDS
		} satisfies LayoutResponse);
		expect(map?.words.num).toBe(6);
		expect(map?.hidden).toEqual([]);
	});

	it('finds words in a range via binary search', () => {
		const index = new WordIndex(WORDS);
		const offsets = (start: number, end: number) =>
			index.in({ start, end }).map((word) => [word[5], word[6]]);
		expect(offsets(4, 14)).toEqual([[4, 14]]);
		// Partial matches should return the full word (as in export).
		expect(offsets(6, 9)).toEqual([[4, 14]]);
		expect(offsets(0, 26)).toEqual([
			[0, 3],
			[4, 14],
			[15, 26]
		]);
		// Only whitespace between words: no match.
		expect(offsets(3, 4)).toEqual([]);
		expect(offsets(14, 14)).toEqual([]);
		expect(offsets(38, 99)).toEqual([[36, 40]]);
		expect(offsets(99, 120)).toEqual([]);
	});
});

describe('Boxen', () => {
	it('aggregates words per line and page', () => {
		expect(linesBoxen(WORDS)).toEqual([
			{ page: 0, x0: 59, y0: 172, x1: 144, y1: 184 },
			{ page: 0, x0: 59, y0: 185, x1: 134, y1: 197 },
			{ page: 1, x0: 59, y0: 100, x1: 110, y1: 112 }
		]);
	});

	it('creates markings only for non-plain segments', () => {
		const segments: Segment[] = [
			{ kind: 'plain', text: 'MAX ', source: { start: 0, end: 4 } },
			{
				kind: 'masked-original',
				text: 'MUSTERMANN\nMUSTERGASSE',
				id: 'f1',
				source: { start: 4, end: 26 }
			},
			{ kind: 'plain', text: ' 12\f', source: { start: 26, end: 30 } },
			{ kind: 'question', text: 'Seite', id: 'f2', number: 1, source: { start: 30, end: 35 } },
			{ kind: 'kept-here', text: 'ohne Quelle', id: 'f3' }
		];
		const list = markings(segments, new WordIndex(WORDS));
		expect(list.map((m) => [m.segment.id, m.boxen.length])).toEqual([
			['f1', 2],
			['f2', 1]
		]);
		expect(onPage(list, 1).map((m) => m.segment.id)).toEqual(['f2']);
		expect(onPage(list, 0)[0].boxen[0]).toEqual({ page: 0, x0: 82, y0: 172, x1: 144, y1: 184 });
	});

	it('collects boxes per page', () => {
		const perPage = boxenPerPage(
			[
				{ start: 0, end: 14 },
				{ start: 30, end: 40 }
			],
			new WordIndex(WORDS)
		);
		expect([...perPage.keys()]).toEqual([0, 1]);
		expect(perPage.get(1)).toEqual([{ page: 1, x0: 59, y0: 100, x1: 110, y1: 112 }]);
	});

	it('converts boxes to SVG attributes', () => {
		expect(rect({ page: 0, x0: 10, y0: 20, x1: 30, y1: 25 }, 1)).toEqual({
			x: 9,
			y: 19,
			width: 22,
			height: 7
		});
	});
});

describe('Overpaint (placement plan)', () => {
	const REPLACEMENT = {
		value: '<<PERSON_e395da1b>>',
		x: 82,
		baseline: 181,
		size: 8,
		font: 'sans',
		bold: false,
		color: '#000000',
		width: 70,
		rotate: 0
	} as const;
	// A placement that spans a page break: text on page 0, only box on page 1.
	const PLAN: PlacementOut[] = [
		{
			finding_id: 'sidecar-1',
			start: 4,
			end: 35,
			page: 0,
			cover: [[81, 172, 152, 184]],
			fill: 'white',
			text: REPLACEMENT
		},
		{
			finding_id: 'sidecar-1',
			start: 4,
			end: 35,
			page: 1,
			cover: [[58, 100, 91, 112]],
			fill: 'white',
			text: null
		}
	];

	const code: Segment = {
		kind: 'code',
		text: REPLACEMENT.value,
		id: 'hand-1',
		operable: true,
		source: { start: 4, end: 35 }
	};

	it('assigns placements by source range, not by ID', () => {
		const list = markings([code], new WordIndex(WORDS), platzierungsIndex(PLAN));
		const page0 = onPage(list, 0)[0];
		expect(page0.overpaint).toEqual({
			areas: [{ page: 0, x0: 81, y0: 172, x1: 152, y1: 184 }],
			fill: 'white',
			text: REPLACEMENT
		});
		const page1 = onPage(list, 1)[0];
		expect(page1.overpaint?.text).toBeNull();
		expect(page1.overpaint?.areas).toHaveLength(1);
	});

	it('overpaints only codes and placeholders; without plan the marking stays', () => {
		const question: Segment = { ...code, kind: 'question', number: 1 };
		const index = platzierungsIndex(PLAN);
		expect(onPage(markings([question], new WordIndex(WORDS), index), 0)[0].overpaint).toBe(
			undefined
		);
		expect(onPage(markings([code], new WordIndex(WORDS)), 0)[0].overpaint).toBe(undefined);
		expect(platzierungsIndex(undefined).size).toBe(0);
	});

	it('blackens when a line is fully filled', () => {
		const schwarz = PLAN.map((part) => ({ ...part, fill: 'black' as const, text: null }));
		const list = markings([code], new WordIndex(WORDS), platzierungsIndex(schwarz));
		expect(onPage(list, 0)[0].overpaint?.fill).toBe('black');
	});

	it('ensures tiny touch areas are at least the required size', () => {
		expect(atLeast({ page: 0, x0: 10, y0: 10, x1: 11, y1: 10.5 }, 6)).toEqual({
			page: 0,
			x0: 7.5,
			y0: 7.25,
			x1: 13.5,
			y1: 13.25
		});
		const large = { page: 0, x0: 0, y0: 0, x1: 40, y1: 20 };
		expect(atLeast(large, 6)).toEqual(large);
	});
});

describe('Wordwise selection', () => {
	const match = new WordMatch(WORDS);

	it('finds the word under the pointer, otherwise the next in the line', () => {
		expect(match.on({ page: 0, x: 100, y: 178 })).toBe(1);
		expect(match.on({ page: 0, x: 81, y: 178 })).toBe(0);
		// After line end: the last word of the line.
		expect(match.on({ page: 0, x: 300, y: 190 })).toBe(3);
		// With tolerance the margin counts.
		expect(match.on({ page: 0, x: 145.5, y: 178 }, 2)).toBe(1);
		// Between lines and on empty pages: nothing.
		expect(match.on({ page: 0, x: 100, y: 150 })).toBeNull();
		expect(match.on({ page: 5, x: 100, y: 178 })).toBeNull();
		expect(match.on({ page: 1, x: 91.5, y: 105 })).toBe(5);
	});

	it('takes all words between anchor and focus, also backwards and across pages', () => {
		expect(sectionBetween(WORDS, 1, 3)).toEqual({ start: 4, end: 29 });
		expect(sectionBetween(WORDS, 3, 1)).toEqual({ start: 4, end: 29 });
		expect(sectionBetween(WORDS, 2, 4)).toEqual({ start: 15, end: 35 });
		expect(sectionBetween(WORDS, 0, 0)).toEqual({ start: 0, end: 3 });
	});

	it('finds the line of a word', () => {
		expect(lineAround(WORDS, 0)).toEqual([0, 1]);
		expect(lineAround(WORDS, 3)).toEqual([2, 3]);
		expect(lineAround(WORDS, 5)).toEqual([4, 5]);
	});
});

describe('Scale', () => {
	const PDF = [1, 1.5, 2, 3];

	it('chooses the smallest image level that is sharp enough', () => {
		expect(imageLevel(1, 1, PDF)).toBe(1);
		expect(imageLevel(1, 2, PDF)).toBe(2);
		expect(imageLevel(1.25, 1, PDF)).toBe(1.5);
		expect(imageLevel(1.25, 2, PDF)).toBe(3);
		expect(imageLevel(0.7, 1, PDF)).toBe(1);
		// Rounding does not force a higher level.
		expect(imageLevel(1.004, 1, PDF)).toBe(1);
	});

	it('takes the largest level if none is sufficient', () => {
		expect(imageLevel(2, 2, PDF)).toBe(3);
		expect(imageLevel(0.42, 2, [0.5, 1])).toBe(1);
		expect(imageLevel(0.2, 2, [1, 0.5])).toBe(0.5);
	});

	it('scales down large images at 100% to A4 length, not PDF pages', () => {
		expect(displaySize({ width: 4032, height: 3024, unit: 'px' })).toEqual({
			width: 842,
			height: 631.5
		});
		expect(displaySize({ width: 3024, height: 4032, unit: 'px' })).toEqual({
			width: 631.5,
			height: 842
		});
		expect(displaySize({ width: 600, height: 400, unit: 'px' })).toEqual({
			width: 600,
			height: 400
		});
		expect(displaySize({ width: 1190, height: 842, unit: 'pt' })).toEqual({
			width: 1190,
			height: 842
		});
	});

	it('converts screen pixels to page units', () => {
		expect(inUnits(12, 2)).toBe(6);
		expect(inUnits(12, 0)).toBe(12);
	});
});
