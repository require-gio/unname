<script lang="ts">
	import { cn } from '#lib/utils.js';
	import Skeleton from '#lib/ui/Skeleton.svelte';
	import type { HistoryRow } from '#lib/history/history.js';
	import { entryHref } from '#lib/history/links.js';
	import { t } from '#lib/state/i18n.svelte.js';

	/**
	 * "Zuletzt erstellt": the most recently created entries (plan 3.3), at
	 * most three — the sidebar passes them in (`recentEntries`). Sorted by
	 * creation time only; opening or renaming never reorders them. Two lines
	 * each: title and time, then day, extent and reference. The reference
	 * never shrinks, so it stays readable when the title is cut.
	 */
	let {
		rows,
		active,
		status = 'ready'
	}: {
		rows: readonly HistoryRow[];
		/** The open entry, highlighted when it is among the recents. */
		active?: string;
		status?: 'loads' | 'error' | 'ready';
	} = $props();
</script>

<section aria-labelledby="recent-heading" class="flex flex-col">
	<h2
		id="recent-heading"
		class="px-2.5 pt-1.5 pb-1 text-fine font-semibold tracking-[0.03em] text-subtle uppercase"
	>
		{t('history.recent')}
	</h2>
	{#if status === 'loads'}
		<div class="flex flex-col gap-3 px-2.5 py-2" aria-hidden="true">
			<Skeleton class="h-2.5 w-36" />
			<Skeleton class="h-2.5 w-28" />
		</div>
	{:else if status === 'error'}
		<p role="status" class="px-2.5 py-2 text-caption text-subtle">{t('historyList.error')}</p>
	{:else if rows.length === 0}
		<p class="px-2.5 py-2 text-caption text-subtle">{t('historyList.empty')}</p>
	{:else}
		<ul class="flex flex-col gap-px">
			{#each rows as row (row.id)}
				{@const current = row.id === active}
				<li>
					<a
						href={entryHref(row.entry)}
						title={row.title}
						aria-current={current ? 'page' : undefined}
						class={cn(
							'flex flex-col gap-0.5 rounded-sm px-2.5 py-2 no-underline transition-colors',
							current ? 'bg-surface text-ink shadow-1' : 'text-ink-2 hover:bg-surface-2'
						)}
					>
						<span class="flex items-baseline gap-2">
							<span
								class={cn(
									'min-w-0 flex-1 truncate text-ui',
									current ? 'font-semibold' : 'font-medium'
								)}>{row.title}</span
							>
							<time datetime={row.entry.created_at} class="shrink-0 text-caption text-subtle"
								>{row.created.time}</time
							>
						</span>
						<span class="flex items-baseline gap-1.5 text-caption text-subtle">
							<span class="min-w-0 truncate">{row.created.day} · {row.extent}</span>
							<span
								class={cn(
									'shrink-0 font-mono text-fine text-muted',
									row.referenceIsFallback && 'max-w-24 truncate'
								)}>#{row.reference}</span
							>
						</span>
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</section>
