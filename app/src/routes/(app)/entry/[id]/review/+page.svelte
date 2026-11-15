<script lang="ts">
	import HistoryBackLink from '#lib/features/history/HistoryBackLink.svelte';
	import { historyHref } from '#lib/history/links.js';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import ReviewView from '#lib/features/review/ReviewView.svelte';
	import Button from '#lib/ui/Button.svelte';
	import Card from '#lib/ui/Card.svelte';
	import Spinner from '#lib/ui/Spinner.svelte';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { entryViewModel } from '#lib/state/entry.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { ui } from '#lib/state/ui.svelte.js';

	/**
	 * `?file=n` selects the file (starting at 1) and survives a reload.
	 * Without a parameter it opens the last active file from the draft; for new entries it
	 * opens the first reviewable file (AS2).
	 */
	const id = $derived(page.params.id ?? '');
	const view = entryViewModel;

	// If the connection is established only after opening, the entry reloads immediately
	// instead of waiting for the next cycle.
	$effect(() => {
		void sidecarGate.connection;
		if (id) return view.open(id);
	});

	// During review the focus belongs to the document.
	$effect(() => ui.focusBetreten());

	const desired = $derived(Number(page.url.searchParams.get('file')) - 1);
	const index = $derived.by(() => {
		const num = view.files.length;
		if (Number.isInteger(desired) && desired >= 0 && desired < num) return desired;
		const restored = view.files.findIndex((file) => file.id === view.view?.active_file_id);
		if (restored >= 0) return restored;
		const first = view.files.findIndex((file) => file.ready);
		return first < 0 ? 0 : first;
	});

	function select(fresh: number): void {
		void goto(`${resolve('/(app)/entry/[id]/review', { id })}?file=${fresh + 1}`, {
			replace: true,
			reset: false
		});
	}
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
{:else if view.error && !view.entry}
	<div class="mx-auto flex max-w-[960px] flex-col gap-3 px-10 pt-16">
		<HistoryBackLink />
		<p role="alert" class="text-lead text-red-ink">{t('entry.error')}</p>
	</div>
{:else if !view.viewLoaded}
	<div class="mx-auto max-w-[960px] px-10 pt-16">
		<Spinner size={22} label={t('common.loads')} class="text-subtle" />
	</div>
{:else}
	<ReviewView {index} onselect={select} />
{/if}
