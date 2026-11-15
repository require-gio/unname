<script lang="ts">
	import { cn } from '#lib/utils.js';
	import {
		onPage,
		boxenPerPage,
		markings,
		afterPage,
		platzierungsIndex,
		linesBoxen,
		type Box
	} from '#lib/layout/boxen.js';
	import type { PagesMap } from '#lib/layout/map.js';
	import { inUnits } from '#lib/layout/scale.js';
	import {
		sectionBetween,
		WordMatch,
		lineAround,
		type PagesPoint
	} from '#lib/layout/word-selection.js';
	import type { Section } from '#lib/review/state.js';
	import {
		readPagePosition,
		restorePagePosition,
		type ViewPosition
	} from '#lib/review/view-position.js';
	import type { PlacementOut } from '#lib/sidecar/api.gen.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import type { Segment } from '#lib/ui/segments.js';
	import Page from './Page.svelte';

	/**
	 * Fixed pages – PDF, scan, image – displayed as they appear.
	 *
	 * - **Result:** page image with overlay from the placement plan of the Apply response –
	 *   exact area and replacement text where the export places them. Open questions stay readable.
	 * - **Original:** page image with markings.
	 * - **Side-by-side:** for each page, original on the left, result on the right; pages share height
	 *   and scroll together.
	 *
	 * Marking works word-wise: drag, double-click (word), triple-click (line), Shift-click to extend.
	 * The output is a source range similar to a browser text selection.
	 *
	 * Segments define a location; the layout map shows where it is; the placement plan shows the result.
	 * This component only handles the mapping, not the geometry.
	 */
	let {
		map,
		mode,
		original,
		result,
		placements,
		active,
		fill = false,
		scale,
		onsegment,
		onmark,
		load,
		article = $bindable()
	}: {
		map: PagesMap;
		mode: 'result' | 'original' | 'sideBySide';
		/** Segments of the original (with selection highlight). */
		original: readonly Segment[];
		/** Segments of the result; without Apply response same as `original`. */
		result: readonly Segment[];
		placements?: readonly PlacementOut[];
		active?: string;
		fill?: boolean;
		scale?: unknown;
		onsegment?: (segment: Segment, element: SVGElement) => void;
		/** A word-wise selection is ready: section and rectangle for the menu. */
		onmark?: (section: Section, rect: DOMRect) => void;
		load: (page: number, level: number, signal: AbortSignal) => Promise<ImageBitmap>;
		article?: HTMLElement;
	} = $props();

	const words = $derived(map.words.all());
	const match = $derived(new WordMatch(words));
	const plan = $derived(platzierungsIndex(placements));
	const links = $derived(markings(original, map.words));
	const right = $derived(mode === 'original' ? links : markings(result, map.words, plan));
	const hidden = $derived(boxenPerPage(map.hidden, map.words));

	export function readPosition(top: number, bottom?: number): ViewPosition | null {
		return article ? readPagePosition(article, map, top, bottom) : null;
	}

	export function restorePosition(scroller: HTMLElement, position: ViewPosition): boolean {
		return article ? restorePagePosition(article, map, scroller, position) : false;
	}

	/* -- Word-level marking ----------------------------------- */

	/** When screen pixels exceed this count, a click turns into a drag. */
	const ZIEHSCHWELLE = 4;

	let ziehen = $state<{
		anchor: number;
		focus: number;
		x: number;
		y: number;
		active: boolean;
	} | null>(null);
	/** Where the last marking started – Shift+Click extends from there. */
	let lastAnchor: number | null = null;
	/** After a drag over a spot: its click is not treated as a menu click. */
	let clickLock = false;

	const selectionBoxen = $derived(
		ziehen?.active
			? afterPage(
					linesBoxen(
						words.slice(
							Math.min(ziehen.anchor, ziehen.focus),
							Math.max(ziehen.anchor, ziehen.focus) + 1
						)
					)
				)
			: new Map<number, Box[]>()
	);

	/** Pointer position in the display area of the page below – or `null` if not applicable. */
	function pointFrom(event: MouseEvent): { point: PagesPoint; toleranz: number } | null {
		if (!(event.target instanceof Element)) return null;
		const element = event.target.closest<HTMLElement>('[data-page]');
		const svg = element?.querySelector('svg');
		if (!element || !svg) return null;
		const index = Number(element.dataset.page);
		const page = map.pages[index];
		if (!page) return null;
		const frame = svg.getBoundingClientRect();
		const pxPerUnit = frame.width / page.width;
		if (pxPerUnit <= 0) return null;
		return {
			point: {
				page: index,
				x: (event.clientX - frame.left) / pxPerUnit,
				y: (event.clientY - frame.top) / pxPerUnit
			},
			toleranz: inUnits(3, pxPerUnit)
		};
	}

	function wordUnder(event: MouseEvent): number | null {
		const place = pointFrom(event);
		return place ? match.on(place.point, place.toleranz) : null;
	}

	/** Bottom line box of the region in screen coordinates – the menu attaches here. */
	function rectBy(a: number, b: number): DOMRect | null {
		const boxen = linesBoxen(words.slice(Math.min(a, b), Math.max(a, b) + 1));
		const last = boxen[boxen.length - 1];
		const svg = article?.querySelector(`[data-page="${last?.page}"] svg`);
		const page = last ? map.pages[last.page] : undefined;
		if (!last || !svg || !page) return null;
		const frame = svg.getBoundingClientRect();
		const k = frame.width / page.width;
		return new DOMRect(
			frame.left + last.x0 * k,
			frame.top + last.y0 * k,
			(last.x1 - last.x0) * k,
			(last.y1 - last.y0) * k
		);
	}

	function mark(a: number, b: number): void {
		lastAnchor = a;
		const rect = rectBy(a, b);
		if (!onmark || !rect) return;
		onmark(sectionBetween(words, a, b), rect);
	}

	function runter(event: PointerEvent): void {
		clickLock = false;
		if (event.button !== 0 || event.ctrlKey || event.metaKey || event.altKey) return;
		const index = wordUnder(event);
		if (index === null) return;
		if (event.shiftKey && lastAnchor !== null) {
			// Open only on release: pressing would close an open menu (click elsewhere),
			// otherwise the new menu would close immediately.
			event.preventDefault();
			ziehen = {
				anchor: lastAnchor,
				focus: index,
				x: event.clientX,
				y: event.clientY,
				active: true
			};
			return;
		}
		ziehen = { anchor: index, focus: index, x: event.clientX, y: event.clientY, active: false };
	}

	function move(event: PointerEvent): void {
		if (!ziehen) return;
		if (!ziehen.active) {
			if (Math.hypot(event.clientX - ziehen.x, event.clientY - ziehen.y) < ZIEHSCHWELLE) return;
			ziehen.active = true;
		}
		const index = wordUnder(event);
		if (index !== null) ziehen.focus = index;
	}

	function high(): void {
		const done = ziehen;
		ziehen = null;
		if (!done?.active) return;
		clickLock = true;
		mark(done.anchor, done.focus);
	}

	function clickAbfangen(event: MouseEvent): void {
		if (!clickLock) return;
		clickLock = false;
		event.stopPropagation();
		event.preventDefault();
	}

	/**
	 * Double-click selects the word; triple-click selects the line – not a single position.
	 * Counting happens on mouseup: a click is suppressed if the press closed an open menu.
	 */
	function multipleClick(event: MouseEvent): void {
		if (event.detail < 2) return;
		if (event.target instanceof Element && event.target.closest('[role="button"]')) return;
		const index = wordUnder(event);
		if (index === null) return;
		if (event.detail === 2) mark(index, index);
		else {
			const [by, until] = lineAround(words, index);
			mark(by, until);
		}
	}
</script>

<!-- Mouse pointer control; keyboard shortcut for adding a manual entry is Alt+M. -->
<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<article
	bind:this={article}
	data-column="pages"
	class="flex flex-col gap-4"
	onpointerdown={runter}
	onpointermove={move}
	onpointerup={high}
	onpointercancel={() => (ziehen = null)}
	onclickcapture={clickAbfangen}
	onmouseup={multipleClick}
>
	{#if mode === 'sideBySide'}
		<div
			aria-hidden="true"
			class="grid grid-cols-2 gap-4 font-sans text-fine font-semibold tracking-[0.04em] text-muted uppercase"
		>
			<span>{t('review.column.original')}</span>
			<span>{t('review.column.result')}</span>
		</div>
	{/if}
	{#each map.pages as page (page.index)}
		{@const number = { n: page.index + 1, total: map.pages.length }}
		<div class={cn(mode === 'sideBySide' && 'grid grid-cols-2 items-start gap-4')}>
			{#if mode === 'sideBySide'}
				<Page
					{page}
					total={map.pages.length}
					title={t('review.page.original', number)}
					markings={onPage(links, page.index)}
					hidden={hidden.get(page.index) ?? []}
					selection={selectionBoxen.get(page.index)}
					{active}
					fill={fill || mode === 'sideBySide'}
					{scale}
					load={(level, signal) => load(page.index, level, signal)}
				/>
			{/if}
			<Page
				{page}
				total={map.pages.length}
				title={mode === 'sideBySide' ? t('review.page.result', number) : undefined}
				markings={onPage(right, page.index)}
				hidden={hidden.get(page.index) ?? []}
				selection={selectionBoxen.get(page.index)}
				{active}
				fill={fill || mode === 'sideBySide'}
				{scale}
				{onsegment}
				load={(level, signal) => load(page.index, level, signal)}
			/>
		</div>
	{/each}
</article>
