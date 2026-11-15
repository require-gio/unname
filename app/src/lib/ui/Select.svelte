<script lang="ts" generics="T extends string">
	import type { HTMLSelectAttributes } from 'svelte/elements';
	import { cn } from '#lib/utils.js';

	/**
	 * Native select list: OS provides keyboard, screen reader, and touch support,
	 * and the dropdown aligns correctly at the window edge.
	 */
	let {
		value = $bindable(),
		options,
		class: className,
		...rest
	}: {
		value: T;
		options: readonly { value: T; label: string }[];
		class?: string;
	} & Omit<HTMLSelectAttributes, 'class' | 'value'> = $props();
</script>

<select
	bind:value
	{...rest}
	class={cn(
		'h-control-md cursor-pointer rounded-md border border-line-strong bg-surface px-3 font-sans text-body text-ink disabled:cursor-not-allowed disabled:opacity-55',
		className
	)}
>
	{#each options as option (option.value)}
		<option value={option.value}>{option.label}</option>
	{/each}
</select>
