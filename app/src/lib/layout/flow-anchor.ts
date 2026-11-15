import type { LayoutUnit } from '#lib/sidecar/api.gen.js';
import type { Segment } from '#lib/ui/segments.js';

/**
 * Word: Texteinheit ↔ DOM (spec §5.3, 5.4).
 *
 * docx-preview renders a preview copy with an empty `<span id="unn_u…">`
 * at the start of each layout unit (paragraph). The unit consists of the
 * text nodes from this anchor up to the next anchor – crossing run spans,
 * hyperlinks and page breaks (`lastRenderedPageBreak` splits a paragraph into
 * two `<p>`) – but never into another header or footer.
 *
 * These nodes are matched against the unit's source text. Equality is
 * defined by a small equivalence table:
 *
 * | Source (python-docx) | DOM (docx-preview)          |
 * |----------------------|-----------------------------|
 * | `\t`                 | `\u2003` in tab span        |
 * | `\n` (`w:br`)        | `<br>`                      |
 * | `\n` (`w:cr`)        | nothing                     |
 * | `-` (`w:noBreakHyphen`) | `<wbr>`                 |
 * | nothing              | soft hyphen, `\u200b`       |
 *
 * If matching fails, there is no mapping – the view shows a simplified
 * unit and notes the mismatch. **Never silently wrong.**
 *
 * The mapping provides a DOM offset for every character boundary. This
 * creates a DOM `Range` (replace) and a browser selection position for the
 * offset (highlight). Inserted spots (`[data-source="start:end"]`) form a
 * block: code has no letters belonging to the original.
 *
 * All of this works on any `Document` – in the WebView on the iframe,
 * in tests on jsdom.
 */

export const ANCHOR_PREFIX = 'unn_u';
/** An inserted element represents the source range `start:end`. */
export const SOURCE_ATTRIBUTE = 'data-source';
/** View additions (question number) that do not belong to any text. */
export const ADDITION_ATTRIBUTE = 'data-addition';

const IGNORIERBAR = new Set(['\u00ad', '\u200b']);

export type Piece =
	/** A text node; `offsets[i]` is the source offset before character `i` (length + 1 entries). */
	| { kind: 'text'; node: Text; offsets: number[] }
	/** An element without text that represents a single character in the source (`<br>`, `<wbr>`). */
	| { kind: 'character'; element: Element; start: number; end: number }
	/** An inserted element (code, question, selection …) for a source range. */
	| { kind: 'replacement'; element: Element; start: number; end: number };

export interface Figure {
	/** Source range of the unit (UTF-16, absolute). */
	start: number;
	end: number;
	pieces: Piece[];
}

export interface Point {
	node: Node;
	versatz: number;
}

const ELEMENT = 1;
const TEXT = 3;

function istAnchor(node: Node): boolean {
	return node.nodeType === ELEMENT && (node as Element).id.startsWith(ANCHOR_PREFIX);
}

function istGeschichte(node: Node): boolean {
	if (node.nodeType !== ELEMENT) return false;
	const name = (node as Element).localName;
	return name === 'header' || name === 'footer';
}

/** The area where a unit may continue: its header/footer or the root. */
function boundaryBy(anchor: Element, wurzel: Node): Node {
	return anchor.closest('header, footer') ?? wurzel;
}

/** Next node in document order within `boundary`. */
function next(node: Node, boundary: Node, kinderAuslassen: boolean): Node | null {
	if (!kinderAuslassen && node.firstChild) return node.firstChild;
	let current: Node | null = node;
	while (current && current !== boundary) {
		if (current.nextSibling) return current.nextSibling;
		current = current.parentNode;
	}
	return null;
}

function sourceBy(element: Element): { start: number; end: number } | null {
	const [start, end] = (element.getAttribute(SOURCE_ATTRIBUTE) ?? '').split(':').map(Number);
	return Number.isInteger(start) && Number.isInteger(end) ? { start, end } : null;
}

/**
 * Nodes of a unit in order – text nodes, `<br>`/`<wbr>` and
 * inserted elements (as whole). Stops at the next anchor.
 */
function* unitNode(anchor: Element, wurzel: Node): Generator<Node> {
	const boundary = boundaryBy(anchor, wurzel);
	const inGeschichte = boundary !== wurzel;
	let node = next(anchor, boundary, true);
	while (node) {
		if (istAnchor(node)) return;
		let fromLet = false;
		if (node.nodeType === TEXT) yield node;
		else if (node.nodeType === ELEMENT) {
			const element = node as Element;
			if (element.hasAttribute(SOURCE_ATTRIBUTE)) {
				yield element;
				fromLet = true;
			} else if (
				element.hasAttribute(ADDITION_ATTRIBUTE) ||
				(!inGeschichte && istGeschichte(element))
			) {
				fromLet = true;
			} else if (element.localName === 'br' || element.localName === 'wbr') {
				yield element;
			}
		}
		node = next(node, boundary, fromLet);
	}
}

/**
 * Match the unit anchored at `anchor` against its source text.
 *
 * @param text Source text of the unit
 * @param start Offset of the unit in the full source text
 * @returns the mapping – or `null` if DOM and source do not align
 */
export function abbilden(
	anchor: Element,
	wurzel: Node,
	text: string,
	start: number
): Figure | null {
	const pieces: Piece[] = [];
	let cursor = 0;
	for (const node of unitNode(anchor, wurzel)) {
		if (node.nodeType === TEXT) {
			const data = (node as Text).data;
			const offsets: number[] = [];
			for (const character of data.split('')) {
				// `w:cr` appears in source as a line break, docx-preview does not render it.
				while (text[cursor] === '\n' && character !== '\n') cursor += 1;
				offsets.push(start + cursor);
				if (character === text[cursor] || (character === '\u2003' && text[cursor] === '\t')) {
					cursor += 1;
				} else if (!IGNORIERBAR.has(character)) {
					return null;
				}
			}
			offsets.push(start + cursor);
			pieces.push({ kind: 'text', node: node as Text, offsets });
			continue;
		}
		const element = node as Element;
		const source = sourceBy(element);
		if (source) {
			if (source.start !== start + cursor || source.end < source.start) return null;
			if (source.end > start + text.length) return null;
			pieces.push({ kind: 'replacement', element, ...source });
			cursor = source.end - start;
			continue;
		}
		const erwartet = element.localName === 'br' ? '\n' : '-';
		while (erwartet !== '\n' && text[cursor] === '\n') cursor += 1;
		if (text[cursor] !== erwartet) return null;
		pieces.push({ kind: 'character', element, start: start + cursor, end: start + cursor + 1 });
		cursor += 1;
	}
	while (text[cursor] === '\n') cursor += 1;
	if (cursor !== text.length) return null;
	return { start, end: start + text.length, pieces };
}

function before(element: Node): Point {
	const eltern = element.parentNode!;
	return { node: eltern, versatz: Array.prototype.indexOf.call(eltern.childNodes, element) };
}

function behind(element: Node): Point {
	const point = before(element);
	return { node: point.node, versatz: point.versatz + 1 };
}

/**
 * Convert a DOM position to a source offset within the unit.
 * For a start position the boundary is *before* the first character
 * (the replacement lands in the run where the position begins – like in export);
 * for an end position the boundary is *after* the last character before it.
 */
export function pointFor(figure: Figure, offset: number, page: 'start' | 'end'): Point | null {
	const { pieces } = figure;
	if (page === 'start') {
		for (const piece of pieces) {
			if (piece.kind === 'text') {
				const index = piece.offsets.findIndex(
					(value, i) => value === offset && i < piece.node.length && piece.offsets[i + 1] > value
				);
				if (index >= 0) return { node: piece.node, versatz: index };
				if (piece.offsets[0] > offset) return { node: piece.node, versatz: 0 };
			} else if (piece.start >= offset) return before(piece.element);
		}
	} else {
		for (let i = pieces.length - 1; i >= 0; i--) {
			const piece = pieces[i];
			if (piece.kind === 'text') {
				const offsets = piece.offsets;
				for (let j = offsets.length - 1; j > 0; j--) {
					if (offsets[j] === offset && offsets[j - 1] < offset) {
						return { node: piece.node, versatz: j };
					}
				}
				if (offsets[offsets.length - 1] < offset) {
					return { node: piece.node, versatz: piece.node.length };
				}
			} else if (piece.end <= offset) return behind(piece.element);
		}
	}
	const last = pieces[pieces.length - 1];
	if (!last) return null;
	if (page === 'start' || offset >= figure.end) {
		return last.kind === 'text'
			? { node: last.node, versatz: last.node.length }
			: behind(last.element);
	}
	const first = pieces[0];
	return first.kind === 'text' ? { node: first.node, versatz: 0 } : before(first.element);
}

/** A source range of the unit that will be replaced by an element. */
export interface Part {
	start: number;
	end: number;
	element: Element;
}

/**
 * Replace ranges of the unit with elements.
 * Process from back to front so earlier nodes keep their positions.
 * The element is inserted into the run where the range starts and inherits its formatting –
 * similar to `_replace_docx_runs` in export, which keeps the first run.
 */
export function replace(figure: Figure, parts: readonly Part[]): void {
	const sorted = [...parts].sort((a, b) => b.start - a.start);
	for (const part of sorted) {
		const by = pointFor(figure, part.start, 'start');
		let until = pointFor(figure, part.end, 'end');
		if (!by || !until) continue;
		// Insert first, then delete: otherwise `deleteContents` would pull the range
		// back into the paragraph and leave the element beside the run.
		const start = by.node;
		if (start.nodeType === TEXT) {
			const rest = (start as Text).splitText(by.versatz);
			rest.parentNode!.insertBefore(part.element, rest);
			if (until.node === start) until = { node: rest, versatz: until.versatz - by.versatz };
		} else {
			start.insertBefore(part.element, start.childNodes[by.versatz] ?? null);
			if (until.node === start && until.versatz >= by.versatz) {
				until = { node: start, versatz: until.versatz + 1 };
			}
		}
		const section = part.element.ownerDocument.createRange();
		section.setStartAfter(part.element);
		section.setEnd(until.node, until.versatz);
		section.deleteContents();
	}
}

/**
 * Map a source offset to a browser selection position – `null` if outside the unit.
 * For an inserted element the start or end of the range counts;
 * if it shows the source text itself (question, selection), return the exact position.
 */
export function sourceOffset(
	figure: Figure,
	node: Node,
	versatz: number,
	page: 'start' | 'end'
): number | null {
	for (const piece of figure.pieces) {
		if (piece.kind === 'text' && piece.node === node) {
			return piece.offsets[Math.max(0, Math.min(versatz, piece.offsets.length - 1))];
		}
		if (piece.kind === 'replacement' && piece.element.contains(node)) {
			const content = piece.element.textContent ?? '';
			if (content.length === piece.end - piece.start) {
				const document = piece.element.ownerDocument;
				const section = document.createRange();
				section.selectNodeContents(piece.element);
				try {
					section.setEnd(node, versatz);
				} catch {
					return page === 'start' ? piece.start : piece.end;
				}
				return piece.start + section.toString().length;
			}
			if (page === 'start') return versatz > 0 && content ? piece.end : piece.start;
			return versatz <= 0 && content ? piece.start : piece.end;
		}
	}
	// Position between nodes (element and child index): first piece after it.
	for (const piece of figure.pieces) {
		const start = piece.kind === 'text' ? piece.node : piece.element;
		const document = start.ownerDocument!;
		const section = document.createRange();
		section.setStartBefore(start);
		try {
			if (section.comparePoint(node, versatz) <= 0) {
				return piece.kind === 'text' ? piece.offsets[0] : piece.start;
			}
		} catch {
			return null;
		}
	}
	return figure.pieces.length ? figure.end : null;
}

/** The text the unit displays in the DOM – codes as code, tabs as `\t`. */
export function displayedText(figure: Figure): string {
	return figure.pieces
		.map((piece) => {
			if (piece.kind === 'text') return piece.node.data.replaceAll('\u2003', '\t');
			if (piece.kind === 'character') return piece.element.localName === 'br' ? '\n' : '-';
			return piece.element.textContent ?? '';
		})
		.join('');
}

/**
 * Raw text of a unit as shown by the DOM – no alignment with source text.
 * Used for comparing with the rendering of the saved file.
 */
export function unitRawText(anchor: Element, wurzel: Node): string {
	let text = '';
	for (const node of unitNode(anchor, wurzel)) {
		if (node.nodeType === TEXT) text += (node as Text).data.replaceAll('\u2003', '\t');
		else {
			const element = node as Element;
			if (element.hasAttribute(SOURCE_ATTRIBUTE)) text += element.textContent ?? '';
			else text += element.localName === 'br' ? '\n' : '-';
		}
	}
	return text.replaceAll('\u00ad', '').replaceAll('\u200b', '');
}

/**
 * Simplify a unit: its DOM content is replaced by plain text directly after the anchor.
 * Paragraph formatting stays; mismatched parts disappear instead of being misplaced.
 */
export function vereinfachen(anchor: Element, wurzel: Node, text: string): void {
	const weg: Node[] = [];
	for (const node of unitNode(anchor, wurzel)) weg.push(node);
	for (const node of weg) node.parentNode?.removeChild(node);
	const document = anchor.ownerDocument;
	const parts = text.split('\n');
	const fragment = document.createDocumentFragment();
	parts.forEach((line, index) => {
		if (index > 0) fragment.appendChild(document.createElement('br'));
		if (line) fragment.appendChild(document.createTextNode(line.replaceAll('\t', '\u2003')));
	});
	anchor.after(fragment);
}

/** All instances of the anchor for a unit (headers and footers appear on each page). */
export function anchorElements(wurzel: ParentNode, anchor: string): Element[] {
	return [...wurzel.querySelectorAll(`[id="${anchor}"]`)];
}

/** Unit that contains a given source offset (binary search over starts). */
export function unitOn(units: readonly LayoutUnit[], offset: number): number {
	let bottom = 0;
	let top = units.length - 1;
	let match = -1;
	while (bottom <= top) {
		const middle = (bottom + top) >> 1;
		if (units[middle].start <= offset) {
			match = middle;
			bottom = middle + 1;
		} else top = middle - 1;
	}
	return match;
}

/** A segment clipped to a unit. */
export interface UnitPart {
	segment: Segment;
	start: number;
	end: number;
	/** The text the element displays. */
	text: string;
	/** First part of a location carries operation and number. */
	first: boolean;
}

/**
 * Distribute marked segments (everything except `plain`) across units.
 * A location spanning two units (selection across paragraphs) is clipped:
 * each part shows its source text; code appears in the first part,
 * the rest stay empty – the export only stores locations within a paragraph.
 */
export function partsPerUnit(
	units: readonly LayoutUnit[],
	segments: readonly Segment[],
	sourceText: string
): Map<number, UnitPart[]> {
	const parts = new Map<number, UnitPart[]>();
	for (const segment of segments) {
		const source = segment.source;
		if (segment.kind === 'plain' || !source || source.end <= source.start) continue;
		const showsSource = segment.text === sourceText.slice(source.start, source.end);
		let first = true;
		for (let i = Math.max(0, unitOn(units, source.start)); i < units.length; i++) {
			const unit = units[i];
			if (unit.start >= source.end) break;
			const start = Math.max(source.start, unit.start);
			const end = Math.min(source.end, unit.end);
			if (end <= start) continue;
			const text = showsSource ? sourceText.slice(start, end) : first ? segment.text : '';
			const list = parts.get(i) ?? [];
			list.push({ segment, start, end, text, first });
			parts.set(i, list);
			first = false;
		}
	}
	return parts;
}
