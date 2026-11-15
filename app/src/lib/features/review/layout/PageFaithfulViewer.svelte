<script lang="ts">
	import { untrack } from 'svelte';
	import { pageFaithfulMap, unlocatedPlaces, type PageFaithfulMap } from '#lib/layout/map.js';
	import type { Section } from '#lib/review/state.js';
	import type { ViewPosition } from '#lib/review/view-position.js';
	import { SidecarError } from '#lib/sidecar/client.js';
	import type { LayoutPagedResponse } from '#lib/sidecar/api.gen.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import type { Segment } from '#lib/ui/segments.js';
	import PageViewer from './PageViewer.svelte';

	type Page = 'original' | 'result';

	/**
	 * The viewer renders DOCX pages as fixed pages.
	 *
	 * - **Result:** pages that would be saved for the last apply state – codes are already
	 *   in the image, with only the overlay menu on top. Updated after each apply.
	 * - **Original:** pages of the cleaned copy with markings.
	 * - **Side-by-side:** original on the left, result on the right. Long codes may cause line
	 *   breaks, so page lengths can differ.
	 *
	 * Paragraphs that the engine placed differently from the text have no overlay – the view
	 * reports how many locations are affected.
	 */
	let {
		mode,
		original,
		result,
		status,
		active,
		fill = false,
		scale,
		onsegment,
		onmark,
		onerror,
		loadMap,
		loadImage,
		article = $bindable()
	}: {
		mode: 'result' | 'original' | 'sideBySide';
		original: readonly Segment[];
		result: readonly Segment[];
		/** The apply response; when it changes the result is refreshed. `null` before the first apply. */
		status: unknown;
		active?: string;
		fill?: boolean;
		scale?: unknown;
		onsegment?: (segment: Segment, element: SVGElement) => void;
		onmark?: (section: Section, rect: DOMRect) => void;
		/** The engine failed: the view falls back to "Page". */
		onerror?: () => void;
		loadMap: (page: Page, signal: AbortSignal) => Promise<LayoutPagedResponse>;
		loadImage: (
			page: Page,
			revision: number,
			number: number,
			level: number,
			signal: AbortSignal
		) => Promise<ImageBitmap>;
		article?: HTMLElement;
	} = $props();

	/** Short pause after an apply: multiple decisions in quick succession trigger only one update. */
	const VERZUG_MS = 250;
	/** An apply is still running (status 409): retry this many times. */
	const VERSUCHE = 20;

	let links = $state<PageFaithfulMap | null>(null);
	let right = $state<PageFaithfulMap | null>(null);
	let sets = $state(false);
	let positionViewer = $state<ReturnType<typeof PageViewer>>();

	export function readPosition(top: number, bottom?: number): ViewPosition | null {
		return positionViewer?.readPosition(top, bottom) ?? null;
	}

	export function restorePosition(scroller: HTMLElement, position: ViewPosition): boolean {
		// The result may paginate differently; wait until the current Apply has been laid out.
		if (mode !== 'original' && (!right || sets)) return false;
		return positionViewer?.restorePosition(scroller, position) ?? false;
	}

	function holen(page: Page, signal: AbortSignal, versuch = 0): Promise<PageFaithfulMap> {
		return untrack(() => loadMap(page, signal)).then(pageFaithfulMap, (error: unknown) => {
			if (error instanceof SidecarError && error.status === 409 && versuch < VERSUCHE) {
				return new Promise<PageFaithfulMap>((ok, nein) => {
					const time = setTimeout(() => holen(page, signal, versuch + 1).then(ok, nein), 300);
					signal.addEventListener('abort', () => clearTimeout(time), { once: true });
				});
			}
			throw error;
		});
	}

	// Load the original version once per file, also for the pre-apply result and the space key.
	$effect(() => {
		const abort = new AbortController();
		holen('original', abort.signal)
			.then((map) => (links = map))
			.catch(() => {
				if (!abort.signal.aborted) onerror?.();
			});
		return () => abort.abort();
	});

	$effect(() => {
		if (status === null || status === undefined) {
			right = null;
			return;
		}
		const abort = new AbortController();
		sets = true;
		const time = setTimeout(() => {
			holen('result', abort.signal)
				.then((map) => {
					right = map;
					sets = false;
				})
				.catch(() => {
					if (abort.signal.aborted) return;
					sets = false;
					onerror?.();
				});
		}, VERZUG_MS);
		return () => {
			clearTimeout(time);
			abort.abort();
		};
	});

	const sideBySide = $derived(mode === 'sideBySide');
	/** Right side or alone: the result – before first apply show original pages. */
	const resultMap = $derived(right ?? links);
	const resultPage = $derived<Page>(right ? 'result' : 'original');
	const shown = $derived(mode === 'original' ? links : resultMap);
	const unlocated = $derived(
		Math.max(
			mode === 'result' ? 0 : unlocatedPlaces(original, links?.unlocated ?? []),
			mode === 'original' ? 0 : unlocatedPlaces(result, resultMap?.unlocated ?? [])
		)
	);

	function load(page: Page, map: PageFaithfulMap) {
		return (number: number, level: number, signal: AbortSignal) =>
			loadImage(page, map.revision, number, level, signal);
	}
</script>

<div data-column="pageFaithful" class="flex flex-col gap-3">
	{#if shown}
		<p class="text-fine text-muted">
			{t('review.pageFaithful.engine', {
				engine: `${shown.engine.name} ${shown.engine.version}`
			})}
			{#if sets && mode !== 'original'}
				<span role="status"> · {t('review.pageFaithful.sets')}</span>
			{/if}
		</p>
	{/if}
	{#if unlocated > 0}
		<p
			role="note"
			class="flex items-center gap-2 self-start rounded-md border border-dashed border-amber-dash bg-amber-1 px-3 py-1.5 text-label text-amber-ink"
		>
			{t('review.pageFaithful.unlocated', { n: unlocated })}
		</p>
	{/if}

	{#if sideBySide && links && resultMap}
		<div
			aria-hidden="true"
			class="grid grid-cols-2 gap-4 font-sans text-fine font-semibold tracking-[0.04em] text-muted uppercase"
		>
			<span>{t('review.column.original')}</span>
			<span>{t('review.column.result')}</span>
		</div>
		<div class="grid grid-cols-2 items-start gap-4">
			<!-- Left side view only: no tab stop or pointer, hidden from screen readers. -->
			<div aria-hidden="true" inert>
				<PageViewer
					map={links.map}
					mode="original"
					{original}
					result={original}
					fill
					{scale}
					load={load('original', links)}
				/>
			</div>
			{#key `${resultPage}:${resultMap.revision}`}
				<PageViewer
					bind:this={positionViewer}
					bind:article
					map={resultMap.map}
					mode="result"
					{original}
					{result}
					{active}
					fill
					{scale}
					{onsegment}
					{onmark}
					load={load(resultPage, resultMap)}
				/>
			{/key}
		</div>
	{:else if mode === 'original' && links}
		<PageViewer
			bind:this={positionViewer}
			bind:article
			map={links.map}
			mode="original"
			{original}
			result={original}
			{active}
			{fill}
			{scale}
			{onsegment}
			{onmark}
			load={load('original', links)}
		/>
	{:else if mode !== 'original' && resultMap}
		<!-- Each state fresh: page images belong to exactly one apply state. -->
		{#key `${resultPage}:${resultMap.revision}`}
			<PageViewer
				bind:this={positionViewer}
				bind:article
				map={resultMap.map}
				mode="result"
				{original}
				{result}
				{active}
				{fill}
				{scale}
				{onsegment}
				{onmark}
				load={load(resultPage, resultMap)}
			/>
		{/key}
	{:else}
		<p role="status" class="text-label text-muted">{t('review.pageFaithful.loads')}</p>
	{/if}
</div>
