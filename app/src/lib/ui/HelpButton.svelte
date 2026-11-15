<script lang="ts">
	import { cn } from '#lib/utils.js';
	import { help } from '#lib/state/help.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';

	/** "?" button at top-right toggles numbered help hints. */
	let { size = 'sm', class: className }: { size?: 'sm' | 'md'; class?: string } = $props();
</script>

<button
	type="button"
	aria-pressed={help.on}
	aria-label={t('help.show')}
	title={t('help.title')}
	onclick={() => help.toggle()}
	class={cn(
		'inline-flex shrink-0 items-center justify-center rounded-full border font-sans text-lead font-bold transition-colors',
		size === 'sm' ? 'size-9' : 'size-control-md',
		help.on
			? 'border-accent bg-accent text-on-accent halo-accent'
			: 'border-line-strong bg-surface text-muted shadow-1 hover:text-ink',
		className
	)}>?</button
>
