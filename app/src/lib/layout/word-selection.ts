import { END, PAGE, START, LINE, type Word } from './map.js';

/**
 * Word-level selection in the page view.
 *
 * There is no browser text selection over an image, and an invisible text layer
 * like in pdf.js would cause jumps in columns and tables. The layout map's word
 * list is the source of truth: pressing on word i and dragging to word j selects
 * all words between them in reading order (source order). The resulting UTF-16
 * range matches the one from `quellBereich()` in the text.
 *
 * Words are addressed by their index in the word list.
 */

/** A point in the display area of a page. */
export interface PagesPoint {
	page: number;
	x: number;
	y: number;
}

/** Determines which word lies under the pointer. */
export class WordMatch {
	readonly #words: readonly Word[];
	readonly #perPage = new Map<number, number[]>();

	constructor(words: readonly Word[]) {
		this.#words = words;
		words.forEach((word, index) => {
			const list = this.#perPage.get(word[PAGE]);
			if (list) list.push(index);
			else this.#perPage.set(word[PAGE], [index]);
		});
	}

	/**
	 * The word under the point; if between words or at line end, the next word
	 * on the same line. `toleranz` is in display-space units – about 3 screen
	 * pixels.
	 */
	on(point: PagesPoint, toleranz = 0): number | null {
		const candidates = this.#perPage.get(point.page);
		if (!candidates) return null;
		let next: number | null = null;
		let gap = Infinity;
		for (const index of candidates) {
			const [, x0, y0, x1, y1] = this.#words[index];
			if (point.y < y0 - toleranz || point.y > y1 + toleranz) continue;
			if (point.x >= x0 - toleranz && point.x <= x1 + toleranz) return index;
			const width = point.x < x0 ? x0 - point.x : point.x - x1;
			if (width < gap) {
				gap = width;
				next = index;
			}
		}
		return next;
	}
}

/** Source range covering all words from `a` to `b` (any direction). */
export function sectionBetween(
	words: readonly Word[],
	a: number,
	b: number
): { start: number; end: number } {
	const first = words[Math.min(a, b)];
	const last = words[Math.max(a, b)];
	return { start: first[START], end: last[END] };
}

/** First and last word of the line containing word `i` (triple-click). */
export function lineAround(words: readonly Word[], i: number): [number, number] {
	const page = words[i][PAGE];
	const line = words[i][LINE];
	const same = (j: number) => words[j][PAGE] === page && words[j][LINE] === line;
	let by = i;
	let until = i;
	while (by > 0 && same(by - 1)) by--;
	while (until < words.length - 1 && same(until + 1)) until++;
	return [by, until];
}
