<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';
	import Icon from './Icon.svelte';

	/**
	 * Toggle chip component.
	 *
	 * - on: ✓, Indigo — “will be masked”
	 * - off: +, dashed outline
	 * - locked: always on, not interactive
	 *
	 * Implemented as a button with `aria-pressed`. Use `ChipAuswahl` for single-choice groups.
	 */
	let {
		on,
		locked = false,
		num,
		size = 'sm',
		onclick,
		children,
		class: className
	}: {
		on: boolean;
		locked?: boolean;
		num?: number | string;
		size?: 'sm' | 'md';
		onclick?: () => void;
		children: Snippet;
		class?: string;
	} = $props();
</script>

<button
	type="button"
	aria-pressed={locked ? true : on}
	disabled={locked}
	{onclick}
	class={cn(
		'inline-flex shrink-0 items-center gap-1.5 rounded-full border font-sans text-label transition-colors',
		size === 'sm' ? 'h-control-sm px-2.75' : 'h-9 px-3.25',
		locked
			? 'cursor-default border-line bg-chip text-muted'
			: on
				? 'border-tint-line bg-tint-2 font-semibold text-tint-ink hover:bg-tint-3'
				: 'border-dashed border-line-dash bg-surface font-medium text-muted hover:border-tint-line hover:text-ink',
		className
	)}
>
	{#if locked}
		<Icon name="lock" size={12} strokeWidth={2.2} />
	{:else}
		<span aria-hidden="true" class="inline-flex w-3 justify-center">{on ? '✓' : '+'}</span>
	{/if}
	{@render children()}
	{#if num !== undefined}
		<span
			class={cn(
				'text-fine font-semibold tabular-nums',
				on || locked ? 'text-accent-fg' : 'text-subtle'
			)}>{num}</span
		>
	{/if}
</button>
