<script lang="ts">
	import CoachMark from './CoachMark.svelte';
	import { help } from '#lib/state/help.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';

	/**
	 * Layer for all registered help hints; appears once in the layout.
	 * Escape hides the help again.
	 */
	function hotkey(event: KeyboardEvent) {
		if (help.on && event.key === 'Escape' && !event.defaultPrevented) help.ausschalten();
	}
</script>

<svelte:window onkeydown={hotkey} />

{#if help.on}
	{#each help.marken as mark (mark.id)}
		<CoachMark
			id={help.notizId(mark.id)}
			number={mark.number}
			text={t(mark.key, mark.values)}
			page={mark.page}
			anchor={mark.target}
		/>
	{/each}
{/if}
