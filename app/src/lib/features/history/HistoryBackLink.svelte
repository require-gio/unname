<script lang="ts">
	import { page } from '$app/state';
	import { cn } from '#lib/utils.js';
	import Icon from '#lib/ui/Icon.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';
	import { historyHref } from '#lib/history/links.js';
	import { historyView } from '#lib/state/history-view.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';

	/**
	 * Back link to history when viewing an entry opened from history.
	 * Shown only when that origin is known; it restores the previous
	 * scope, sort, search, limit and scroll position. `kompakt` selects the
	 * icon-only form for the review floating toolbar.
	 */
	let {
		kompakt = false,
		class: className
	}: {
		kompakt?: boolean;
		class?: string;
	} = $props();

	const origin = $derived(historyView.originFor(page.params.id));
	const href = $derived(origin ? historyHref(origin.scope, origin.sort) : historyHref('all'));
</script>

{#if origin}
	{#if kompakt}
		<IconButton
			{href}
			label={t('history.back')}
			icon="allEntries"
			variant="secondary"
			class={cn('rounded-[12px] shadow-float', className)}
			onclick={() => historyView.markReturning()}
		/>
	{:else}
		<a
			{href}
			onclick={() => historyView.markReturning()}
			class={cn(
				'inline-flex w-fit items-center gap-1.5 rounded-sm text-label font-medium text-link no-underline hover:underline',
				className
			)}
		>
			<Icon name="back" size={15} />{t('history.back')}
		</a>
	{/if}
{/if}
