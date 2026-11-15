<script lang="ts">
	import { Switch } from 'bits-ui';
	import { cn } from '#lib/utils.js';

	/**
	 * Toggle for immediate on/off actions (mask all, select, pattern,
	 * OCR, second opinion).
	 *
	 * Labeled via `label` or—if the name is shown beside it—via
	 * `labelledby` using its ID.
	 */
	let {
		checked = $bindable(false),
		label,
		labelledby,
		size = 'sm',
		disabled = false,
		oncheckedchange,
		class: className
	}: {
		checked?: boolean;
		label?: string;
		labelledby?: string;
		size?: 'sm' | 'md';
		disabled?: boolean;
		oncheckedchange?: (checked: boolean) => void;
		class?: string;
	} = $props();
</script>

<Switch.Root
	bind:checked
	onCheckedChange={oncheckedchange}
	{disabled}
	aria-label={label}
	aria-labelledby={labelledby}
	class={cn(
		'relative inline-flex shrink-0 cursor-pointer items-center rounded-full bg-track-off p-0.75 transition-colors disabled:cursor-not-allowed disabled:opacity-55 aria-checked:bg-accent',
		size === 'sm' ? 'h-5.5 w-9.5' : 'h-6.5 w-11',
		className
	)}
>
	<Switch.Thumb
		class={cn(
			'pointer-events-none block rounded-full bg-on-accent shadow-raised transition-transform duration-150 motion-reduce:transition-none',
			size === 'sm'
				? 'size-4 data-[state=checked]:translate-x-4'
				: 'size-5 data-[state=checked]:translate-x-4.5'
		)}
	/>
</Switch.Root>
