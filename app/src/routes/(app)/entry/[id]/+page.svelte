<script lang="ts">
	import HistoryBackLink from '#lib/features/history/HistoryBackLink.svelte';
	import { historyHref } from '#lib/history/links.js';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import SavedView from '#lib/features/saved/SavedView.svelte';
	import DetectionView from '#lib/features/review/DetectionView.svelte';
	import Button from '#lib/ui/Button.svelte';
	import Card from '#lib/ui/Card.svelte';
	import MaskLabel from '#lib/ui/MaskLabel.svelte';
	import Spinner from '#lib/ui/Spinner.svelte';
	import { timePoint } from '#lib/format.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { entryViewModel } from '#lib/state/entry.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { entryTarget, historyMeta } from '#lib/history/entry-meta.js';

	/**
	 * A single entry from the history.
	 *
	 * UI flow: board “2 · detection running” → overview “review”.
	 * If saved, board “4 · saved”.
	 *
	 * Text: do not reopen because the text isn’t persisted (E10) –
	 * only a short note and “answer restore”.
	 */
	const id = $derived(page.params.id ?? '');
	const view = entryViewModel;
	let restoredEntry = $state<string | null>(null);

	// If the connection is only established after opening, the entry is re-read immediately
	// instead of waiting for the next cycle.
	$effect(() => {
		void sidecarGate.connection;
		if (id) return view.open(id);
	});

	const entry = $derived(view.id === id ? view.entry : null);
	$effect(() => {
		if (!entry || !view.viewLoaded || restoredEntry === id) return;
		restoredEntry = id;
		if (
			page.url.searchParams.get('overview') !== '1' &&
			entryTarget(entry, view.view) === 'review'
		) {
			void goto(resolve('/(app)/entry/[id]/review', { id }), { replace: true, reset: false });
		}
	});
	const meta = $derived(entry ? historyMeta(entry, t) : null);
	const backTarget = $derived(resolve('/(app)/entry/[id]/restore', { id }));
</script>

{#if view.error === 'missing'}
	<div class="mx-auto flex max-w-[960px] flex-col gap-6 px-10 pt-16 pb-10">
		<Card tone="quiet" gap="lg" class="items-start">
			<p class="text-lead text-muted">{t('entry.notFound')}</p>
			<div class="flex flex-wrap gap-2">
				<Button href={historyHref('all')} icon="allEntries">{t('history.back')}</Button>
				<Button href={resolve('/(app)')} icon="new" variant="ghost">{t('sidebar.new')}</Button>
			</div>
		</Card>
	</div>
{:else if view.error && !entry}
	<div class="mx-auto flex max-w-[960px] flex-col gap-3 px-10 pt-16">
		<HistoryBackLink />
		<p role="alert" class="text-lead text-red-ink">{t('entry.error')}</p>
	</div>
{:else if entry?.kind === 'documents' && entry.status === 'saved'}
	<SavedView {entry} />
{:else if entry?.kind === 'documents'}
	<DetectionView />
{:else if entry}
	<div class="mx-auto flex max-w-[960px] flex-col gap-6 px-10 pt-16 pb-10">
		<header class="flex flex-col gap-2">
			<HistoryBackLink class="mb-1" />
			<h1 class="font-serif text-display font-normal tracking-[-0.015em] text-ink">
				{entry.title}
			</h1>
			<p class="text-lead text-muted">
				{meta?.text} · {t('entry.created', { time: timePoint(new Date(entry.created_at)) })}
			</p>
		</header>
		<Card gap="lg" class="items-start gap-4">
			<MaskLabel label={entry.masking.method === 'reversible' ? 'reversible' : 'irreversible'} />
			<p class="max-w-150 text-lead text-pretty text-muted">
				{entry.masking.method === 'reversible'
					? t('entry.text.info')
					: t('entry.text.infoIrreversible')}
			</p>
			{#if entry.masking.method === 'reversible'}
				<Button href={backTarget} variant="primary" icon="restore">
					{t('text.restore')}
				</Button>
			{/if}
		</Card>
	</div>
{:else}
	<div class="mx-auto max-w-[960px] px-10 pt-16">
		<Spinner size={22} label={t('common.loads')} class="text-subtle" />
	</div>
{/if}
