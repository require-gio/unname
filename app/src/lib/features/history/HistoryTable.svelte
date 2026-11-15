<script lang="ts">
	import Badge from '#lib/ui/Badge.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';
	import Popover from '#lib/ui/Popover.svelte';
	import type { HistoryRow } from '#lib/history/history.js';
	import { entryHref } from '#lib/history/links.js';
	import type { StorageResponse } from '#lib/sidecar/api.gen.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { entryNotice } from '#lib/history/disk-space.js';
	import HistoryStatusBadge from './HistoryStatusBadge.svelte';

	/**
	 * The history table (plan 3.3): entry (title and reference), extent,
	 * creation time, workflow status and a compact actions column. The title
	 * is a real link; the reference is selectable text. In a narrow content
	 * area the rows reflow into stacked fields instead of being clipped.
	 */
	let {
		rows,
		exact,
		storage,
		labelledby,
		onopen,
		onrename
	}: {
		rows: readonly HistoryRow[];
		exact: ReadonlySet<string>;
		storage: StorageResponse | null;
		labelledby: string;
		onopen: (row: HistoryRow) => void;
		onrename: (row: HistoryRow, invoker: HTMLElement) => void;
	} = $props();

	const cell = '@max-3xl:block @max-3xl:p-0 px-3 py-3 align-top';
</script>

<div class="@container">
	<table aria-labelledby={labelledby} class="w-full border-collapse text-left @max-3xl:block">
		<thead class="@max-3xl:sr-only">
			<tr class="border-b border-line text-meta font-semibold text-subtle">
				<th scope="col" class="px-3 pb-2 font-semibold">{t('history.column.entry')}</th>
				<th scope="col" class="px-3 pb-2 font-semibold">{t('history.column.extent')}</th>
				<th scope="col" class="px-3 pb-2 font-semibold whitespace-nowrap"
					>{t('history.column.created')}</th
				>
				<th scope="col" class="px-3 pb-2 font-semibold">{t('history.column.status')}</th>
				<th scope="col" class="px-3 pb-2"
					><span class="sr-only">{t('history.column.actions')}</span></th
				>
			</tr>
		</thead>
		<tbody class="@max-3xl:flex @max-3xl:flex-col @max-3xl:gap-2">
			{#each rows as row (row.id)}
				{@const exactMatch = exact.has(row.id)}
				{@const notice = entryNotice(storage, row.id, t)}
				<tr
					data-entry={row.id}
					class={[
						'border-b border-line-soft @max-3xl:grid @max-3xl:grid-cols-[minmax(0,1fr)_auto] @max-3xl:gap-x-3 @max-3xl:gap-y-1.5 @max-3xl:rounded-md @max-3xl:border @max-3xl:p-3',
						exactMatch && 'bg-tint-1'
					]}
				>
					<td class={[cell, 'max-w-0 min-w-56 @max-3xl:col-span-2 @max-3xl:max-w-none']}>
						<a
							href={entryHref(row.entry)}
							onclick={() => onopen(row)}
							class="line-clamp-2 font-semibold break-words text-ink no-underline hover:underline"
						>
							{row.title}<span class="sr-only"> #{row.reference}</span>
						</a>
						<span class="mt-0.5 flex flex-wrap items-center gap-2">
							<span
								class={[
									'font-mono text-caption text-muted select-text',
									row.referenceIsFallback && 'break-all'
								]}
								aria-hidden="true">#{row.reference}</span
							>
							{#if exactMatch}<Badge tone="tint" flat>{t('history.exact')}</Badge>{/if}
						</span>
					</td>
					<td class={[cell, 'text-label text-ink-2 @max-3xl:col-span-2']}>{row.extent}</td>
					<td class={[cell, 'text-label whitespace-nowrap text-ink-2']}>
						<time datetime={row.entry.created_at}>{row.created.day}, {row.created.time}</time>
					</td>
					<td class={[cell, '@max-3xl:row-start-3']}>
						<HistoryStatusBadge status={row.status} />
					</td>
					<td class={[cell, 'py-2 @max-3xl:col-start-2 @max-3xl:row-start-3']}>
						<span class="flex items-center justify-end gap-0.5">
							<Popover
								label={t('history.info.label', { title: row.title, reference: row.reference })}
								alignment="end"
							>
								{#snippet trigger(props)}
									<IconButton
										{...props}
										label={t('history.info.label', { title: row.title, reference: row.reference })}
										icon="info"
										size="sm"
										iconSize={16}
									/>
								{/snippet}
								<p class="font-semibold break-words text-ink">{row.title}</p>
								<dl class="flex flex-col gap-1 text-label text-muted">
									<div>
										<dt class="sr-only">{t('history.reference')}</dt>
										<dd class="font-mono break-all text-ink-2 select-text">#{row.reference}</dd>
									</div>
									<div>
										<dt class="sr-only">{t('history.column.created')}</dt>
										<dd>{t('history.info.created', { time: row.created.full })}</dd>
									</div>
									<div>
										<dt class="sr-only">{t('history.column.extent')}</dt>
										<dd>{row.extent}</dd>
									</div>
									<div>
										<dt class="sr-only">{t('history.info.storage')}</dt>
										<dd>{notice ?? t('history.info.storageUnknown')}</dd>
									</div>
								</dl>
							</Popover>
							<IconButton
								label={t('history.rename.action', { title: row.title, reference: row.reference })}
								icon="rename"
								size="sm"
								iconSize={16}
								onclick={(event: MouseEvent) => onrename(row, event.currentTarget as HTMLElement)}
							/>
						</span>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
