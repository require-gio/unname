import { autoUpdate, computePosition, flip, offset, shift } from '@floating-ui/dom';
import type { Attachment } from 'svelte/attachments';
import type { Anchor } from './selection.js';

/**
 * DOM side of the marking in the review step. The calculation of which original area is meant
 * is testable in `auswahl.ts`; here we only create an anchor from a node and an offset of the
 * browser selection.
 */

export interface DomAnchor extends Anchor {
	/** `data-spalte` of the surrounding area: "einzeln", "original", "ergebnis". */
	column: string;
}

/**
 * Anchor to a position in the browser selection. Returns null if the position is not inside a
 * segment (question number, peek, outside the document).
 */
export function anchorFrom(
	node: Node | null,
	versatz: number,
	wurzel: HTMLElement
): DomAnchor | null {
	if (!node) return null;
	const element = (
		node.nodeType === Node.TEXT_NODE ? node.parentElement : (node as Element)
	)?.closest<HTMLElement>('[data-seg]');
	if (!element || !wurzel.contains(element)) return null;
	const [paragraph, index] = (element.dataset.seg ?? '').split(':').map(Number);
	if (!Number.isInteger(paragraph) || !Number.isInteger(index)) return null;
	const section = document.createRange();
	section.selectNodeContents(element);
	try {
		section.setEnd(node, versatz);
	} catch {
		return null;
	}
	return {
		paragraph,
		index,
		versatz: section.toString().length,
		column: element.closest<HTMLElement>('[data-column]')?.dataset.column ?? 'einzeln'
	};
}

/** Something a floating surface can attach to: an element or a rectangle. */
export type Reference = { getBoundingClientRect(): DOMRect };

/**
 * Make an element hover over a reference (selection menu at the marking).
 * Positioned via Floating UI script – inline styles in markup are prohibited,
 * runtime coordinates are allowed.
 */
export function schweben(
	reference: Reference | null,
	options: { page?: 'top' | 'bottom'; gap?: number; sequences?: Element | null } = {}
): Attachment<HTMLElement> {
	return (element) => {
		if (!reference) return;
		const set = async () => {
			const position = await computePosition(reference, element, {
				strategy: 'fixed',
				placement: options.page ?? 'bottom',
				middleware: [offset(options.gap ?? 10), flip(), shift({ padding: 16 })]
			});
			Object.assign(element.style, { left: `${position.x}px`, top: `${position.y}px` });
		};
		const sequences = options.sequences;
		if (sequences instanceof Element) {
			return autoUpdate(
				{
					getBoundingClientRect: () => reference.getBoundingClientRect(),
					contextElement: sequences
				},
				element,
				() => void set()
			);
		}
		void set();
	};
}

/** A fixed rectangle as reference – the marking may change, the menu stays put. */
export function rectReference(rect: DOMRect, scroller: Element | null): Reference {
	const start = scroller?.scrollTop ?? 0;
	return {
		getBoundingClientRect: () => {
			const verschoben = (scroller?.scrollTop ?? 0) - start;
			return new DOMRect(rect.x, rect.y - verschoben, rect.width, rect.height);
		}
	};
}
