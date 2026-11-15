<script lang="ts">
	import { t } from '#lib/state/i18n.svelte.js';
	import Button from '#lib/ui/Button.svelte';
	import HelpButton from '#lib/ui/HelpButton.svelte';

	/**
	 * Navigation bar above the restore view (Board “5”):
	 * “← Overview”, the path “entry › answer restored” and “?” —
	 * placed here instead of top-right in the content area.
	 */
	let {
		back,
		entry,
		current
	}: {
		/** Overview link for the entry. */
		back: string;
		entry: string | null;
		current: string;
	} = $props();
</script>

<nav aria-label={t('restore.navigation')} class="flex flex-wrap items-center gap-3">
	<Button href={back} icon="back">{t('restore.overview')}</Button>
	<ol class="flex min-w-0 flex-1 items-center gap-2 text-ui text-muted">
		{#if entry}
			<li class="min-w-0 truncate">
				<a href={back} class="text-link no-underline hover:underline">{entry}</a>
			</li>
			<li aria-hidden="true" class="text-faint">›</li>
		{/if}
		<li aria-current="page" class="shrink-0 font-semibold text-ink">{current}</li>
	</ol>
	<HelpButton />
</nav>
