/**
 * Page breaking: docx-preview only breaks pages at
 * stored breaks – hard page breaks and the `lastRenderedPageBreak` left by Word.
 * If those are missing (files from LibreOffice, programs, never saved in Word),
 * the first page can grow arbitrarily.
 *
 * `umbrechen()` measures each page after rendering. When a page overflows,
 * blocks starting with the first that no longer fits are moved to a new page
 * with the same dimensions, header and footer. Tables are split between rows,
 * never inside a row span. A paragraph moves as a whole – only Word itself
 * splits line-wise (`lastRenderedPageBreak`).
 *
 * Nodes themselves are moved, not copies: anchors and text stay the same,
 * `fluss-anker.ts` maps them as usual. Header and footer appear as copies per
 * page – just like docx-preview does.
 */

/** Vertical position of a block on the page, in px. */ export interface Block {
	top: number;
	bottom: number;
}

/** A table row; `until` is the last row that a row-span from this row reaches. */
export interface Line extends Block {
	until: number;
}

/** Browser rounding tolerance: how far a block may overhang. */
const TOLERANZ_PX = 1;
/** Guard against infinite loops for blocks that never fit on a page. */
const AT_MOST_PAGES = 5000;

/**
 * Index of the first block that exceeds `boundary` – or `null` if all fit.
 * The first block of a page never moves: it already fit best.
 */
export function hyphenationPoint(blocks: readonly Block[], boundary: number): number | null {
	const index = blocks.findIndex((block) => block.bottom > boundary + TOLERANZ_PX);
	return index > 0 ? index : null;
}

/**
 * First row that will appear on the next page – or `null` if the table cannot be split.
 * Row spans stay together; the first row always remains on this page.
 */
export function linesHyphenationPoint(lines: readonly Line[], boundary: number): number | null {
	const first = hyphenationPoint(lines, boundary);
	if (first === null) return null;
	for (let kandidat = first; kandidat > 0; kandidat--) {
		const verbunden = lines.slice(0, kandidat).some((line) => line.until >= kandidat);
		if (!verbunden) return kandidat;
	}
	return null;
}

/**
 * Break all pages below `wurzel` (docx-preview:
 * `section.docx` with `header`, `article`, `footer`). Run before zoom
 * and after fonts are loaded. Returns the number of new pages.
 */
export function umbrechen(wurzel: HTMLElement): number {
	let fresh = 0;
	let page = wurzel.querySelector<HTMLElement>('section.docx');
	while (page && fresh < AT_MOST_PAGES) {
		if (share(page)) fresh++;
		page = nextPage(page);
	}
	return fresh;
}

function nextPage(page: HTMLElement): HTMLElement | null {
	let element = page.nextElementSibling;
	while (element && !(element.localName === 'section' && element.classList.contains('docx'))) {
		element = element.nextElementSibling;
	}
	return element as HTMLElement | null;
}

/** Split an overflowing page; returns `true` if a new page follows. */
function share(page: HTMLElement): boolean {
	const viewport = page.ownerDocument.defaultView;
	if (!viewport) return false;
	const soll = parseFloat(viewport.getComputedStyle(page).minHeight);
	const ueberhang = page.getBoundingClientRect().height - soll;
	if (!(soll > 0) || ueberhang <= TOLERANZ_PX) return false;

	const contents = [...page.children].filter(
		(kind): kind is HTMLElement => kind.localName === 'article'
	);
	const blocks = contents.flatMap((content) =>
		[...content.children].map((element) => ({ element: element as HTMLElement, content }))
	);
	if (blocks.length === 0) return false;
	const positions = blocks.map(({ element }) => position(element));
	// The page is `ueberhang` too long: move content up by that amount.
	const boundary = Math.max(...positions.map((block) => block.bottom)) - ueberhang;

	let ab = blocks.findIndex((_, i) => positions[i].bottom > boundary + TOLERANZ_PX);
	if (ab < 0) return false;
	const kandidat = blocks[ab].element;
	if (kandidat.localName === 'table' && positions[ab].top < boundary) {
		const rest = shareTable(kandidat as HTMLTableElement, boundary);
		if (rest) {
			kandidat.after(rest);
			blocks.splice(ab + 1, 0, { element: rest, content: blocks[ab].content });
			ab += 1;
		}
	}
	if (ab === 0) return false;

	const fresh = page.cloneNode(false) as HTMLElement;
	const header = page.querySelector(':scope > header');
	const fuss = page.querySelector(':scope > footer');
	if (header) fresh.append(header.cloneNode(true));
	const targets = new Map<HTMLElement, HTMLElement>();
	for (const { element, content } of blocks.slice(ab)) {
		let target = targets.get(content);
		if (!target) {
			target = content.cloneNode(false) as HTMLElement;
			targets.set(content, target);
			fresh.append(target);
		}
		target.append(element);
	}
	if (fuss) fresh.append(fuss.cloneNode(true));
	page.after(fresh);
	return true;
}

/** Split a table; returns a flat copy with the remaining rows. */
function shareTable(table: HTMLTableElement, boundary: number): HTMLTableElement | null {
	// docx-preview attaches rows directly to the table, without `tbody`.
	const lines = [...table.rows];
	const body = lines[0]?.parentElement;
	if (!body || lines.some((line) => line.parentElement !== body)) return null;
	const ab = linesHyphenationPoint(
		lines.map((line, i) => ({
			...position(line),
			until: i + Math.max(0, ...[...line.cells].map((cell) => cell.rowSpan - 1))
		})),
		boundary
	);
	if (ab === null) return null;
	const rest = table.cloneNode(false) as HTMLTableElement;
	for (const kind of table.children) {
		if (kind.localName === 'colgroup') rest.append(kind.cloneNode(true));
	}
	if (body === table) {
		rest.append(...lines.slice(ab));
	} else {
		const newBody = body.cloneNode(false) as HTMLElement;
		newBody.append(...lines.slice(ab));
		rest.append(newBody);
	}
	return rest;
}

function position(element: Element): Block {
	const frame = element.getBoundingClientRect();
	return { top: frame.top, bottom: frame.bottom };
}
