<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';

	/**
	 * Section with header: title, optional description, and optional actions on the right.
	 * The heading uses the generated ID for aria-labelledby.
	 */
	let {
		title,
		description,
		layer = 4,
		eyebrow = false,
		actions,
		children,
		class: className
	}: {
		title: string;
		description?: string;
		/** Heading level in the document. */
		layer?: 2 | 3 | 4;
		/** Use an uppercase small-caps line instead of a heading ("WAS MASKIERT WIRD"). */
		eyebrow?: boolean;
		actions?: Snippet;
		children?: Snippet;
		class?: string;
	} = $props();

	const id = $props.id();
</script>

<section aria-labelledby={id} class={cn('flex flex-col gap-3', className)}>
	<div class="flex items-start justify-between gap-4">
		<div class="flex min-w-0 flex-col gap-0.5">
			<svelte:element
				this={`h${layer}`}
				{id}
				class={eyebrow
					? 'text-caption font-semibold tracking-[0.03em] text-muted uppercase'
					: 'text-lead font-semibold text-ink'}>{title}</svelte:element
			>
			{#if description}
				<p class="text-ui text-pretty text-muted">{description}</p>
			{/if}
		</div>
		{#if actions}
			<div class="flex shrink-0 flex-wrap items-center gap-2">{@render actions()}</div>
		{/if}
	</div>
	{@render children?.()}
</section>
