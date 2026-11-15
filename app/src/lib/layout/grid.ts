import type { LayoutCellStyle, LayoutSheet } from '#lib/sidecar/api.gen.js';
import type { Segment } from '#lib/ui/segments.js';

/**
 * Table layout calculations for review – grid positions, virtualisation window,
 * merged cells, cell segments and stylesheet. Rendering occurs in `TabellenViewer.svelte`.
 *
 * Sizes are px at 100 %; zoom is applied via CSS `zoom` on the grid.
 */

/** `[Zeile, Spalte, Anzeige, Stil, start, end]` – like `LayoutCell` from the sidecar. */
export type Cell = readonly [number, number, string, number, number, number];

export const LINE = 0;
export const COLUMN = 1;
export const DISPLAY = 2;
export const STYLE = 3;
export const START = 4;
export const END = 5;

/** Number of lines per fetch; the sidecar processes blocks of equal size. */
export const BLOCK_LINES = 500;
/** Height of column headers (A, B, C) and width of row numbers, px at 100 %. */
export const HEADER_HEIGHT = 20;
export const HEADER_WIDTH = 44;

/**
 * One axis (rows or columns): positions as prefix sums, hidden items have size 0.
 * For 10 000 rows this yields 10 001 numbers – binary search for look-up.
 */
export class Axis {
	readonly #start: Float64Array;

	constructor(sizes: ArrayLike<number>) {
		this.#start = new Float64Array(sizes.length + 1);
		for (let i = 0; i < sizes.length; i++) {
			this.#start[i + 1] = this.#start[i] + Math.max(0, sizes[i]);
		}
	}

	get num(): number {
		return this.#start.length - 1;
	}

	/** Total length. */
	get length(): number {
		return this.#start[this.#start.length - 1];
	}

	/** Start position of element `i` (for `i = count` the end of the axis). */
	pos(i: number): number {
		return this.#start[Math.max(0, Math.min(i, this.num))];
	}

	size(i: number): number {
		return this.pos(i + 1) - this.pos(i);
	}

	/** Index of the element at pixel position `px` (clamped to the axis). */
	on(px: number): number {
		let bottom = 0;
		let top = this.num;
		while (bottom < top) {
			const middle = (bottom + top) >> 1;
			if (this.#start[middle + 1] <= px) bottom = middle + 1;
			else top = middle;
		}
		return Math.min(bottom, Math.max(0, this.num - 1));
	}

	/** Elements intersecting `[from, to)` plus `margin` on both sides: `[first, last]`. */
	viewport(by: number, until: number, rand = 0): [number, number] {
		if (this.num === 0) return [0, -1];
		const first = Math.max(0, this.on(by) - rand);
		const last = Math.min(this.num - 1, this.on(Math.max(by, until - 0.5)) + rand);
		return [first, last];
	}
}

export function columnsAxis(sheet: LayoutSheet): Axis {
	const hidden = new Set(sheet.hidden_cols ?? []);
	const breiten = sheet.col_widths ?? [];
	return new Axis(
		Array.from({ length: sheet.cols }, (_, i) => (hidden.has(i) ? 0 : (breiten[i] ?? 64)))
	);
}

export function linesAxis(sheet: LayoutSheet): Axis {
	const hidden = new Set(sheet.hidden_rows ?? []);
	const heights = sheet.row_heights ?? {};
	const standard = sheet.default_row_height ?? 20;
	return new Axis(
		Array.from({ length: sheet.rows }, (_, i) => (hidden.has(i) ? 0 : (heights[i] ?? standard)))
	);
}

/** Excel column name: 0 → A, 25 → Z, 26 → AA. */
export function columnsName(index: number): string {
	let name = '';
	for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) {
		name = String.fromCharCode(65 + ((n - 1) % 26)) + name;
	}
	return name;
}

/* -- Merged cells ----------------------------------------------------- */

export type Verbund = readonly [number, number, number, number];

/**
 * Merged cells: the top-left cell holds content and area; the others are not drawn.
 * A merged region that extends into the viewport is drawn even if its anchor cell is outside.
 */
export class Verbunde {
	readonly #anchor = new Map<string, Verbund>();
	readonly #list: readonly Verbund[];

	constructor(list: readonly Verbund[]) {
		this.#list = list;
		for (const verbund of list) this.#anchor.set(`${verbund[0]}:${verbund[1]}`, verbund);
	}

	/** The group whose anchor is `(line, column)`. */
	anchor(line: number, column: number): Verbund | undefined {
		return this.#anchor.get(`${line}:${column}`);
	}

	/** Is `(line, column)` inside a group but not its anchor? */
	covered(line: number, column: number): boolean {
		return this.#list.some(
			([z0, s0, z1, s1]) =>
				line >= z0 && line <= z1 && column >= s0 && column <= s1 && (line !== z0 || column !== s0)
		);
	}

	/** Groups that intersect the window while their anchor lies outside. */
	hereinragend(lines: [number, number], columns: [number, number]): Verbund[] {
		return this.#list.filter(
			([z0, s0, z1, s1]) =>
				z1 >= lines[0] &&
				z0 <= lines[1] &&
				s1 >= columns[0] &&
				s0 <= columns[1] &&
				(z0 < lines[0] || s0 < columns[0])
		);
	}
}

/* -- Tiles: what is drawn in the window ------------------------------- */

export interface Tile {
	line: number;
	column: number;
	/** Position and size in the grid, px at 100% (relative to the area, not the sheet). */
	x: number;
	y: number;
	width: number;
	height: number;
	cell?: Cell;
}

export interface Bereichsfenster {
	lines: [number, number];
	columns: [number, number];
}

/**
 * Tiles of a window: each visible cell once, no hidden parts of a group,
 * groups with full area. `origin` is the area position on the sheet (0 for fixed sections).
 */
export function tiles(
	viewport: Bereichsfenster,
	lines: Axis,
	columns: Axis,
	verbunde: Verbunde,
	cells: (line: number) => readonly Cell[] | undefined,
	origin: { x: number; y: number }
): Tile[] {
	const list: Tile[] = [];
	const [z0, z1] = viewport.lines;
	const [s0, s1] = viewport.columns;
	const tile = (line: number, column: number, cell?: Cell): void => {
		const verbund = verbunde.anchor(line, column);
		const zUntil = verbund ? verbund[2] : line;
		const sUntil = verbund ? verbund[3] : column;
		const width = columns.pos(sUntil + 1) - columns.pos(column);
		const height = lines.pos(zUntil + 1) - lines.pos(line);
		if (width <= 0 || height <= 0) return;
		list.push({
			line,
			column,
			x: columns.pos(column) - origin.x,
			y: lines.pos(line) - origin.y,
			width,
			height,
			cell
		});
	};
	const cellIn = (line: number, column: number): Cell | undefined =>
		cells(line)?.find((cell) => cell[COLUMN] === column);

	for (const [zv, sv] of verbunde.hereinragend(viewport.lines, viewport.columns)) {
		tile(zv, sv, cellIn(zv, sv));
	}
	for (let line = z0; line <= z1; line++) {
		if (lines.size(line) <= 0) continue;
		const row = cells(line);
		let i = 0;
		for (let column = s0; column <= s1; column++) {
			while (row && i < row.length && row[i][COLUMN] < column) i++;
			const cell = row && row[i]?.[COLUMN] === column ? row[i] : undefined;
			if (columns.size(column) <= 0 && !verbunde.anchor(line, column)) continue;
			if (verbunde.covered(line, column)) continue;
			tile(line, column, cell);
		}
	}
	return list;
}

/**
 * Tiles and row headers by line – for `role="row"` (A-13): each cell
 * and each header in its line, ascending.
 */
export function inLines<K extends { line: number }, H extends { n: number }>(
	list: readonly K[],
	headers: readonly H[] = []
): { line: number; header?: H; tiles: K[] }[] {
	const lines = new Map<number, { line: number; header?: H; tiles: K[] }>();
	const entry = (line: number) => {
		let found = lines.get(line);
		if (!found) {
			found = { line, tiles: [] };
			lines.set(line, found);
		}
		return found;
	};
	for (const header of headers) entry(header.n).header = header;
	for (const tile of list) entry(tile.line).tiles.push(tile);
	return [...lines.values()].sort((a, b) => a.line - b.line);
}

/* -- Cells by line ------------------------------------------------------ */

/** Cells by line, filled block by block in arrival order. */
export class CellStorage {
	readonly #lines = new Map<number, Cell[]>();
	readonly #blocks = new Set<number>();

	hat(block: number): boolean {
		return this.#blocks.has(block);
	}

	drop(block: number, cells: readonly Cell[]): void {
		this.#blocks.add(block);
		for (const cell of cells) {
			const row = this.#lines.get(cell[LINE]);
			if (row) row.push(cell);
			else this.#lines.set(cell[LINE], [cell]);
		}
		for (const cell of cells) this.#lines.get(cell[LINE])?.sort((a, b) => a[COLUMN] - b[COLUMN]);
	}

	line(line: number): readonly Cell[] | undefined {
		return this.#lines.get(line);
	}

	/** All cells with source text in reading order – for locating a position. */
	*all(): Generator<Cell> {
		for (const line of [...this.#lines.keys()].sort((a, b) => a - b)) {
			yield* this.#lines.get(line)!;
		}
	}
}

/**
 * Loads cells blockwise and stores them per sheet and language. Each block is requested at most once;
 * `loaded` reports each arrived block, `error` reports the first failure (the view then falls back to text).
 */
export class CellLader {
	readonly #storage = new Map<string, CellStorage>();
	readonly #unterwegs = new Map<string, Promise<void>>();
	readonly #load: (
		sheet: number,
		by: number,
		until: number,
		language: string
	) => Promise<readonly Cell[]>;
	readonly #loaded: () => void;
	readonly #error: (reason: unknown) => void;

	constructor(
		load: (sheet: number, by: number, until: number, language: string) => Promise<readonly Cell[]>,
		loaded: () => void,
		error: (reason: unknown) => void
	) {
		this.#load = load;
		this.#loaded = loaded;
		this.#error = error;
	}

	storage(sheet: number, language: string): CellStorage {
		const key = `${sheet}:${language}`;
		let found = this.#storage.get(key);
		if (!found) {
			found = new CellStorage();
			this.#storage.set(key, found);
		}
		return found;
	}

	block(sheet: number, block: number, language: string): Promise<void> {
		const target = this.storage(sheet, language);
		if (target.hat(block)) return Promise.resolve();
		const key = `${sheet}:${language}:${block}`;
		const laufend = this.#unterwegs.get(key);
		if (laufend) return laufend;
		const by = block * BLOCK_LINES;
		const request = this.#load(sheet, by, by + BLOCK_LINES - 1, language)
			.then((cells) => {
				target.drop(block, cells);
				this.#loaded();
			})
			.catch((reason: unknown) => this.#error(reason))
			.finally(() => this.#unterwegs.delete(key));
		this.#unterwegs.set(key, request);
		return request;
	}
}

/** The blocks needed for a viewport together with fixed lines – unique, ascending. */
export function requiredBlocks(
	viewport: [number, number],
	fixLines: number,
	lines: number
): number[] {
	if (lines <= 0) return [];
	const blocks = [...blocksFor(viewport), ...(fixLines > 0 ? blocksFor([0, fixLines - 1]) : [])];
	if (blocks.length === 0) blocks.push(0);
	return [...new Set(blocks)].sort((a, b) => a - b);
}

/** The blocks required by a line window. */
export function blocksFor(lines: [number, number]): number[] {
	if (lines[1] < lines[0]) return [];
	const blocks: number[] = [];
	for (let b = Math.floor(lines[0] / BLOCK_LINES); b <= Math.floor(lines[1] / BLOCK_LINES); b++) {
		blocks.push(b);
	}
	return blocks;
}

/* -- Segments of a cell --------------------------------------------------- */

/** Does the segment exactly match its source text (no code, no placeholder)? */
function sameText(segment: Segment): boolean {
	return (
		segment.source !== undefined &&
		segment.kind !== 'code' &&
		segment.kind !== 'placeholder' &&
		segment.text.length === segment.source.end - segment.source.start
	);
}

/**
 * Segments grouped by source range, for the question "what is in this cell?".
 * Like `inAbsaetze` but for tens of thousands of cells: binary search instead of linear scan.
 * Text is cut at cell boundaries; code resides entirely in the cell where it starts – the export
 * contains a replacement in exactly one cell.
 */
export class SegmentIndex {
	readonly #segments: readonly Segment[];

	constructor(segments: readonly Segment[]) {
		this.#segments = segments
			.filter((segment) => segment.source !== undefined)
			.sort((a, b) => a.source!.start - b.source!.start || a.source!.end - b.source!.end);
	}

	/** Segments in `[start, end)`; empty if nothing is present. */
	in(start: number, end: number): Segment[] {
		const match: Segment[] = [];
		let bottom = 0;
		let top = this.#segments.length;
		while (bottom < top) {
			const middle = (bottom + top) >> 1;
			if (this.#segments[middle].source!.end <= start) bottom = middle + 1;
			else top = middle;
		}
		for (let i = bottom; i < this.#segments.length; i++) {
			const segment = this.#segments[i];
			const source = segment.source!;
			if (source.start >= end) break;
			if (source.end <= start) continue;
			if (!sameText(segment)) {
				if (source.start >= start || match.length === 0) match.push(segment);
				continue;
			}
			const by = Math.max(source.start, start);
			const until = Math.min(source.end, end);
			if (until <= by) continue;
			match.push({
				...segment,
				text: segment.text.slice(by - source.start, until - source.start),
				source: { start: by, end: until }
			});
		}
		return match;
	}
}

/**
 * What a cell displays: the source-text segments when a character is present
 * or when the display already matches the source text (so it can be selected);
 * otherwise the formatted value (`null`).
 */
export function cellContent(cell: Cell, index: SegmentIndex, sourceText: string): Segment[] | null {
	if (cell[START] < 0) return null;
	const segments = index.in(cell[START], cell[END]);
	if (segments.length === 0) return null;
	if (segments.some((segment) => segment.kind !== 'plain')) return segments;
	return cell[DISPLAY] === sourceText.slice(cell[START], cell[END]) ? segments : null;
}

/* -- Styles ----------------------------------------------------------------- */

const COLOR = /^#[0-9A-Fa-f]{6}$/;
const FONT = /^[A-Za-z0-9 -]{1,48}$/;
const STRICH: Record<string, string> = {
	thin: '1px solid',
	hair: '1px solid',
	medium: '2px solid',
	thick: '3px solid',
	double: '3px double',
	dashed: '1px dashed',
	mediumDashed: '2px dashed',
	dotted: '1px dotted',
	dashDot: '1px dashed',
	mediumDashDot: '2px dashed',
	dashDotDot: '1px dotted',
	mediumDashDotDot: '2px dotted',
	slantDashDot: '2px dashed'
};
/**
 * `safe`: If content does not fit, its start remains visible –
 * a right-aligned number would otherwise lose its leading digits (".234,50 €").
 */
const WAAGERECHT = {
	left: 'flex-start',
	center: 'safe center',
	right: 'safe flex-end',
	justify: 'flex-start'
};
const SENKRECHT = { top: 'flex-start', middle: 'safe center', bottom: 'safe flex-end' };

/**
 * Stylesheet for a grid: a class `s{n}` per style, under `wurzel`.
 * Inline styles are disabled; values come from the file and are strictly validated –
 * only colors in `#RRGGBB`, font names using letters, digits, spaces, and hyphens.
 */
export function styleRules(wurzel: string, stile: readonly LayoutCellStyle[]): string {
	return stile
		.map((style, index) => {
			const rules: string[] = [];
			if (style.bold) rules.push('font-weight:700');
			if (style.italic) rules.push('font-style:italic');
			if (style.underline) rules.push('text-decoration:underline');
			if (typeof style.size === 'number' && style.size > 0 && style.size < 200) {
				rules.push(`font-size:${(style.size * 4) / 3}px`);
			}
			if (style.font && FONT.test(style.font)) {
				rules.push(`font-family:"${style.font}",var(--grid-font)`);
			}
			if (style.color && COLOR.test(style.color)) rules.push(`color:${style.color}`);
			if (style.fill && COLOR.test(style.fill)) rules.push(`background-color:${style.fill}`);
			rules.push(`justify-content:${WAAGERECHT[style.align ?? 'left']}`);
			rules.push(`text-align:${style.align ?? 'left'}`);
			rules.push(`align-items:${SENKRECHT[style.valign ?? 'bottom']}`);
			if (style.wrap) rules.push('white-space:pre-wrap', 'overflow-wrap:anywhere');
			for (const page of ['top', 'right', 'bottom', 'left'] as const) {
				const linie = style.border?.[page];
				if (!linie) continue;
				const strich = STRICH[linie[0]] ?? '1px solid';
				const color = COLOR.test(linie[1]) ? linie[1] : 'var(--paper-ink)';
				rules.push(`border-${page}:${strich} ${color}`);
			}
			return `${wurzel} .s${index}{${rules.join(';')}}`;
		})
		.join('\n');
}
