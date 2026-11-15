<script lang="ts">
	import type { HTMLInputAttributes } from 'svelte/elements';
	import { cn } from '#lib/utils.js';

	/**
	 * Single-line input. `pille` uses a dashed style for adding
	 * (e.g. “Add term …”), `standard` is the regular field.
	 */
	let {
		value = $bindable(''),
		variant = 'standard',
		invalid = false,
		class: className,
		...rest
	}: {
		value?: string;
		variant?: 'standard' | 'pille';
		invalid?: boolean;
		class?: string;
	} & Omit<HTMLInputAttributes, 'class' | 'value'> = $props();
</script>

<input
	bind:value
	aria-invalid={invalid || undefined}
	{...rest}
	class={cn(
		'bg-surface font-sans text-ink placeholder:text-subtle disabled:cursor-not-allowed disabled:opacity-55',
		variant === 'standard' &&
			'h-10.5 w-full rounded-md border border-line-strong px-3 text-body aria-invalid:border-red-ink',
		variant === 'pille' &&
			'h-8.5 min-w-48 rounded-full border border-dashed border-line-dash px-3.5 text-label',
		className
	)}
/>
