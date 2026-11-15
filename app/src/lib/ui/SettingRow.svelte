<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';

	/**
	 * A setting row: title and description on the left, control on the right.
	 * If `forTarget` is provided, the title becomes the label for the control
	 * (`<label for>`); otherwise the control references the title via `aria-labelledby`
	 * using `titleId` from the snippet.
	 */
	let {
		title,
		description,
		forTarget,
		tone = 'standard',
		children,
		class: className
	}: {
		title: string;
		description?: string;
		forTarget?: string;
		tone?: 'standard' | 'danger' | 'quiet' | 'open';
		children: Snippet<[{ titleId: string }]>;
		class?: string;
	} = $props();

	const titleId = $props.id();
</script>

<div
	class={cn(
		'flex flex-wrap items-center justify-between gap-x-4 gap-y-3',
		tone !== 'open' && 'rounded-lg border px-4 py-3.5',
		tone === 'standard' && 'border-line',
		tone === 'danger' && 'border-red-line-soft bg-red-0',
		tone === 'quiet' && 'border-line bg-surface-3',
		className
	)}
>
	<span class="flex max-w-[440px] min-w-0 flex-col gap-0.5">
		{#if forTarget}
			<label for={forTarget} id={titleId} class="font-semibold">{title}</label>
		{:else}
			<span id={titleId} class="font-semibold">{title}</span>
		{/if}
		{#if description}<span class="text-label text-pretty text-muted">{description}</span>{/if}
	</span>
	<div class="flex flex-wrap items-center gap-2">{@render children({ titleId })}</div>
</div>
