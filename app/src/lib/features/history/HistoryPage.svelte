<script lang="ts">
	import { onMount, tick, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import Button from '#lib/ui/Button.svelte';
	import Card from '#lib/ui/Card.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';
	import Select from '#lib/ui/Select.svelte';
	import Skeleton from '#lib/ui/Skeleton.svelte';
	import {
		historyRows,
		parseScope,
		parseSort,
		queryHistory,
		type HistoryRow,
		type HistorySort
	} from '#lib/history/history.js';
	import { historyHref } from '#lib/history/links.js';
	import type { Entry } from '#lib/sidecar/api.gen.js';
	import { historyView } from '#lib/state/history-view.svelte.js';
	import { i18n, t } from '#lib/state/i18n.svelte.js';
	import { history } from '#lib/state/history.svelte.js';
	import HistoryTable from './HistoryTable.svelte';
	import RenameEntryDialog from './RenameEntryDialog.svelte';

	/**
	 * The full history (sidebar/history plan 3.1, 3.5, 3.6): one view for
	 * Open, Completed and All entries with local search and sorting. It works
	 * only on the collection the shell already polls — no per-row requests,
	 * no document loading, no model calls.
	 */

	const scope = $derived(parseScope(page.url.searchParams.get('view')));
	const sort = $derived(parseSort(page.url.searchParams.get('sort')));

	const rows = $derived(historyRows(history.entries, new Date(), i18n.language, t));
	const result = $derived(queryHistory(rows, { scope, query: historyView.query, sort }));
	const visible = $derived(result.rows.slice(0, historyView.limit));
	const searching = $derived(historyView.query.trim().length > 0);

	let root = $state<HTMLElement>();
	let heading = $state<HTMLElement>();
	let search = $state<HTMLInputElement>();
	/** Announced politely: search results and rename outcomes, never polling. */
	let announcement = $state('');

	const scroller = () => root?.closest('main') ?? null;

	// Arriving from an entry via "Back to history" restores the limit and scroll; any other arrival starts at the top.
	const returning = historyView.takeReturning();
	let restoreScroll: number | null = null;
	if (returning && returning.scope === parseScope(page.url.searchParams.get('view'))) {
		historyView.limit = returning.limit;
		restoreScroll = returning.scroll;
	} else {
		historyView.resetLimit();
	}

	onMount(() => {
		const main = scroller();
		if (main) main.scrollTop = 0;
	});

	// Restore the scroll position once the rows are there.
	$effect(() => {
		if (restoreScroll === null || !history.loaded) return;
		const target = restoreScroll;
		restoreScroll = null;
		void tick().then(() => {
			const main = scroller();
			if (main) main.scrollTop = target;
		});
	});

	// A new query, scope or sort starts the rendered list over.
	let lastKey: string | null = null;
	$effect(() => {
		const key = `${scope}|${sort}|${historyView.query}`;
		if (lastKey !== null && lastKey !== key) {
			untrack(() => historyView.resetLimit());
			// Another scope from the sidebar is a new page; the content area
			// keeps its own scroll position otherwise.
			if (!lastKey.startsWith(`${scope}|`)) {
				const main = scroller();
				if (main) main.scrollTop = 0;
			}
		}
		lastKey = key;
	});

	// Announce the result count after typing settles.
	let announceTimer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		const query = historyView.query;
		clearTimeout(announceTimer);
		if (!query.trim()) return;
		announceTimer = setTimeout(() => {
			announcement = untrack(() => countText());
		}, 600);
		return () => clearTimeout(announceTimer);
	});

	function countText(): string {
		return searching
			? t('history.result.filtered', { shown: result.rows.length, n: result.scopeTotal })
			: t('history.count', { n: result.scopeTotal });
	}

	const scopeTitle = $derived(
		scope === 'open'
			? t('history.scope.open')
			: scope === 'completed'
				? t('history.scope.completed')
				: t('history.scope.all')
	);

	function setSort(value: HistorySort): void {
		void goto(historyHref(scope, value), { replace: true, reset: false });
	}

	function clearSearch(): void {
		historyView.query = '';
		announcement = '';
		search?.focus();
	}

	function open(row: HistoryRow): void {
		historyView.open({
			entryId: row.id,
			scope,
			sort,
			limit: historyView.limit,
			scroll: scroller()?.scrollTop ?? 0
		});
	}

	// -- Rename ---------------------------------------------------------------

	let renaming = $state<Entry | null>(null);
	let renameOpen = $state(false);
	let renamedHidden = false;

	function rename(row: HistoryRow): void {
		renaming = row.entry;
		renamedHidden = false;
		renameOpen = true;
	}

	function renamed(entry: Entry): void {
		renamedHidden = !result.rows.some((row) => row.id === entry.id);
		announcement = renamedHidden
			? t('history.rename.doneHidden', { title: entry.title })
			: t('history.rename.done', { title: entry.title });
	}

	function focusAfterRename(event: Event): void {
		if (!renamedHidden) return;
		event.preventDefault();
		(search ?? heading)?.focus();
	}
</script>

<div bind:this={root} class="mx-auto flex max-w-[1060px] flex-col gap-5 px-8 pt-10 pb-12">
	<header class="flex flex-col gap-1 pr-14">
		<h1
			bind:this={heading}
			id="history-heading"
			tabindex="-1"
			class="font-serif text-display font-normal tracking-[-0.015em] text-ink outline-none"
		>
			{scopeTitle}
		</h1>
		<p class="text-label text-muted">
			{#if history.loaded}{countText()}{:else}{t('history.loading')}{/if}
		</p>
	</header>

	<div class="flex flex-wrap items-center gap-3">
		<div class="relative min-w-60 flex-[1_1_320px]">
			<label for="history-search" class="sr-only">{t('history.search.label')}</label>
			<span
				class="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-subtle"
				aria-hidden="true"><Icon name="search" size={16} /></span
			>
			<input
				bind:this={search}
				bind:value={historyView.query}
				id="history-search"
				type="search"
				autocomplete="off"
				spellcheck="false"
				placeholder={t('history.search.placeholder')}
				class="h-10.5 w-full rounded-md border border-line-strong bg-surface pr-10 pl-9 font-sans text-body text-ink placeholder:text-subtle [&::-webkit-search-cancel-button]:hidden"
			/>
			{#if searching}
				<IconButton
					label={t('history.search.clear')}
					icon="close"
					size="sm"
					iconSize={15}
					class="absolute top-1/2 right-1.5 -translate-y-1/2"
					onclick={clearSearch}
				/>
			{/if}
		</div>
		<Select
			aria-label={t('history.sort.label')}
			value={sort}
			options={[
				{ value: 'newest', label: t('history.sort.newest') },
				{ value: 'oldest', label: t('history.sort.oldest') }
			]}
			onchange={(event) => setSort(parseSort(event.currentTarget.value))}
		/>
	</div>

	<p class="sr-only" aria-live="polite" aria-atomic="true">{announcement}</p>

	{#if history.error && history.loaded}
		<p
			role="status"
			class="rounded-md border border-amber-line bg-amber-1 px-3.5 py-2.5 text-label text-amber-ink"
		>
			{t('history.error.stale')}
		</p>
	{/if}

	{#if !history.loaded && history.error}
		<Card tone="quiet" gap="lg" class="items-start" role="alert">
			<p class="text-lead text-muted">{t('history.error.initial')}</p>
			<Button icon="restore" onclick={() => void history.update()}>
				{t('history.retry')}
			</Button>
		</Card>
	{:else if !history.loaded}
		<div role="status" class="flex flex-col gap-4 py-2">
			<span class="sr-only">{t('history.loading')}</span>
			{#each [0, 1, 2, 3] as index (index)}
				<div class="flex items-center gap-6 border-b border-line-soft pb-4">
					<Skeleton class="h-3 w-56" />
					<Skeleton class="h-3 w-28" />
					<Skeleton class="h-3 w-24" />
					<Skeleton class="h-3 w-20" />
				</div>
			{/each}
		</div>
	{:else if rows.length === 0}
		<Card tone="quiet" gap="lg" class="items-start">
			<p class="text-lead text-muted">{t('history.empty.none')}</p>
			<Button href={resolve('/(app)')} icon="new">{t('sidebar.new')}</Button>
		</Card>
	{:else if result.scopeTotal === 0}
		<Card tone="quiet" gap="lg" class="items-start">
			<p class="text-lead text-muted">
				{scope === 'open' ? t('history.empty.open') : t('history.empty.completed')}
			</p>
			<Button href={historyHref('all', sort)} icon="allEntries">
				{t('history.scope.all')}
			</Button>
		</Card>
	{:else if result.rows.length === 0}
		<Card tone="quiet" gap="lg" class="items-start">
			<p class="text-lead text-muted">{t('history.empty.search')}</p>
			<Button icon="close" onclick={clearSearch}>{t('history.search.clear')}</Button>
		</Card>
	{:else}
		<HistoryTable
			rows={visible}
			exact={result.exact}
			storage={history.storage}
			labelledby="history-heading"
			onopen={open}
			onrename={rename}
		/>
		{#if result.rows.length > visible.length}
			<div class="flex justify-center">
				<Button onclick={() => historyView.showMore()}>{t('history.more')}</Button>
			</div>
		{/if}
	{/if}
</div>

<RenameEntryDialog
	entry={renaming}
	bind:open={renameOpen}
	onrenamed={renamed}
	onCloseFocus={focusAfterRename}
/>
