<script lang="ts">
	import { cn } from '#lib/utils.js';

	/**
	 * Placeholder bar used in illustrations for unreadable text while content loads.
	 * Width and height are set via `class`.
	 * `tone` colors it as neutral, masked, or a question.
	 */
	let {
		tone = 'neutral',
		class: className
	}: { tone?: 'neutral' | 'masked' | 'question'; class?: string } = $props();
</script>

<span
	aria-hidden="true"
	class={cn(
		'block h-1.5 rounded-xs',
		tone === 'neutral' && 'bg-skeleton',
		tone === 'masked' && 'bg-accent',
		tone === 'question' && 'bg-amber-strong',
		className
	)}
></span>
