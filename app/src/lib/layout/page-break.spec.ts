import { describe, expect, it } from 'vitest';
import { hyphenationPoint, linesHyphenationPoint } from './page-break.js';

const block = (top: number, bottom: number) => ({ top, bottom });

describe('hyphenation point', () => {
	it('finds the first block that crosses the limit', () => {
		expect(hyphenationPoint([block(0, 40), block(40, 80), block(80, 120)], 100)).toBe(2);
	});

	it('leaves everything that fits, even with rounding', () => {
		expect(hyphenationPoint([block(0, 40), block(40, 100.6)], 100)).toBeNull();
	});

	it('never moves the first block of a page', () => {
		expect(hyphenationPoint([block(0, 400), block(400, 420)], 100)).toBeNull();
	});
});

describe('line hyphenation point', () => {
	const line = (top: number, bottom: number, until: number) => ({ top, bottom, until });

	it('splits between lines', () => {
		const lines = [line(0, 20, 0), line(20, 40, 1), line(40, 60, 2), line(60, 80, 3)];
		expect(linesHyphenationPoint(lines, 50)).toBe(2);
	});

	it('keeps line groups together', () => {
		// Line 1 extends to line 2: split occurs before it.
		const lines = [line(0, 20, 0), line(20, 40, 2), line(40, 60, 2), line(60, 80, 3)];
		expect(linesHyphenationPoint(lines, 50)).toBe(1);
	});

	it('gives up when a group covers the whole table', () => {
		const lines = [line(0, 20, 2), line(20, 40, 2), line(40, 60, 2)];
		expect(linesHyphenationPoint(lines, 30)).toBeNull();
	});

	it('fits everything, nothing to split', () => {
		expect(linesHyphenationPoint([line(0, 20, 0), line(20, 40, 1)], 100)).toBeNull();
	});
});
