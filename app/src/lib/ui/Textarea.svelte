<script lang="ts">
	import type { HTMLTextareaAttributes } from 'svelte/elements';
	import { cn } from '#lib/utils.js';

	/**
	 * Multi-line input. `document` mode matches the Newsreader view,
	 * `codes` uses JetBrains Mono (AI answer with code),
	 * `eingebettet` removes border and rounding when placed inside a card.
	 */
	let {
		value = $bindable(''),
		font = 'standard',
		eingebettet = false,
		class: className,
		...rest
	}: {
		value?: string;
		font?: 'standard' | 'document' | 'codes';
		eingebettet?: boolean;
		class?: string;
	} & Omit<HTMLTextareaAttributes, 'class' | 'value'> = $props();
</script>

<textarea
	bind:value
	{...rest}
	class={cn(
		'block w-full resize-none bg-field px-5 py-4.5 placeholder:text-subtle',
		eingebettet ? 'border-0' : 'rounded-md border border-line-strong',
		font === 'standard' && 'font-sans text-body text-ink',
		font === 'document' && 'font-doc text-doc leading-[1.6] text-doc-ink',
		font === 'codes' && 'font-mono text-label leading-[1.7] text-ink-2',
		className
	)}></textarea>
