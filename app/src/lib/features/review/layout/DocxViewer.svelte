<script lang="ts">
	import { untrack } from 'svelte';
	import type { Attachment } from 'svelte/attachments';
	import { cn } from '#lib/utils.js';
	import type { FlowMap } from '#lib/layout/map.js';
	import type { Section } from '#lib/review/state.js';
	import type { ViewPosition } from '#lib/review/view-position.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { segmentCaption } from '#lib/ui/caption.js';
	import type { Segment } from '#lib/ui/segments.js';
	import { FlowIsland, ISLAND_SRCDOC } from './flow-island.js';

	/**
	 * Rendered preview copy from the sidecar, using docx-preview inside an iframe island
	 * without scripts – includes pages, tables, headers and footers.
	 *
	 * - **Result:** Each location is replaced in the DOM at the run that performs the export.
	 *   Open questions remain readable (Bernstein).
	 * - **Original:** same locations marked.
	 * - **Side-by-side:** two islands, original on the left, result on the right. Pages share the
	 *   same height (page size from the file) so both columns scroll together page by page.
	 *
	 * Pages that become too long due to missing line breaks are split by `seiten-umbruch.ts`;
	 * if a font lacks a metrically equivalent replacement, the view reports it.
	 *
	 * Marking follows the browser selection; the island creates a source range via unit anchors.
	 * Uncertain matches are shown in a simplified form and labeled – never silently wrong.
	 */
	let {
		map,
		sourceText,
		mode,
		original,
		result,
		active,
		fill = false,
		scale = 100,
		onsegment,
		onmark,
		onerror,
		load,
		article = $bindable()
	}: {
		map: FlowMap;
		sourceText: string;
		mode: 'result' | 'original' | 'sideBySide';
		original: readonly Segment[];
		result: readonly Segment[];
		active?: string;
		fill?: boolean;
		/** Zoom in percent; with `fill` the page width is used. */
		scale?: number;
		onsegment?: (segment: Segment, element: HTMLElement) => void;
		onmark?: (section: Section, rect: DOMRect) => void;
		/** Called when the preview cannot be loaded or rendered; fallback to plain text view. */
		onerror?: () => void;
		load: (signal: AbortSignal) => Promise<Blob>;
		article?: HTMLElement;
	} = $props();

	const sideBySide = $derived(mode === 'sideBySide');
	let main = $state<FlowIsland | null>(null);
	let links = $state<FlowIsland | null>(null);
	let simplified = $state(0);
	/** Fonts lacking metric-equivalent replacements (e.g., Aptos) are missing here. */
	let fehlend = $state<string[]>([]);
	let width = $state(0);

	// The preview copy is fetched once per file; both islands render the same.
	const abort = new AbortController();
	let data: Promise<Blob> | null = null;
	$effect(() => () => abort.abort());

	function island(role: 'main' | 'links'): Attachment<HTMLIFrameElement> {
		// No external dependencies: the island lives as long as its iframe.
		return (iframe) => untrack(() => create(iframe, role));
	}

	function create(iframe: HTMLIFrameElement, role: 'main' | 'links'): () => void {
		const units = map.units;
		const text = sourceText;
		const fresh = new FlowIsland(iframe, {
			units,
			sourceText: text,
			operable: role === 'main',
			caption: segmentCaption,
			questionNumber: (n) => t('question.number', { n }),
			originalText: (segment) =>
				t('code.original', { text: segment.original || t('code.removed') }),
			onsegment: role === 'main' ? (segment, element) => onsegment?.(segment, element) : undefined,
			onselection: role === 'main' ? reportSelection : undefined,
			onsize: () => einpassen()
		});
		let lebt = true;
		data ??= load(abort.signal);
		data
			.then((blob) => fresh.render(blob))
			.then(() => {
				if (!lebt) return;
				if (role === 'main') {
					main = fresh;
					fehlend = fresh.fehlend;
				} else links = fresh;
				simplified = Math.max(simplified, fresh.simplified);
			})
			.catch(() => {
				if (lebt && !abort.signal.aborted) onerror?.();
			});
		return () => {
			lebt = false;
			fresh.destroy();
			if (role === 'main') main = null;
			else links = null;
		};
	}

	/**
	 * docx-preview uses CSS inches (96 px). 100 % corresponds to PDF pages:
	 * 1 pt = 1 px, so an A4 page has the same size in both renderings.
	 */
	const PT_PER_PX = 0.75;

	/** Adjust zoom and island size based on column width, zoom level, and appearance. */
	function einpassen(): void {
		const column = sideBySide ? (width - 16) / 2 : width;
		const zoom = fill || sideBySide ? null : (scale / 100) * PT_PER_PX;
		main?.einpassen(column, zoom);
		links?.einpassen(column, zoom);
	}

	$effect(() => {
		void width;
		void scale;
		void fill;
		void sideBySide;
		void main;
		void links;
		einpassen();
	});

	$effect(() => {
		main?.show(mode === 'original' ? original : result, active);
	});

	$effect(() => {
		links?.show(original, undefined);
	});

	function reportSelection(): void {
		const read = main?.selection();
		if (read) onmark?.(read.section, read.rect);
	}

	/** Alt+M: read the selection in the island if it exists. */
	export function readSelection(): { section: Section; rect: DOMRect } | null {
		return main?.selection() ?? null;
	}

	/** Returns an element in the interactive island – used for Alt+N and focus after the menu. */
	export function finden(selektor: string): HTMLElement | null {
		return main?.finden(selektor) ?? null;
	}

	export function readPosition(top: number): ViewPosition | null {
		return main?.readPosition(top) ?? null;
	}

	export function restorePosition(scroller: HTMLElement, position: ViewPosition): boolean {
		return main?.restorePosition(scroller, position) ?? false;
	}
</script>

<article
	bind:this={article}
	bind:clientWidth={width}
	data-column="flow"
	class="flex flex-col gap-3"
>
	{#if simplified > 0}
		<p
			role="note"
			class="flex items-center gap-2 self-start rounded-md border border-dashed border-amber-dash bg-amber-1 px-3 py-1.5 text-label text-amber-ink"
		>
			{t('review.flow.simplified', { n: simplified })}
		</p>
	{/if}
	{#if fehlend.length > 0}
		<p class="self-start text-fine text-muted">
			{t('review.flow.fonts', {
				n: fehlend.length,
				names: fehlend.map((name) => `„${name}“`).join(', ')
			})}
		</p>
	{/if}
	{#if sideBySide}
		<div
			aria-hidden="true"
			class="grid grid-cols-2 gap-4 font-sans text-fine font-semibold tracking-[0.04em] text-muted uppercase"
		>
			<span>{t('review.column.original')}</span>
			<span>{t('review.column.result')}</span>
		</div>
	{/if}
	<!-- Wider than the column: left-aligned so the view can scroll horizontally. -->
	<div class={cn(sideBySide && 'grid grid-cols-2 items-start gap-4')}>
		{#if sideBySide}
			<!-- Left pane is view-only: no tab stop, hidden from screen readers like regular text. -->
			<iframe
				{@attach island('links')}
				title={t('review.flow.original')}
				sandbox="allow-same-origin"
				srcdoc={ISLAND_SRCDOC}
				tabindex="-1"
				aria-hidden="true"
				class="block border-0 bg-transparent"
				height="0"
			></iframe>
		{/if}
		<iframe
			{@attach island('main')}
			title={sideBySide ? t('review.flow.result') : t('review.flow')}
			sandbox="allow-same-origin"
			srcdoc={ISLAND_SRCDOC}
			class={cn('block border-0 bg-transparent', !sideBySide && 'mx-auto')}
			height="0"
		></iframe>
	</div>
</article>
