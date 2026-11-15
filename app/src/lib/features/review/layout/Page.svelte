<script lang="ts">
	import { cn } from '#lib/utils.js';
	import { atLeast, rect, type Box, type Marking } from '#lib/layout/boxen.js';
	import { displaySize, imageLevel, inUnits } from '#lib/layout/scale.js';
	import type { LayoutPage, PlacementText } from '#lib/sidecar/api.gen.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { segmentCaption } from '#lib/ui/caption.js';
	import { PAGES_STYLE } from '#lib/ui/segment-styles.js';
	import type { Segment } from '#lib/ui/segments.js';

	/**
	 * Fixed page layout: canvas with page image at the bottom and an SVG overlay above.
	 * All positions are expressed as SVG attributes – inline styles are prohibited by the rules.
	 *
	 * The SVG defines the page size via `width`/`height` (100 % = original size, zoom scales,
	 * wide pages scroll horizontally) or – with `fill` – the column width. The canvas sits
	 * directly underneath. The image loads only when the page approaches the viewport
	 * and releases its memory when far away.
	 *
	 * Layers (bottom-up): markings, then the overpaint from the placement plan
	 * (area + placeholder, as the export renders), then the active selection. Hovering
	 * over an overpainted spot hides the overpaint – the underlying page image is the original.
	 *
	 * Interactive elements are SVG groups with `role="button"` in reading order,
	 * at least 12 screen pixels tall.
	 */
	let {
		page,
		total,
		markings,
		hidden,
		selection = [],
		active,
		title,
		fill = false,
		scale,
		onsegment,
		load
	}: {
		page: LayoutPage;
		total: number;
		markings: readonly Marking[];
		/** Boxes for invisible text on this page. */
		hidden: readonly Box[];
		/** Boxes for the current word selection on this page. */
		selection?: readonly Box[];
		/** ID of the location where a menu is currently open. */
		active?: string;
		/** Name of the page image; default “Page n of m”. */
		title?: string;
		/** Fit to column width instead of original size. */
		fill?: boolean;
		/** Changes with zoom – recomputed as needed. */
		scale?: unknown;
		onsegment?: (segment: Segment, element: SVGElement) => void;
		load: (level: number, signal: AbortSignal) => Promise<ImageBitmap>;
	} = $props();

	let harness = $state<HTMLElement>();
	let canvas = $state<HTMLCanvasElement>();
	let nah = $state(false);
	let pxPerUnit = $state(0);
	let loaded = $state<number | null>(null);
	let error = $state(false);

	/** One screen pixel expressed in page units – used for spacing and text sizing. */
	/** Font class from the design → web font using Base-14 metrics in export. */
	const FONT: Record<PlacementText['font'], string> = {
		sans: 'font-replacement',
		serif: 'font-replacement-serif',
		mono: 'font-replacement-mono'
	};

	const px = $derived(inUnits(1, pxPerUnit || 1));
	const display = $derived(displaySize(page));

	function measure(): void {
		if (harness) pxPerUnit = harness.getBoundingClientRect().width / page.width;
	}

	function beobachten(element: HTMLElement) {
		// Approx. one page height ahead in both directions.
		const view = new IntersectionObserver(
			(entries) => {
				for (const entry of entries) nah = entry.isIntersecting;
			},
			{ rootMargin: '100% 0px' }
		);
		const size = new ResizeObserver(measure);
		view.observe(element);
		size.observe(element);
		return () => {
			view.disconnect();
			size.disconnect();
		};
	}

	// CSS zoom changes rendered width, not always the measured box.
	// Re-measure after each zoom change so the image scale grows accordingly.
	$effect(() => {
		void scale;
		void fill;
		const frame = requestAnimationFrame(measure);
		return () => cancelAnimationFrame(frame);
	});

	$effect(() => {
		const target = canvas;
		if (!target || !nah || pxPerUnit <= 0) return;
		const level = imageLevel(pxPerUnit, window.devicePixelRatio || 1, page.scales);
		if (level === loaded) return;
		const abort = new AbortController();
		load(level, abort.signal)
			.then((image) => {
				if (abort.signal.aborted) {
					image.close();
					return;
				}
				target.width = image.width;
				target.height = image.height;
				target.getContext('2d')?.drawImage(image, 0, 0);
				// The canvas holds the pixels; the bitmap is no longer needed.
				image.close();
				loaded = level;
				error = false;
			})
			.catch(() => {
				if (!abort.signal.aborted) error = true;
			});
		return () => abort.abort();
	});

	// When far from view: release pixels, placeholder keeps the height.
	$effect(() => {
		if (nah || !canvas || loaded === null) return;
		canvas.width = 0;
		canvas.height = 0;
		loaded = null;
	});

	function select(event: Event, segment: Segment): void {
		if (!onsegment || !(event.currentTarget instanceof SVGElement)) return;
		onsegment(segment, event.currentTarget);
	}

	function hotkey(event: KeyboardEvent, segment: Segment): void {
		if (event.key !== 'Enter' && event.key !== ' ') return;
		event.preventDefault();
		select(event, segment);
	}

	function caption(segment: Segment): string {
		const name = segmentCaption(segment);
		return segment.number !== undefined
			? `${name} · ${t('question.number', { n: segment.number })}`
			: name;
	}
</script>

{#snippet areas(marking: Marking, operable: boolean)}
	{@const segment = marking.segment}
	{@const overpaint = marking.overpaint}
	<!-- Overpainted areas show original on hover, then marked as masked. -->
	{@const style = PAGES_STYLE[overpaint ? 'masked-original' : segment.kind] ?? {}}
	{@const frame = overpaint?.areas ?? marking.boxen}
	{@const first = marking.boxen[0] ?? frame[0]}
	{#each marking.boxen as box, part (part)}
		{#if style.strich}
			<rect data-area {...rect(box)} class="fill-transparent stroke-none" />
			<line
				x1={box.x0}
				x2={box.x1}
				y1={box.y1 + px}
				y2={box.y1 + px}
				class={cn('stroke-[1.5]', style.strich)}
				vector-effect="non-scaling-stroke"
			/>
		{:else}
			<rect
				data-area
				{...rect(box, px)}
				rx={px}
				class={cn('stroke-1', style.area)}
				vector-effect="non-scaling-stroke"
			/>
		{/if}
	{/each}
	{#if overpaint}
		<g data-overpaint class="transition-opacity group-hover:opacity-0">
			{#each overpaint.areas as area, part (part)}
				<rect
					{...rect(area)}
					class={cn(
						'stroke-accent stroke-1',
						overpaint.fill === 'black' ? 'fill-paper-ink' : 'fill-paper'
					)}
					vector-effect="non-scaling-stroke"
				/>
			{/each}
			{#if overpaint.text}
				{@const text = overpaint.text}
				<text
					data-replacement
					aria-hidden="true"
					x={text.x}
					y={text.baseline}
					font-size={text.size}
					font-weight={text.bold ? 700 : 400}
					fill={text.color}
					textLength={text.width}
					lengthAdjust="spacingAndGlyphs"
					transform={text.rotate ? `rotate(${text.rotate} ${text.x} ${text.baseline})` : undefined}
					class={cn(FONT[text.font], 'whitespace-pre')}>{text.value}</text
				>
			{/if}
		</g>
	{/if}
	{#each frame as box, part (part)}
		{#if operable}
			<!-- Interaction area: even on tiny (invisible) text at least 12 px. -->
			<rect {...rect(atLeast(box, 12 * px))} class="fill-transparent stroke-none" />
		{/if}
		<rect
			{...rect(atLeast(box, 12 * px), 3 * px)}
			rx={2 * px}
			class={cn(
				'fill-none stroke-2',
				active === segment.id
					? 'stroke-accent'
					: 'stroke-none group-hover:stroke-accent/50 group-focus-visible:stroke-accent'
			)}
			vector-effect="non-scaling-stroke"
		/>
	{/each}
	{#if segment.kind === 'question' && segment.number !== undefined && first}
		{@const r = 7.5 * px}
		{@const wide = String(segment.number).length > 1 ? 3 * px : 0}
		<!-- Circle like on the question card, superscripted right of the spot;
		     the page edge separates it from neighboring areas. -->
		<g aria-hidden="true" class="pointer-events-none">
			<rect
				x={first.x1 + 2 * px}
				y={first.y0 - r}
				width={2 * r + 2 * wide}
				height={2 * r}
				rx={r}
				class="fill-amber-strong stroke-paper"
				stroke-width={1.5 * px}
			/>
			<text
				x={first.x1 + 2 * px + r + wide}
				y={first.y0}
				font-size={9 * px}
				text-anchor="middle"
				dominant-baseline="central"
				class="fill-on-amber-strong font-sans font-bold">{segment.number}</text
			>
		</g>
	{/if}
{/snippet}

<div
	bind:this={harness}
	{@attach beobachten}
	data-page={page.index}
	class={cn('relative mx-auto bg-paper shadow-page select-none', fill ? 'w-full' : 'w-fit')}
>
	<div
		role="img"
		aria-label={title ?? t('review.page', { n: page.index + 1, total })}
		class="absolute inset-0"
	>
		<!-- Canvas starts at 0×0; a non-empty canvas would otherwise be 300×150. -->
		<canvas bind:this={canvas} width="0" height="0" class="size-full"></canvas>
	</div>
	<svg
		viewBox="0 0 {page.width} {page.height}"
		width={display.width}
		height={display.height}
		class={cn('relative block h-auto', fill && 'w-full')}
	>
		{#each hidden as box, index (index)}
			<rect
				{...rect(box, 2 * px)}
				aria-label={t('review.page.hidden')}
				role="img"
				class="fill-none stroke-on-amber-strong [stroke-dasharray:1_2]"
				vector-effect="non-scaling-stroke"><title>{t('review.page.hidden.title')}</title></rect
			>
		{/each}
		{#each markings as marking, index (marking.segment.id ?? `m${index}`)}
			{@const segment = marking.segment}
			{#if segment.operable === true && onsegment !== undefined}
				<g
					data-id={segment.id}
					role="button"
					tabindex="0"
					aria-label={caption(segment)}
					aria-haspopup="menu"
					aria-expanded={active === segment.id}
					class="group cursor-pointer outline-none"
					onclick={(event) => select(event, segment)}
					onkeydown={(event) => hotkey(event, segment)}
				>
					{@render areas(marking, true)}
				</g>
			{:else}
				<!-- Not interactive: pointer passes through to underlying words. -->
				<g
					data-id={segment.id}
					role="group"
					aria-label={caption(segment)}
					class="group pointer-events-none"
				>
					{@render areas(marking, false)}
				</g>
			{/if}
		{/each}
		{#each selection as box, index (index)}
			<rect data-selection {...rect(box, px)} class="pointer-events-none fill-accent/25" />
		{/each}
	</svg>
	{#if error}
		<p
			role="alert"
			class="absolute inset-x-4 top-4 rounded-md border border-red-line-soft bg-red-0 px-3 py-2 text-label text-red-ink"
		>
			{t('review.page.error')}
		</p>
	{/if}
</div>
