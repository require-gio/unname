<script lang="ts">
	import HistoryBackLink from '#lib/features/history/HistoryBackLink.svelte';
	import { historyHref } from '#lib/history/links.js';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import RestoreView from '#lib/features/restore/RestoreView.svelte';
	import { fetchEntry } from '#lib/sidecar/api-v2.js';
	import type { Entry } from '#lib/sidecar/api.gen.js';
	import { SidecarError } from '#lib/sidecar/client.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import Button from '#lib/ui/Button.svelte';
	import Card from '#lib/ui/Card.svelte';
	import Spinner from '#lib/ui/Spinner.svelte';

	/**
	 * Board “5 · AI answer restored” is linked to an entry.
	 *
	 * The AI answer or file from the saved view arrives via `page.state`
	 * (never in the URL) and disappears after a reload.
	 */
	const id = $derived(page.params.id ?? '');
	let entry = $state<Entry | null>(null);
	let missing = $state(false);
	let error = $state(false);

	$effect(() => {
		const connection = sidecarGate.connection;
		const searched = id;
		if (!connection || !searched) return;
		let active = true;
		fetchEntry(connection, searched)
			.then((fresh) => {
				if (!active) return;
				entry = fresh;
				missing = false;
				error = false;
			})
			.catch((cause) => {
				if (!active) return;
				missing = cause instanceof SidecarError && cause.status === 404;
				error = !missing;
			});
		return () => {
			active = false;
		};
	});
</script>

{#if entry && entry.id === id}
	{#key entry.id}
		<RestoreView {entry} answer={page.state.answer} selection={page.state.file} />
	{/key}
{:else if missing}
	<div class="mx-auto flex max-w-[960px] flex-col gap-6 px-10 pt-16 pb-10">
		<Card tone="quiet" gap="lg" class="items-start">
			<p class="text-lead text-muted">{t('entry.notFound')}</p>
			<div class="flex flex-wrap gap-2">
				<Button href={historyHref('all')} icon="allEntries">{t('history.back')}</Button>
				<Button href={resolve('/(app)')} icon="new" variant="ghost">{t('sidebar.new')}</Button>
			</div>
		</Card>
	</div>
{:else if error}
	<div class="mx-auto flex max-w-[960px] flex-col gap-3 px-10 pt-16">
		<HistoryBackLink />
		<p role="alert" class="text-lead text-red-ink">{t('entry.error')}</p>
	</div>
{:else}
	<div class="mx-auto max-w-[960px] px-10 pt-16">
		<Spinner size={22} label={t('common.loads')} class="text-subtle" />
	</div>
{/if}
