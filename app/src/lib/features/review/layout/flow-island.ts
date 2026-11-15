import { renderAsync } from 'docx-preview';
import { readTextPosition, textAnchorRect, type ViewPosition } from '#lib/review/view-position.js';
import { beschneiden } from '#lib/review/selection.js';
import type { Section } from '#lib/review/state.js';
import {
	abbilden,
	ANCHOR_PREFIX,
	anchorElements,
	replace,
	SOURCE_ATTRIBUTE,
	sourceOffset,
	partsPerUnit,
	vereinfachen,
	ADDITION_ATTRIBUTE,
	type UnitPart
} from '#lib/layout/flow-anchor.js';
import { umbrechen } from '#lib/layout/page-break.js';
import type { LayoutUnit } from '#lib/sidecar/api.gen.js';
import type { Segment } from '#lib/ui/segments.js';
import { missingFonts, fontRules } from './word-fonts.js';

/**
 * A Word island: the preview copy rendered by docx-preview into an
 * `<iframe sandbox="allow-same-origin" srcdoc>` without scripts.
 * Tailwind preflight does not affect Word formatting, and the app does not
 * alter document styles. The app manipulates the DOM from the outside:
 * it replaces each unit (`flow-anchor.ts`), attaches its events, and reads
 * the selection.
 *
 * When a new segment list arrives, only units whose parts changed are
 * re-assigned. Their paragraphs come from an untouched copy that was
 * created after rendering. The file is never re-rendered.
 */

/** Options matching the layout spec: use saved page breaks, no comments. */
const RENDER_OPTIONS = {
	className: 'docx',
	inWrapper: true,
	breakPages: true,
	ignoreLastRenderedPageBreak: false,
	renderHeaders: true,
	renderFooters: true,
	renderFootnotes: false,
	renderEndnotes: false,
	renderComments: false,
	renderChanges: false,
	renderAltChunks: false,
	useBase64URL: true,
	experimental: false
};

/** Tokens required for the island's markings – values come from the app. */
const TOKENS = [
	'tint-3',
	'tint-4',
	'tint-5',
	'tint-ink',
	'tint-ink-strong',
	'tint-line',
	'accent',
	'amber-2',
	'amber-ink',
	'amber-ink-2',
	'amber-dash',
	'amber-strong',
	'on-amber-strong',
	'faint',
	'ai-line',
	'blue-2',
	'green-2',
	'doc-ink',
	'bar',
	'bar-ink',
	'paper',
	'shadow-page',
	'font-sans',
	'font-mono'
];

/**
 * Island styles. Colors are tokens (`var(--…)`) set by `tokenStyle()` from the app.
 * Classes match `SEGMENT_STYLE`; the `#document` selector overrides docx-preview run styles.
 * Fonts are installed, otherwise metric equivalents are bundled (`word-fonts.ts`).
 */
const ISLAND_STYLE = `
html, body { margin: 0; background: transparent; overflow: hidden; }
.docx-wrapper { background: transparent !important; padding: 0 !important; }
.docx-wrapper > section.docx { box-shadow: var(--shadow-page) !important; margin: 0 0 16px !important; background: var(--paper) !important; }
a { color: inherit; text-decoration: inherit; pointer-events: none; }
#document .unn-seg { border-radius: 2px; -webkit-box-decoration-break: clone; box-decoration-break: clone; }
#document button.unn-seg { font: inherit; color: inherit; border: 0; margin: 0; display: inline; text-align: left; vertical-align: baseline; cursor: pointer; position: relative; }
#document button.unn-seg:hover { box-shadow: 0 0 0 1.5px var(--tint-line); }
#document button.unn-seg:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
#document .unn-seg[data-active], #document .unn-seg[data-active]:hover { box-shadow: 0 0 0 2px var(--accent); }
#document .unn-seg.unn-code { background: var(--tint-3); color: var(--tint-ink); font-family: var(--font-mono); font-size: 0.74em; padding: 0.125em 0.375em; white-space: nowrap; vertical-align: 0.06em; }
#document .unn-seg.unn-placeholder { background: var(--tint-3); color: var(--tint-ink); font-family: var(--font-sans); font-size: 0.84em; font-weight: 600; padding: 1px 0.4375em; white-space: nowrap; }
#document .unn-seg.unn-masked-original { background: var(--tint-4); color: var(--tint-ink-strong); padding: 1px 3px; }
#document .unn-seg.unn-question { background: var(--amber-2); color: var(--amber-ink); outline: 1.5px dashed var(--amber-dash); outline-offset: 1px; padding: 1px 3px; }
#document .unn-seg.unn-kept-category { border-bottom: 1.5px dashed var(--faint); }
#document .unn-seg.unn-kept-here { border-bottom: 1.5px dotted var(--faint); }
#document .unn-seg.unn-ai-kept { border-bottom: 1.5px dotted var(--ai-line); }
#document .unn-seg.unn-unknown-code { background: var(--amber-2); color: var(--amber-ink); font-family: var(--font-mono); font-size: 0.74em; outline: 1.5px dashed var(--amber-dash); padding: 0.125em 0.375em; }
#document .unn-seg.unn-selection { background: var(--blue-2); color: var(--doc-ink); }
#document sup[${ADDITION_ATTRIBUTE}] { display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box; min-width: 15px; height: 15px; padding: 0 3px; margin-left: 4px; border-radius: 999px; background: var(--amber-strong); color: var(--on-amber-strong); box-shadow: 0 0 0 1.5px var(--paper); font: 700 9px/1 var(--font-sans); vertical-align: super; }
#document button.unn-seg[data-original]:is(:hover, :focus-visible)::after {
	content: attr(data-original); position: absolute; bottom: calc(100% + 6px); left: 0; z-index: 10;
	padding: 5px 9px; border-radius: 4px; background: var(--bar); color: var(--bar-ink);
	font: 500 12px/1.2 var(--font-sans); white-space: nowrap; pointer-events: none;
}
#document .unn-simplified { outline: 1px dotted var(--faint); outline-offset: 2px; }
`;

/** Empty document for the island; docx-preview renders into `#document`. */
export const ISLAND_SRCDOC = `<!doctype html><html lang="de"><head><meta charset="utf-8"><style>${ISLAND_STYLE}${fontRules()}</style><style id="tokens"></style></head><body><div id="stile"></div><div id="document"></div></body></html>`;

function tokenStyle(): string {
	const values = getComputedStyle(document.documentElement);
	const lines = TOKENS.map((name) => `--${name}: ${values.getPropertyValue(`--${name}`).trim()};`);
	return `:root { ${lines.join(' ')} }`;
}

export interface IslandOptions {
	units: readonly LayoutUnit[];
	sourceText: string;
	/** Right column or sole island: codes are buttons, selection opens the menu. */
	operable: boolean;
	caption: (segment: Segment) => string;
	questionNumber: (number: number) => string;
	originalText: (segment: Segment) => string;
	onsegment?: (segment: Segment, element: HTMLElement) => void;
	/** Mouse up or Shift+Arrow: view reads the selection. */
	onselection?: () => void;
	/** Content height or width changed. */
	onsize?: () => void;
}

interface Exemplar {
	/** Paragraph indices (in `#absaetze`) that the unit spans. */
	paragraphs: number[];
	/** First instance carries control and `data-id` (headers/footers appear multiple times). */
	first: boolean;
}

export class FlowIsland {
	readonly #iframe: HTMLIFrameElement;
	readonly #o: IslandOptions;
	#document: Document | null = null;
	#wurzel: HTMLElement | null = null;
	/** Paragraphs after rendering, untouched; `#absaetze` reflects live state. */
	#template: Element[] = [];
	#paragraphs: Element[] = [];
	#exemplare = new Map<number, Exemplar[]>();
	#key = new Map<number, string>();
	#segments = new WeakMap<Element, Segment>();
	#active: string | undefined;
	#beobachter: MutationObserver | null = null;
	#zoom = 1;
	/** Units that appear simplified because matching failed. */
	simplified = 0;
	/** Pages that required reflow (`seiten-umbruch.ts`). */
	nachgebrochen = 0;
	/** Document fonts missing here with no metrically equivalent replacement. */
	fehlend: string[] = [];

	constructor(iframe: HTMLIFrameElement, options: IslandOptions) {
		this.#iframe = iframe;
		this.#o = options;
	}

	/** Renders preview copy; then creates figures for all units. */
	async render(data: Blob): Promise<void> {
		const document = await this.#loaded();
		this.#document = document;
		const wurzel = document.getElementById('document')!;
		const stile = document.getElementById('stile')!;
		this.#wurzel = wurzel;
		this.#setTokens();
		await renderAsync(data, wurzel, stile, RENDER_OPTIONS);
		// Measure only after fonts load—they determine line metrics.
		void wurzel.offsetHeight;
		await document.fonts?.ready;
		this.nachgebrochen = umbrechen(wurzel);
		this.fehlend = missingFonts(wurzel);

		this.#vereinfachen();
		this.#paragraphs = [...wurzel.querySelectorAll('p')];
		this.#template = this.#paragraphs.map((paragraph) => paragraph.cloneNode(true) as Element);
		const index = new Map(this.#paragraphs.map((paragraph, i) => [paragraph, i]));
		this.#o.units.forEach((unit, i) => {
			const exemplare = anchorElements(wurzel, unit.anchor).map((anchor, n) => {
				const figure = abbilden(anchor, wurzel, this.#text(i), unit.start);
				const paragraphs = new Set<number>();
				const own = anchor.closest('p');
				if (own) paragraphs.add(index.get(own)!);
				for (const piece of figure?.pieces ?? []) {
					const node = piece.kind === 'text' ? piece.node : piece.element;
					const paragraph = node.parentElement?.closest('p');
					if (paragraph && index.has(paragraph)) paragraphs.add(index.get(paragraph)!);
				}
				return { paragraphs: [...paragraphs], first: n === 0 };
			});
			this.#exemplare.set(i, exemplare);
		});
		this.#events(document);
		// When app theme changes, island tokens update.
		this.#beobachter = new MutationObserver(() => this.#setTokens());
		this.#beobachter.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ['class', 'data-theme', 'style']
		});
		void document.fonts?.ready.then(() => this.#o.onsize?.());
	}

	#loaded(): Promise<Document> {
		const iframe = this.#iframe;
		const done = iframe.contentDocument;
		if (done?.readyState === 'complete' && done.getElementById('document')) {
			return Promise.resolve(done);
		}
		return new Promise((ok, error) => {
			iframe.addEventListener(
				'load',
				() => {
					const document = iframe.contentDocument;
					if (document) ok(document);
					else error(new Error('Die Insel hat kein Dokument.'));
				},
				{ once: true }
			);
		});
	}

	#setTokens(): void {
		const style = this.#document?.getElementById('tokens');
		if (style) style.textContent = tokenStyle();
	}

	#text(index: number): string {
		const unit = this.#o.units[index];
		return this.#o.sourceText.slice(unit.start, unit.end);
	}

	/** Units whose DOM doesn't match source text are rendered as plain text in place. */
	#vereinfachen(): void {
		const wurzel = this.#wurzel!;
		const betroffen = new Set<number>();
		this.#o.units.forEach((unit, i) => {
			for (const anchor of anchorElements(wurzel, unit.anchor)) {
				if (abbilden(anchor, wurzel, this.#text(i), unit.start)) continue;
				vereinfachen(anchor, wurzel, this.#text(i));
				anchor.closest('p')?.classList.add('unn-simplified');
				betroffen.add(i);
			}
		});
		this.simplified = betroffen.size;
	}

	/**
	 * Assigns units with the marked segments. Only units whose parts changed are
	 * returned from the template and replaced.
	 */
	show(segments: readonly Segment[], active: string | undefined): void {
		const wurzel = this.#wurzel;
		if (!wurzel) return;
		const parts = partsPerUnit(this.#o.units, segments, this.#o.sourceText);
		let changed = false;
		for (let i = 0; i < this.#o.units.length; i++) {
			const list = parts.get(i) ?? [];
			const key = JSON.stringify(
				list.map((part) => [
					part.segment.kind,
					part.start,
					part.end,
					part.text,
					part.segment.id,
					part.segment.number,
					part.segment.operable,
					part.segment.original,
					part.first
				])
			);
			if ((this.#key.get(i) ?? '[]') === key) continue;
			this.#key.set(i, key);
			this.#belegen(i, list);
			changed = true;
		}
		this.#setActive(active);
		if (changed) this.#o.onsize?.();
	}

	#belegen(index: number, parts: readonly UnitPart[]): void {
		const wurzel = this.#wurzel!;
		const unit = this.#o.units[index];
		for (const exemplar of this.#exemplare.get(index) ?? []) {
			for (const i of exemplar.paragraphs) {
				const frisch = this.#template[i].cloneNode(true) as Element;
				this.#paragraphs[i].replaceWith(frisch);
				this.#paragraphs[i] = frisch;
			}
			if (parts.length === 0) continue;
			const anchor = this.#paragraphs[exemplar.paragraphs[0]]?.querySelector(
				`[id="${unit.anchor}"]`
			);
			if (!anchor) continue;
			const figure = abbilden(anchor, wurzel, this.#text(index), unit.start);
			if (!figure) continue;
			const elements = parts.map((part) => ({
				start: part.start,
				end: part.end,
				element: this.#element(part, exemplar.first)
			}));
			replace(figure, elements);
			parts.forEach((part, n) => {
				const number = part.segment.number;
				if (part.segment.kind !== 'question' || number === undefined || !part.first) return;
				const sup = document.createElement('sup');
				sup.setAttribute(ADDITION_ATTRIBUTE, '');
				sup.setAttribute('aria-label', this.#o.questionNumber(number));
				sup.textContent = String(number);
				elements[n].element.after(sup);
			});
		}
	}

	#element(part: UnitPart, first: boolean): HTMLElement {
		const segment = part.segment;
		const button =
			this.#o.operable && first && part.first && segment.operable === true && !!this.#o.onsegment;
		// Create in the app document so prototypes and instanceof stay the same.
		const element = document.createElement(button ? 'button' : 'span');
		element.className = `unn-seg unn-${segment.kind}`;
		element.setAttribute(SOURCE_ATTRIBUTE, `${part.start}:${part.end}`);
		element.textContent = part.text;
		if (first && part.first && segment.id !== undefined) element.dataset.id = segment.id;
		if (button) {
			element.setAttribute('type', 'button');
			element.setAttribute('aria-haspopup', 'menu');
			element.setAttribute('aria-expanded', 'false');
			element.setAttribute('aria-label', this.#o.caption(segment));
			if (segment.original !== undefined) {
				element.dataset.original = this.#o.originalText(segment);
			}
			this.#segments.set(element, segment);
		}
		return element;
	}

	#setActive(active: string | undefined): void {
		this.#active = active;
		for (const element of this.#wurzel?.querySelectorAll<HTMLElement>('.unn-seg[data-id]') ?? []) {
			const on = active !== undefined && element.dataset.id === active;
			element.toggleAttribute('data-active', on);
			if (element.localName === 'button') element.setAttribute('aria-expanded', String(on));
		}
	}

	setActive(active: string | undefined): void {
		if (active !== this.#active) this.#setActive(active);
	}

	/**
	 * Island events: click on a code, keyboard and pointer for the app.
	 * Shortcuts (Alt+N, Alt+M, Space) and “click elsewhere” are listened on the
	 * app window – they are forwarded as events on the iframe.
	 */
	#events(document: Document): void {
		document.addEventListener('click', (event) => {
			const target = event.target instanceof Node ? event.target : null;
			const element = (
				target?.nodeType === 1 ? (target as Element) : target?.parentElement
			)?.closest('button.unn-seg');
			if (element) {
				const segment = this.#segments.get(element);
				if (segment) this.#o.onsegment?.(segment, element as HTMLElement);
				return;
			}
			if ((target as Element | null)?.closest?.('a')) event.preventDefault();
		});
		for (const kind of ['keydown', 'keyup'] as const) {
			document.addEventListener(kind, (event) => {
				const target = event.target as Element | null;
				// Space on a code activates the button – not “show original”.
				if (event.code === 'Space' && target?.closest?.('button')) return;
				const next = new KeyboardEvent(kind, {
					key: event.key,
					code: event.code,
					altKey: event.altKey,
					ctrlKey: event.ctrlKey,
					metaKey: event.metaKey,
					shiftKey: event.shiftKey,
					repeat: event.repeat,
					bubbles: true,
					cancelable: true
				});
				this.#iframe.dispatchEvent(next);
				if (next.defaultPrevented) event.preventDefault();
				if (kind === 'keyup' && event.shiftKey) this.#o.onselection?.();
			});
		}
		document.addEventListener('pointerdown', () =>
			this.#iframe.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
		);
		document.addEventListener('pointerup', () => this.#o.onselection?.());
	}

	/**
	 * Fit the island: CSS `zoom` on the island document and iframe size.
	 * The iframe never scrolls itself; the view handles scrolling.
	 * Without `zoom` the page fills the available width (“page width”).
	 * Wide pages make the iframe wider; the view then scrolls horizontally.
	 */
	einpassen(available: number, zoom: number | null): void {
		const html = this.#document?.documentElement;
		const wurzel = this.#wurzel;
		if (!html || !wurzel || available <= 0) return;
		const pages = [...wurzel.querySelectorAll('section.docx')];
		const breiteste = Math.max(0, ...pages.map((page) => page.getBoundingClientRect().width));
		const page = breiteste / this.#zoom;
		const faktor = zoom ?? (page > 0 ? available / page : 1);
		if (faktor !== this.#zoom) {
			this.#zoom = faktor;
			html.style.zoom = String(faktor);
		}
		const height = wurzel.getBoundingClientRect().height;
		this.#iframe.width = String(Math.floor(Math.max(available, page * faktor)));
		this.#iframe.height = String(Math.ceil(height));
	}

	/** An element in the island – for Alt+N and restoring focus. */
	finden(selektor: string): HTMLElement | null {
		return this.#wurzel?.querySelector<HTMLElement>(selektor) ?? null;
	}

	readPosition(top: number): ViewPosition | null {
		return this.#wurzel
			? readTextPosition(this.#wurzel, top - this.#iframe.getBoundingClientRect().top)
			: null;
	}

	restorePosition(scroller: HTMLElement, position: ViewPosition): boolean {
		if (!this.#wurzel) return false;
		const rect =
			textAnchorRect(this.#wurzel, position.anchor, position.page) ??
			this.#wurzel.querySelectorAll('section.docx')[position.page - 1]?.getBoundingClientRect();
		if (!rect) return false;
		scroller.scrollTop +=
			this.#iframe.getBoundingClientRect().top + rect.top - scroller.getBoundingClientRect().top;
		return true;
	}

	/**
	 * Browser selection of the island as source range, with rectangle in the app window.
	 * Returns null when there is no usable selection.
	 */
	selection(): { section: Section; rect: DOMRect } | null {
		const document = this.#document;
		const selection = document?.getSelection();
		if (!document || !selection || selection.isCollapsed || selection.rangeCount === 0) return null;
		const a = this.#offset(selection.anchorNode, selection.anchorOffset, 'start');
		const b = this.#offset(selection.focusNode, selection.focusOffset, 'end');
		if (a === null || b === null) return null;
		const forward = a <= b;
		const start = forward ? a : this.#offset(selection.focusNode, selection.focusOffset, 'start');
		const end = forward ? b : this.#offset(selection.anchorNode, selection.anchorOffset, 'end');
		if (start === null || end === null) return null;
		const section = beschneiden(start, end, this.#o.sourceText);
		if (!section) return null;
		const innen = selection.getRangeAt(0).getBoundingClientRect();
		const frame = this.#iframe.getBoundingClientRect();
		selection.removeAllRanges();
		return {
			section,
			rect: new DOMRect(
				frame.left + this.#iframe.clientLeft + innen.left,
				frame.top + this.#iframe.clientTop + innen.top,
				innen.width,
				innen.height
			)
		};
	}

	/** Source offset for a selection position: via the preceding anchor to the unit. */
	#offset(node: Node | null, versatz: number, page: 'start' | 'end'): number | null {
		const wurzel = this.#wurzel;
		if (!node || !wurzel || !wurzel.contains(node)) return null;
		const anchor = this.#anchorBefore(node, versatz);
		if (!anchor) return null;
		const index = Number(anchor.id.slice(ANCHOR_PREFIX.length));
		const unit = this.#o.units[index];
		if (!unit) return null;
		const figure = abbilden(anchor, wurzel, this.#text(index), unit.start);
		return figure ? sourceOffset(figure, node, versatz, page) : null;
	}

	/** The last anchor before a position – within the same header/footer or in the flow text. */
	#anchorBefore(node: Node, versatz: number): Element | null {
		const wurzel = this.#wurzel!;
		const element = node.nodeType === 1 ? (node as Element) : node.parentElement;
		const geschichte = element?.closest('header, footer') ?? null;
		const section = node.ownerDocument!.createRange();
		section.setStart(node, versatz);
		let match: Element | null = null;
		for (const anchor of wurzel.querySelectorAll(`[id^="${ANCHOR_PREFIX}"]`)) {
			if ((anchor.closest('header, footer') ?? null) !== geschichte) continue;
			if (section.comparePoint(anchor, 0) > 0) break;
			match = anchor;
		}
		return match;
	}

	destroy(): void {
		this.#beobachter?.disconnect();
		this.#beobachter = null;
	}
}
