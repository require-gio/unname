<script lang="ts" generics="T extends string">
	import { RadioGroup } from 'bits-ui';
	import { cn } from '#lib/utils.js';

	/**
	 * A single selection rendered as chips, e.g. “type of entry” in the dropdown menu.
	 * Implemented as a radio group with arrow-key navigation.
	 */
	let {
		options,
		value = $bindable(),
		label,
		class: className
	}: {
		options: readonly { value: T; label: string }[];
		value: T;
		label: string;
		class?: string;
	} = $props();
</script>

<RadioGroup.Root
	bind:value
	orientation="horizontal"
	aria-label={label}
	class={cn('flex flex-wrap gap-1.5', className)}
>
	{#each options as option (option.value)}
		<RadioGroup.Item
			value={option.value}
			class="inline-flex h-control-sm cursor-pointer items-center rounded-full border border-line-strong bg-surface px-3 font-sans text-label text-muted transition-colors hover:text-ink aria-checked:border-tint-line aria-checked:bg-tint-2 aria-checked:font-semibold aria-checked:text-tint-ink"
		>
			{option.label}
		</RadioGroup.Item>
	{/each}
</RadioGroup.Root>
