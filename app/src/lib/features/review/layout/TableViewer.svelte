<script lang="ts">
	import { tick } from 'svelte';
	import type { Attachment } from 'svelte/attachments';
	import { cn } from '#lib/utils.js';
	import type { GridMap } from '#lib/layout/map.js';
	import {
		Axis,
		requiredBlocks,
		BLOCK_LINES,
		tiles,
		HEADER_WIDTH,
		HEADER_HEIGHT,
		inLines,
		SegmentIndex,
		columnsAxis,
		columnsName,
		START,
		styleRules,
		STYLE,
		Verbunde,
		CellLader,
		linesAxis,
		cellContent,
		DISPLAY,
		type Tile,
		type Cell
	} from '#lib/layout/grid.js';
	import { sourceSection } from '#lib/review/selection.js';
	import { anchorFrom } from '#lib/review/dom.js';
	import type { Section } from '#lib/review/state.js';
	import type { ViewPosition } from '#lib/review/view-position.js';
	import type { LayoutSheetRowsResponse } from '#lib/sidecar/api.gen.js';
	import { i18n, t } from '#lib/state/i18n.svelte.js';
	import { segmentText, type Segment } from '#lib/ui/segments.js';
	import SegmentText from '#lib/ui/SegmentText.svelte';

	/**
	 * Excel and CSV as they appear (spec §6.2): a grid based on the sidecar's
	 * table model – column widths, row heights, merged cells, frozen areas,
	 * styles and number formats.
	 *
	 * - **Virtualized:** Only cells in the viewport (plus margin) exist in the DOM;
	 *   rows are fetched from the sidecar in blocks of 500 as needed. Scrolling 10 000 rows
	 *   feels like scrolling 10.
	 * - **Cells with position** show the source text via `SegmentText` – with codes,
	 *   questions and peek as in the text. This is the text the export changes for that
	 *   exact cell. Other cells show the formatted value.
	 * - **Selection** works only within a single cell using the browser selection;
	 *   cross-cell selection is not supported – the export also replaces content in a single cell.
	 * - **Side-by-side:** two grids, left original (read-only), right result, with linked scrolling.
	 *
	 * Position and size are set by script (inline styles are disallowed); the styles
	 * are emitted in a generated stylesheet (`stilRegeln`).
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
		onerror,
		load,
		article = $bindable()
	}: {
		map: GridMap;
		sourceText: string;
		mode: 'result' | 'original' | 'sideBySide';
		original: readonly Segment[];
		result: readonly Segment[];
		active?: string;
		fill?: boolean;
		/** Zoom in percent; with `fill` column width is applied. */
		scale?: number;
		onsegment?: (segment: Segment, element: HTMLElement) => void;
		/** Cells failed to load: fallback to plain text view. */
		onerror?: () => void;
		load: (
			sheet: number,
			by: number,
			until: number,
			language: 'de' | 'en',
			signal: AbortSignal
		) => Promise<LayoutSheetRowsResponse>;
		article?: HTMLElement;
	} = $props();

	const sideBySide = $derived(mode === 'sideBySide');
	const uid = `r${Math.random().toString(36).slice(2, 9)}`;

	/* -- sheet and dimensions ---------------------------------------------------- */

	let sheetIndex = $state(0);
	const sheet = $derived(map.sheets[sheetIndex] ?? map.sheets[0]);
	const lines = $derived(linesAxis(sheet));
	const columns = $derived(columnsAxis(sheet));
	const verbunde = $derived(new Verbunde(sheet.merges ?? []));
	const fixLines = $derived(Math.min(sheet.frozen?.[0] ?? 0, sheet.rows));
	const fixColumns = $derived(Math.min(sheet.frozen?.[1] ?? 0, sheet.cols));
	const fixHeight = $derived(lines.pos(fixLines));
	const fixWidth = $derived(columns.pos(fixColumns));
	/** Header top (column headers + frozen rows) and left (row numbers + frozen columns). */
	const bandHeight = $derived(HEADER_HEIGHT + fixHeight);
	const bandWidth = $derived(HEADER_WIDTH + fixWidth);
	const totalWidth = $derived(HEADER_WIDTH + columns.length);
	const totalHeight = $derived(HEADER_HEIGHT + lines.length);

	/* -- scrolling and zoom ------------------------------------------------- */

	let top = $state(0);
	let links = $state(0);
	let viewHeight = $state(600);
	let viewWidth = $state(800);
	let width = $state(0);
	let main = $state<HTMLDivElement>();
	let nextTo = $state<HTMLDivElement>();

	const zoom = $derived.by(() => {
		if (!fill) return scale / 100;
		const column = sideBySide ? (width - 16) / 2 : width;
		return Math.min(2, Math.max(0.5, column / Math.max(1, totalWidth)));
	});

	function gescrollt(source: HTMLDivElement): void {
		top = source.scrollTop;
		links = source.scrollLeft;
		viewHeight = source.clientHeight;
		viewWidth = source.clientWidth;
		// When side-by-side, keep the other pane in sync.
		const other = source === main ? nextTo : main;
		if (other && (other.scrollTop !== top || other.scrollLeft !== links)) {
			other.scrollTop = top;
			other.scrollLeft = links;
		}
	}

	/** Visible rows and columns in the viewport, excluding fixed ones. */
	const viewport = $derived.by(() => {
		const y = fixHeight + top / zoom;
		const x = fixWidth + links / zoom;
		const z = lines.viewport(y, y + viewHeight / zoom, 8);
		const s = columns.viewport(x, x + viewWidth / zoom, 3);
		return {
			lines: [Math.max(fixLines, z[0]), z[1]] as [number, number],
			columns: [Math.max(fixColumns, s[0]), s[1]] as [number, number]
		};
	});

	/* -- Load cells ------------------------------------------------------ */

	let loaded = $state(0);
	let missing = $state(false);
	const abort = new AbortController();
	$effect(() => () => abort.abort());

	const lader = new CellLader(
		(index, by, until, language) =>
			load(index, by, until, language === 'en' ? 'en' : 'de', abort.signal).then(
				(answer) => answer.cells as unknown as Cell[]
			),
		() => (loaded += 1),
		() => {
			if (abort.signal.aborted) return;
			missing = true;
			onerror?.();
		}
	);

	const language = $derived(i18n.language === 'en' ? 'en' : 'de');
	const cellStore = $derived(lader.storage(sheet.index, language));

	$effect(() => {
		for (const block of requiredBlocks(viewport.lines, fixLines, sheet.rows)) {
			void lader.block(sheet.index, block, language);
		}
	});

	/* -- Rendered tiles ------------------------------------------------- */

	interface Gezeichnet extends Tile {
		/** Segments from the source text – or `null` for the formatted value. */
		content: Segment[] | null;
		/** Index for `data-seg` (selection). */
		anchor: number;
	}

	interface Image {
		ecke: Gezeichnet[];
		top: Gezeichnet[];
		links: Gezeichnet[];
		body: Gezeichnet[];
		/** Segments per anchor, for `quellBereich`. */
		segments: Segment[][];
	}

	function imageFor(segments: readonly Segment[]): Image {
		void loaded;
		const index = new SegmentIndex(segments);
		const list: Segment[][] = [];
		const line = (n: number) => cellStore.line(n);
		const done = (tile: Tile): Gezeichnet => {
			const content = tile.cell ? cellContent(tile.cell, index, sourceText) : null;
			if (content) list.push(content);
			return { ...tile, content, anchor: content ? list.length - 1 : -1 };
		};
		const [z0, z1] = viewport.lines;
		const [s0, s1] = viewport.columns;
		const fixZ: [number, number] = [0, fixLines - 1];
		const fixS: [number, number] = [0, fixColumns - 1];
		const band = (z: [number, number], s: [number, number], x: number, y: number) =>
			z[1] < z[0] || s[1] < s[0]
				? []
				: tiles({ lines: z, columns: s }, lines, columns, verbunde, line, { x, y }).map(done);
		return {
			ecke: band(fixZ, fixS, 0, 0),
			top: band(fixZ, [s0, s1], fixWidth, 0),
			links: band([z0, z1], fixS, 0, fixHeight),
			body: band([z0, z1], [s0, s1], fixWidth, fixHeight),
			segments: list
		};
	}

	const image = $derived(imageFor(mode === 'original' ? original : result));
	const imageLinks = $derived(sideBySide ? imageFor(original) : null);

	/** Column headers A, B, C … and row numbers in the viewport. */
	const headers = $derived.by(() => {
		const visible = (axis: Axis, i: number) => axis.size(i) > 0;
		const columnsHeaders = (by: number, until: number, origin: number) =>
			Array.from({ length: Math.max(0, until - by + 1) }, (_, i) => by + i)
				.filter((s) => visible(columns, s))
				.map((s) => ({ n: s, x: columns.pos(s) - origin, b: columns.size(s) }));
		const linesHeaders = (by: number, until: number, origin: number) =>
			Array.from({ length: Math.max(0, until - by + 1) }, (_, i) => by + i)
				.filter((z) => visible(lines, z))
				.map((z) => ({ n: z, y: lines.pos(z) - origin, h: lines.size(z) }));
		return {
			columnsFix: columnsHeaders(0, fixColumns - 1, 0),
			columns: columnsHeaders(viewport.columns[0], viewport.columns[1], fixWidth),
			linesFix: linesHeaders(0, fixLines - 1, 0),
			lines: linesHeaders(viewport.lines[0], viewport.lines[1], fixHeight)
		};
	});

	/** Sheet names in the result: they can also be placeholders. */
	function sheetName(index: number): string {
		const section = map.sheets[index]?.name_range;
		if (!section) return map.sheets[index]?.name ?? '';
		const segments = new SegmentIndex(mode === 'original' ? original : result).in(
			section[0],
			section[1]
		);
		return segments.length ? segmentText(segments) : map.sheets[index].name;
	}

	/* -- Stylesheet and layout ----------------------------------------------- */

	const wurzel = `[data-grid="${uid}"]`;
	/**
	 * The grid looks like the file – also in dark mode, matching the page images.
	 * Colors come only from `--paper` and `--paper-ink` (same in both modes);
	 * lines and headers are mixes of those.
	 */
	const mix = (share: number) => `color-mix(in srgb,var(--paper-ink) ${share}%,var(--paper))`;
	const reasonStyle = `
${wurzel}{--grid-font:Calibri,Carlito,Arial,sans-serif}
${wurzel} .z{position:absolute;display:flex;overflow:hidden;box-sizing:border-box;padding:0 3px;
background:var(--paper);color:var(--paper-ink);font-family:var(--grid-font);font-size:14.67px;
line-height:1.2;white-space:pre;box-shadow:inset -1px -1px 0 ${mix(12)}}
${wurzel} .z:hover,${wurzel} .z:focus-within{overflow:visible;z-index:5}
${wurzel} .k{position:absolute;display:flex;align-items:center;justify-content:center;box-sizing:border-box;
background:${mix(5)};color:${mix(72)};font:500 11px/1 var(--font-sans);
box-shadow:inset -1px -1px 0 ${mix(18)}}`;

	function stylesheet(): Attachment<HTMLElement> {
		return (element) => {
			const style = document.createElement('style');
			element.prepend(style);
			$effect(() => {
				style.textContent = `${reasonStyle}\n${styleRules(wurzel, map.stile)}`;
			});
			return () => style.remove();
		};
	}

	function position(x: number, y: number, b: number, h: number): Attachment<HTMLElement> {
		return (element) => {
			Object.assign(element.style, {
				left: `${x}px`,
				top: `${y}px`,
				width: `${b}px`,
				height: `${h}px`
			});
		};
	}

	function masse(b: number | null, h: number | null, z?: number): Attachment<HTMLElement> {
		return (element) => {
			if (b !== null) element.style.width = `${b}px`;
			if (h !== null) element.style.height = `${h}px`;
			if (z !== undefined) element.style.zoom = String(z);
		};
	}

	/* -- Selection and Jump ------------------------------------------------ */

	/** The selection in a cell of the interactive grid – or `null`. */
	export function readSelection(): { section: Section; rect: DOMRect } | null {
		const selection = window.getSelection();
		if (!main || !selection || selection.isCollapsed || selection.rangeCount === 0) return null;
		const a = anchorFrom(selection.anchorNode, selection.anchorOffset, main);
		const b = anchorFrom(selection.focusNode, selection.focusOffset, main);
		// No cross-cell selection: export replaces exactly one cell.
		if (!a || !b || a.paragraph !== b.paragraph) return null;
		const section = sourceSection(image.segments, a, b, sourceText);
		if (!section) return null;
		const rect = selection.getRangeAt(0).getBoundingClientRect();
		selection.removeAllRanges();
		return { section, rect };
	}

	/** An element in the interactive grid. */
	export function finden(selektor: string): HTMLElement | null {
		return main?.querySelector<HTMLElement>(selektor) ?? null;
	}

	export function readPosition(): ViewPosition | null {
		if (!main) return null;
		const row = lines.viewport(fixHeight + top / zoom, fixHeight + top / zoom, 0)[0];
		const cells = [...cellStore.all()].filter((cell) => cell[0] >= row && cell[START] >= 0);
		cells.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
		return { page: sheetIndex + 1, anchor: cells[0]?.[START] ?? null };
	}

	export async function restorePosition(
		_scroller: HTMLElement,
		position: ViewPosition
	): Promise<boolean> {
		if (!main) return false;
		sheetIndex = Math.min(Math.max(position.page - 1, 0), map.sheets.length - 1);
		await tick();
		if (position.anchor !== null) {
			const row = await searchLine(sheetIndex, position.anchor);
			if (row === null) return false;
			main.scrollTop = Math.max(0, (lines.pos(row) - fixHeight) * zoom);
			gescrollt(main);
		}
		return true;
	}

	/**
	 * Bring the location `id` into view (Alt+N): select sheet, search the line above the
	 * source ranges of the blocks (binary search, blocks are ordered in source), scroll to it.
	 * Returns the element once it is rendered.
	 */
	export async function show(id: string): Promise<HTMLElement | null> {
		const segment = (mode === 'original' ? original : result).find((s) => s.id === id);
		const offset = segment?.source?.start;
		if (offset === undefined || !main) return finden(`[data-id="${CSS.escape(id)}"]`);
		let target = 0;
		map.sheets.forEach((kandidat, index) => {
			if ((kandidat.name_range?.[0] ?? 0) <= offset) target = index;
		});
		sheetIndex = target;
		await tick();
		const line = await searchLine(target, offset);
		if (line !== null) {
			const y = Math.max(0, (lines.pos(line) - fixHeight) * zoom - viewHeight / 2);
			main.scrollTop = y;
			top = main.scrollTop;
			await tick();
		}
		return finden(`[data-id="${CSS.escape(id)}"]`);
	}

	async function searchLine(index: number, offset: number): Promise<number | null> {
		const target = lader.storage(index, language);
		const num = Math.ceil((map.sheets[index]?.rows ?? 0) / BLOCK_LINES);
		let bottom = 0;
		let topBlock = num - 1;
		while (bottom <= topBlock) {
			const middle = (bottom + topBlock) >> 1;
			await lader.block(index, middle, language);
			const inBlock = [...target.all()].filter(
				(cell) => cell[START] >= 0 && Math.floor(cell[0] / BLOCK_LINES) === middle
			);
			if (inBlock.length === 0) {
				topBlock = middle - 1;
				continue;
			}
			const match = inBlock.find((cell) => cell[START] <= offset && offset < cell[5]);
			if (match) return match[0];
			if (offset < inBlock[0][START]) topBlock = middle - 1;
			else bottom = middle + 1;
		}
		return null;
	}

	const notices = $derived(
		[
			sheet.hidden ? t('review.grid.hiddenNotice') : null,
			sheet.conditional ? t('review.grid.conditional') : null
		].filter((text): text is string => text !== null)
	);
	const title = $derived(
		mode === 'original' || sideBySide
			? t('review.grid.original', { name: sheetName(sheetIndex) })
			: t('review.grid', { name: sheetName(sheetIndex) })
	);
</script>

{#snippet cell(tile: Gezeichnet, operable: boolean, dx: number, dy: number)}
	<div
		role="cell"
		aria-colindex={tile.column + 1}
		class={cn('z', tile.cell && `s${tile.cell[STYLE]}`)}
		{@attach position(dx + tile.x, dy + tile.y, tile.width, tile.height)}
	>
		{#if tile.content}
			<SegmentText
				segments={tile.content}
				anchor={tile.anchor}
				{active}
				class={tile.cell && map.stile[tile.cell[STYLE]]?.wrap ? '' : 'whitespace-pre'}
				onsegment={operable ? onsegment : undefined}
			/>
		{:else if tile.cell}
			{tile.cell[DISPLAY]}
		{/if}
	</div>
{/snippet}

<!-- One `role="row"` per line, no wrapper box; header and cells are positioned absolutely within the band. -->
{#snippet linesBy(
	list: Gezeichnet[],
	linesHeaders: { n: number; y: number; h: number }[],
	dx: number,
	dy: number,
	operable: boolean
)}
	{#each inLines(list, linesHeaders) as row (row.line)}
		<div role="row" aria-rowindex={row.line + 2} class="contents">
			{#if row.header}
				<div
					role="rowheader"
					class="k"
					{@attach position(0, dy + row.header.y, HEADER_WIDTH, row.header.h)}
				>
					{row.line + 1}
				</div>
			{/if}
			{#each row.tiles as tile (`${tile.line}:${tile.column}`)}
				{@render cell(tile, operable, dx, dy)}
			{/each}
		</div>
	{/each}
{/snippet}

{#snippet grid(ownImage: Image, role: 'main' | 'nextTo')}
	{@const operable = role === 'main'}
	<div
		bind:this={
			() => (role === 'main' ? main : nextTo),
			(el) => (role === 'main' ? (main = el) : (nextTo = el))
		}
		role="table"
		aria-label={role === 'nextTo'
			? t('review.grid.original', { name: sheetName(sheetIndex) })
			: title}
		aria-rowcount={sheet.rows + 1}
		aria-colcount={sheet.cols}
		data-column={sideBySide ? (operable ? 'result' : 'original') : 'einzeln'}
		tabindex="-1"
		onscroll={(event) => gescrollt(event.currentTarget)}
		class="max-h-[min(72vh,calc(100vh-16rem))] w-fit max-w-full overflow-auto rounded-xs bg-paper shadow-page outline-none"
		{@attach (element) => {
			viewHeight = element.clientHeight;
			viewWidth = element.clientWidth;
		}}
	>
		<div class="relative" {@attach masse(totalWidth, totalHeight, zoom)}>
			<!-- Top: corner, column headers and fixed rows stay sticky while scrolling. -->
			<div class="sticky top-0 z-20 flex" {@attach masse(totalWidth, bandHeight)}>
				<div class="sticky left-0 z-30 shrink-0 bg-paper" {@attach masse(bandWidth, bandHeight)}>
					<div
						class="k"
						aria-hidden="true"
						{@attach position(0, 0, HEADER_WIDTH, HEADER_HEIGHT)}
					></div>
					{#if headers.columnsFix.length > 0}
						<div role="row" aria-rowindex={1} class="contents">
							{#each headers.columnsFix as header (header.n)}
								<div
									role="columnheader"
									aria-colindex={header.n + 1}
									class="k"
									{@attach position(HEADER_WIDTH + header.x, 0, header.b, HEADER_HEIGHT)}
								>
									{columnsName(header.n)}
								</div>
							{/each}
						</div>
					{/if}
					{@render linesBy(ownImage.ecke, headers.linesFix, HEADER_WIDTH, HEADER_HEIGHT, operable)}
				</div>
				<div class="relative shrink-0" {@attach masse(totalWidth - bandWidth, bandHeight)}>
					<div role="row" aria-rowindex={1} class="contents">
						{#each headers.columns as header (header.n)}
							<div
								role="columnheader"
								aria-colindex={header.n + 1}
								class="k"
								{@attach position(header.x, 0, header.b, HEADER_HEIGHT)}
							>
								{columnsName(header.n)}
							</div>
						{/each}
					</div>
					{@render linesBy(ownImage.top, [], 0, HEADER_HEIGHT, operable)}
				</div>
			</div>
			<!-- Below: left side shows row numbers and fixed columns, right side the body. -->
			<div class="flex" {@attach masse(totalWidth, totalHeight - bandHeight)}>
				<div
					class="sticky left-0 z-10 shrink-0 bg-paper"
					{@attach masse(bandWidth, totalHeight - bandHeight)}
				>
					{@render linesBy(ownImage.links, headers.lines, HEADER_WIDTH, 0, operable)}
				</div>
				<div
					class="relative shrink-0"
					{@attach masse(totalWidth - bandWidth, totalHeight - bandHeight)}
				>
					{@render linesBy(ownImage.body, [], 0, 0, operable)}
				</div>
			</div>
		</div>
	</div>
{/snippet}

<article
	bind:this={article}
	bind:clientWidth={width}
	data-grid={uid}
	class="flex flex-col gap-3"
	{@attach stylesheet()}
>
	{#each notices as notice (notice)}
		<p
			role="note"
			class="flex items-center gap-2 self-start rounded-md border border-dashed border-amber-dash bg-amber-1 px-3 py-1.5 text-label text-amber-ink"
		>
			{notice}
		</p>
	{/each}
	{#if sideBySide}
		<div
			aria-hidden="true"
			class="grid grid-cols-2 gap-4 font-sans text-fine font-semibold tracking-[0.04em] text-muted uppercase"
		>
			<span>{t('review.column.original')}</span>
			<span>{t('review.column.result')}</span>
		</div>
	{/if}
	<div class={cn(sideBySide && 'grid grid-cols-2 items-start gap-4')}>
		{#if sideBySide && imageLinks}
			<div aria-hidden="true">{@render grid(imageLinks, 'nextTo')}</div>
		{/if}
		{@render grid(image, 'main')}
	</div>
	{#if loaded === 0 && !missing && sheet.rows > 0}
		<p role="status" class="text-meta text-subtle">{t('review.grid.loads')}</p>
	{/if}
	{#if map.sheets.length > 1}
		<div role="tablist" aria-label={t('review.grid.sheets')} class="flex flex-wrap gap-1.5">
			{#each map.sheets as kandidat, index (kandidat.index)}
				<button
					type="button"
					role="tab"
					aria-selected={index === sheetIndex}
					onclick={() => {
						sheetIndex = index;
						if (main) main.scrollTop = main.scrollLeft = 0;
						if (nextTo) nextTo.scrollTop = nextTo.scrollLeft = 0;
						top = links = 0;
					}}
					class={cn(
						'rounded-sm border px-3 py-1 text-label',
						index === sheetIndex
							? 'border-accent bg-surface font-semibold text-ink'
							: 'border-line bg-surface-2 text-muted hover:text-ink'
					)}
				>
					{sheetName(index)}{#if kandidat.hidden}<span class="text-subtle">
							· {t('review.grid.hidden')}</span
						>{/if}
				</button>
			{/each}
		</div>
	{/if}
</article>
