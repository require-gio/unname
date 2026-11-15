import type { PagesMap } from '#lib/layout/map.js';

/** Source offsets are UTF-16, including when replacement text has another length. */
export interface ViewPosition {
	page: number;
	anchor: number | null;
}

function sourceRange(element: Element): [number, number] {
	return (element.getAttribute('data-source') ?? '').split(':').map(Number) as [number, number];
}

function characterRect(element: Element, offset: number): DOMRect | null {
	const document = element.ownerDocument;
	const walker = document.createTreeWalker(element, 4 /* SHOW_TEXT */);
	let remaining = offset;
	for (let node = walker.nextNode(); node; node = walker.nextNode()) {
		const length = node.textContent?.length ?? 0;
		if (remaining < length) {
			const range = document.createRange();
			range.setStart(node, remaining);
			range.setEnd(node, remaining + 1);
			return range.getBoundingClientRect();
		}
		remaining -= length;
	}
	return null;
}

/** First visible word, rather than the start of a potentially very long paragraph. */
export function readTextPosition(root: Element, top: number): ViewPosition | null {
	const elements = [...root.querySelectorAll<HTMLElement>('[data-source]')];
	const element = elements
		.filter(
			(item) => item.getBoundingClientRect().bottom > top && item.getBoundingClientRect().height > 0
		)
		.sort((a, b) => {
			const left = a.getBoundingClientRect();
			const right = b.getBoundingClientRect();
			return Math.max(top, left.top) - Math.max(top, right.top) || left.left - right.left;
		})[0];
	if (!element) return null;
	const [start, end] = sourceRange(element);
	const text = element.textContent ?? '';
	let offset = 0;
	if (end - start === text.length && text.length > 0) {
		let low = 0;
		let high = text.length - 1;
		while (low < high) {
			const middle = (low + high) >>> 1;
			if ((characterRect(element, middle)?.bottom ?? 0) <= top) low = middle + 1;
			else high = middle;
		}
		offset = low;
		while (offset > 0 && /\S/u.test(text[offset - 1])) offset -= 1;
		while (offset < text.length && /\s/u.test(text[offset])) offset += 1;
	}
	const page = element.closest('section.docx');
	const pages = [...root.querySelectorAll('section.docx')];
	return { page: page ? pages.indexOf(page) + 1 : 1, anchor: start + offset };
}

/** Rectangle in the root document's viewport; the caller translates iframe coordinates. */
export function textAnchorRect(
	root: Element,
	anchor: number | null,
	page?: number
): DOMRect | null {
	if (anchor === null) return null;
	const candidates = [...root.querySelectorAll<HTMLElement>('[data-source]')].filter((item) => {
		const [start, end] = sourceRange(item);
		return start <= anchor && anchor < end;
	});
	const section = page === undefined ? undefined : root.querySelectorAll('section.docx')[page - 1];
	const element = candidates.find((item) => section?.contains(item)) ?? candidates[0];
	if (!element) return null;
	const [start, end] = sourceRange(element);
	return end - start === element.textContent?.length
		? characterRect(element, anchor - start)
		: element.getBoundingClientRect();
}

export function readPagePosition(
	root: Element,
	map: PagesMap,
	top: number,
	bottom = Infinity
): ViewPosition | null {
	let fallback: ViewPosition | null = null;
	for (const page of root.querySelectorAll<HTMLElement>('[data-page]')) {
		const frame = page.getBoundingClientRect();
		if (frame.bottom <= top || frame.top >= bottom) continue;
		const index = Number(page.dataset.page);
		const height = map.pages[index]?.height;
		if (!height || !frame.height) continue;
		fallback ??= { page: index + 1, anchor: null };
		const y = Math.max(0, ((top - frame.top) / frame.height) * height);
		const end = ((bottom - frame.top) / frame.height) * height;
		const word = map.words.all().find((item) => item[0] === index && item[4] > y && item[2] < end);
		if (word) return { page: index + 1, anchor: word[5] };
	}
	return fallback;
}

export function restorePagePosition(
	root: Element,
	map: PagesMap,
	scroller: HTMLElement,
	position: ViewPosition
): boolean {
	const candidates =
		position.anchor === null
			? []
			: map.words.all().filter((item) => item[5] <= position.anchor! && position.anchor! < item[6]);
	// Repeated headers have the same source offsets on several pages.
	const word = candidates.find((item) => item[0] === position.page - 1) ?? candidates[0];
	const index = word?.[0] ?? Math.min(Math.max(position.page - 1, 0), map.pages.length - 1);
	const page = root.querySelector<HTMLElement>(`[data-page="${index}"]`);
	if (!page || !page.getBoundingClientRect().height) return false;
	const frame = page.getBoundingClientRect();
	const y = word ? (word[2] / map.pages[index].height) * frame.height : 0;
	scroller.scrollTop += frame.top + y - scroller.getBoundingClientRect().top;
	return true;
}
