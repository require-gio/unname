<script lang="ts">
	import { cn } from '#lib/utils.js';

	/**
	 * Native <progress> element – width follows the value without inline styles
	 * and screen readers can announce the current state.
	 * Without `value` it shows an indeterminate state.
	 */
	let {
		value,
		max = 100,
		label,
		tone = 'accent',
		class: className
	}: {
		value?: number;
		max?: number;
		label: string;
		/** `accent`: running · `ink`: finished · `neutral`: uncertain */
		tone?: 'accent' | 'ink' | 'neutral';
		class?: string;
	} = $props();
</script>

<progress
	{value}
	{max}
	aria-label={label}
	class={cn(
		'block h-1 w-full appearance-none overflow-hidden rounded-full border-0 bg-chip-strong',
		'[&::-webkit-progress-bar]:rounded-full [&::-webkit-progress-bar]:[background:var(--chip-strong)] [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:transition-[width]',
		tone === 'accent' &&
			'[&::-moz-progress-bar]:bg-accent [&::-webkit-progress-value]:[background:var(--accent)]',
		tone === 'ink' &&
			'[&::-moz-progress-bar]:bg-ink [&::-webkit-progress-value]:[background:var(--ink)]',
		tone === 'neutral' &&
			'[&::-moz-progress-bar]:bg-faint [&::-webkit-progress-value]:[background:var(--faint)]',
		className
	)}
></progress>
