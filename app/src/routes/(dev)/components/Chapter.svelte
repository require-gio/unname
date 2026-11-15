<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';

	/** Group heading on the components page, styled like board labels. */
	let {
		title,
		children,
		class: className
	}: { title: string; children: Snippet; class?: string } = $props();
</script>

<div class={cn('flex flex-col gap-3', className)}>
	<h3 class="text-caption font-semibold tracking-[0.03em] text-muted uppercase">{title}</h3>
	{@render children()}
</div>
