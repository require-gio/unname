<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';

	/**
	 * A keyboard key. `light`/`dark` adds a border for help text; `quiet` is only a glyph
	 * in the button (“Maskieren ⏎").
	 */
	let {
		children,
		tone = 'light',
		class: className
	}: { children: Snippet; tone?: 'light' | 'dark' | 'quiet'; class?: string } = $props();
</script>

<kbd
	class={cn(
		'inline-flex min-w-5 items-center justify-center rounded-xs px-1 font-sans text-fine font-semibold',
		tone === 'light' && 'border border-b-2 border-line-strong bg-surface text-muted',
		tone === 'dark' && 'bg-bar-raised text-bar-muted',
		tone === 'quiet' && 'min-w-0 px-0 text-caption font-normal opacity-70',
		className
	)}>{@render children()}</kbd
>
