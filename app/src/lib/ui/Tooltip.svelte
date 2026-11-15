<script lang="ts">
	import { Tooltip } from 'bits-ui';
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';

	/**
	 * Small hint attached to a UI control, using the dark bar colour.
	 * The original per-code tooltip is replaced by a CSS peek in `SegmentText`
	 * because a separate tooltip per code would be too heavy.
	 */
	let {
		text,
		trigger,
		page = 'top',
		delay = 400,
		class: className
	}: {
		text: string;
		trigger: Snippet<[Record<string, unknown>]>;
		page?: 'top' | 'bottom' | 'left' | 'right';
		delay?: number;
		class?: string;
	} = $props();
</script>

<Tooltip.Provider delayDuration={delay}>
	<Tooltip.Root>
		<Tooltip.Trigger>
			{#snippet child({ props })}{@render trigger(props)}{/snippet}
		</Tooltip.Trigger>
		<Tooltip.Portal>
			<Tooltip.Content
				side={page}
				sideOffset={6}
				class={cn(
					'z-50 max-w-xs rounded-sm bg-bar px-2.25 py-1.25 font-sans text-caption font-medium text-bar-ink shadow-peek',
					'motion-safe:data-[state=closed]:animate-out motion-safe:data-[state=closed]:fade-out-0 motion-safe:data-[state=delayed-open]:animate-in motion-safe:data-[state=delayed-open]:fade-in-0',
					className
				)}
			>
				{text}
			</Tooltip.Content>
		</Tooltip.Portal>
	</Tooltip.Root>
</Tooltip.Provider>
