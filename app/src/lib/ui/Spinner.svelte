<script lang="ts">
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { cn } from '#lib/utils.js';

	/** Spins while an operation is in progress. Uses `label` for screen readers. */
	let {
		size = 16,
		label,
		class: className
	}: { size?: number; label?: string; class?: string } = $props();
</script>

<LoaderCircle
	{size}
	role={label ? 'status' : undefined}
	aria-label={label}
	aria-hidden={label ? undefined : 'true'}
	class={cn('shrink-0 animate-spin motion-reduce:animate-none', className)}
/>
