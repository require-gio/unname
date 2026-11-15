<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';
	import Icon from './Icon.svelte';
	import type { IconName } from './icons.js';

	/**
	 * A measurement with a label, e.g. "Local AI · 84 %".
	 * The bar uses a native `<meter>`: screen readers read the value and range,
	 * and the width is handled automatically.
	 *
	 * `safe` colors the bar indigo (the AI is confident), otherwise neutral.
	 * `children` is rendered between the label and the bar (AI justification).
	 */
	let {
		value,
		label,
		valueText,
		icon,
		safe = true,
		children,
		class: className
	}: {
		/** 0…1 */
		value: number;
		label: string;
		/** Displayed value, e.g. "84 %"; formatting is caller's responsibility. */
		valueText: string;
		icon?: IconName;
		safe?: boolean;
		children?: Snippet;
		class?: string;
	} = $props();

	const id = $props.id();
</script>

<div class={cn('flex flex-col gap-1.75', className)}>
	<div
		class={cn(
			'flex items-center justify-between gap-2 text-caption font-semibold',
			safe ? 'text-link' : 'text-muted'
		)}
	>
		<span {id} class="inline-flex items-center gap-1.5">
			{#if icon}<Icon name={icon} size={13} />{/if}{label}
		</span>
		<span class="tabular-nums">{valueText}</span>
	</div>
	{@render children?.()}
	<meter
		{value}
		min="0"
		max="1"
		aria-labelledby={id}
		class={cn(
			'block h-1 w-full appearance-none overflow-hidden rounded-full border-0',
			'[&::-webkit-meter-bar]:h-1 [&::-webkit-meter-bar]:rounded-full [&::-webkit-meter-bar]:border-0',
			'[&::-webkit-meter-optimum-value]:rounded-full',
			// Chromium and WebKit paint the bar and value with gradients; only the
			// `background` shorthand replaces them.
			safe
				? 'bg-tint-5 [&::-moz-meter-bar]:bg-accent [&::-webkit-meter-bar]:[background:var(--tint-5)] [&::-webkit-meter-optimum-value]:[background:var(--accent)]'
				: 'bg-chip-strong [&::-moz-meter-bar]:bg-faint [&::-webkit-meter-bar]:[background:var(--chip-strong)] [&::-webkit-meter-optimum-value]:[background:var(--faint)]'
		)}
	></meter>
</div>
