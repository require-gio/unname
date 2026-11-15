<script lang="ts">
	import { cn } from '#lib/utils.js';
	import type { Label } from '#lib/sidecar/result-policy.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import Icon from './Icon.svelte';
	import { LABEL_DISPLAY } from './labels.js';

	/**
	 * The label of a saved file: reversible masking, irreversible masking, or
	 * the strongest level. The sidecar chooses it (`#lib/sidecar/result-policy.ts`);
	 * this component only displays it.
	 */
	let {
		label,
		inventedNames = false,
		class: className
	}: { label: Label; inventedNames?: boolean; class?: string } = $props();

	const display = $derived(LABEL_DISPLAY[label]);
</script>

<span
	class={cn(
		'inline-flex shrink-0 items-center gap-1.5 self-start rounded-full px-2.5 py-0.5 font-sans text-meta font-semibold whitespace-nowrap',
		display.klass,
		className
	)}
>
	<Icon name={display.icon} size={13} strokeWidth={2.1} />
	{t(display.text)}{#if inventedNames && label === 'reversible'}<span class="font-medium"
			>{`· ${t('label.inventedNames')}`}</span
		>{/if}
</span>
