import type {
	LayoutCellStyle,
	LayoutHidden,
	LayoutPage,
	LayoutPagedEngine,
	LayoutPagedResponse,
	LayoutResponse,
	LayoutSheet,
	LayoutUnit
} from '#lib/sidecar/api.gen.js';
import type { Segment } from '#lib/ui/segments.js';

/**
 * Layout map from the sidecar: where a location appears in the rendered document.
 * The actual text is provided by the segments (`segmenteFuer`); no calculation is done here.
 *
 * All boxes are in the page's display space – the page image at scale 1 (PDF points after rotation
 * or image pixels). Offsets are UTF-16 as in `/findings`.
 */

/** `[Seite, x0, y0, x1, y1, start, end, Zeile]` – compact because there are thousands. */
export type Word = readonly [number, number, number, number, number, number, number, number];

export const PAGE = 0;
export const START = 5;
export const END = 6;
export const LINE = 7;

export interface PagesMap {
	pages: readonly LayoutPage[];
	words: WordIndex;
	hidden: readonly LayoutHidden[];
}

/** Map of fixed pages – or `null` if the file is presented as plain text. */
export function pagesMap(answer: LayoutResponse | null | undefined): PagesMap | null {
	if (!answer || answer.kind !== 'pages' || !answer.pages?.length) return null;
	return {
		pages: answer.pages,
		words: new WordIndex(answer.words ?? []),
		hidden: answer.hidden ?? []
	};
}

/** Map of a Word document: units with their anchor. */
export interface FlowMap {
	units: readonly LayoutUnit[];
	/** An Office engine is present, so page-faithful rendering is possible. */
	pageFaithful: boolean;
}

/** Map of a Word document – or `null` if the file appears in another format. */
export function flowMap(answer: LayoutResponse | null | undefined): FlowMap | null {
	if (!answer || answer.kind !== 'flow' || !answer.units?.length) return null;
	return { units: answer.units, pageFaithful: answer.paged === true };
}

/**
 * Page-faithful Word map: the pages from the Office engine as fixed pages.
 * Words carry source-text ranges – the result places a code over the range of its location –
 * so markings, code menu and selection work like with PDFs.
 */
export interface PageFaithfulMap {
	map: PagesMap;
	/** Revision from which the pages originate; page images apply only to this revision. */
	revision: number;
	engine: LayoutPagedEngine;
	/** Source ranges (UTF-16) of paragraphs that the engine placed differently. */
	unlocated: readonly (readonly [number, number])[];
}

export function pageFaithfulMap(answer: LayoutPagedResponse): PageFaithfulMap {
	return {
		map: {
			pages: answer.pages ?? [],
			words: new WordIndex(answer.words ?? []),
			hidden: []
		},
		revision: answer.revision,
		engine: answer.engine,
		unlocated: answer.unplaced ?? []
	};
}

/** Places that the page-faithful view cannot show: their paragraph is missing in the PDF. */
export function unlocatedPlaces(
	segments: readonly Segment[],
	sections: readonly (readonly [number, number])[]
): number {
	if (sections.length === 0) return 0;
	return segments.filter(
		(segment) =>
			segment.kind !== 'plain' &&
			segment.source !== undefined &&
			sections.some(([start, end]) => segment.source!.start < end && segment.source!.end > start)
	).length;
}

/** Map of a table: sheets with dimensions, styles; cells appear per slice. */
export interface GridMap {
	format: 'xlsx' | 'csv';
	sheets: readonly LayoutSheet[];
	stile: readonly LayoutCellStyle[];
}

/** Map of an Excel or CSV file – or `null` if the file appears in another format. */
export function gridMap(answer: LayoutResponse | null | undefined): GridMap | null {
	if (!answer || answer.kind !== 'grid' || !answer.sheets?.length) return null;
	return {
		format: answer.format === 'csv' ? 'csv' : 'xlsx',
		sheets: answer.sheets,
		stile: answer.styles ?? []
	};
}

/**
 * Words by offset for the question “which words lie in this range?”.
 * Words are returned in reading order and do not overlap; starts and ends both increase,
 * so a binary search is sufficient.
 */
export class WordIndex {
	readonly #words: readonly Word[];

	constructor(words: readonly Word[]) {
		this.#words = words;
	}

	get num(): number {
		return this.#words.length;
	}

	all(): readonly Word[] {
		return this.#words;
	}

	/** All words that touch `[start, end)`, in reading order. */
	in(section: { start: number; end: number }): Word[] {
		const match: Word[] = [];
		if (section.end <= section.start) return match;
		for (let i = this.#firstEndAfter(section.start); i < this.#words.length; i++) {
			const word = this.#words[i];
			if (word[START] >= section.end) break;
			match.push(word);
		}
		return match;
	}

	/** Index des ersten Worts, dessen Ende hinter `offset` liegt. */
	#firstEndAfter(offset: number): number {
		let bottom = 0;
		let top = this.#words.length;
		while (bottom < top) {
			const middle = (bottom + top) >> 1;
			if (this.#words[middle][END] <= offset) bottom = middle + 1;
			else top = middle;
		}
		return bottom;
	}
}
