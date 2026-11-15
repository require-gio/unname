<script lang="ts">
	import { Popover } from 'bits-ui';
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';

	/**
	 * Floating popover attached to a trigger, used for the “Maskierung anpassen”
	 * menu. Bits UI handles focus, Escape key and clicks outside.
	 *
	 * ```svelte
	 * <Popover bind:open label="Maskierung anpassen">
	 *   {#snippet ausloeser(props)}<Button {...props}>…</Button>{/snippet}
	 *   …Inhalt…
	 * </Popover>
	 * ```
	 */
	let {
		open = $bindable(false),
		label,
		trigger,
		children,
		page = 'bottom',
		alignment = 'start',
		gap = 10,
		pfeil = false,
		anchor,
		class: className
	}: {
		open?: boolean;
		label: string;
		trigger?: Snippet<[Record<string, unknown>]>;
		children: Snippet;
		page?: 'top' | 'bottom' | 'left' | 'right';
		alignment?: 'start' | 'center' | 'end';
		gap?: number;
		pfeil?: boolean;
		/** Alternative anchor element instead of the trigger (split menu for codes). */
		anchor?: HTMLElement | null;
		class?: string;
	} = $props();
</script>

<Popover.Root bind:open>
	{#if trigger}
		<Popover.Trigger>
			{#snippet child({ props })}{@render trigger(props)}{/snippet}
		</Popover.Trigger>
	{/if}
	<Popover.Portal>
		<Popover.Content
			side={page}
			align={alignment}
			sideOffset={gap}
			collisionPadding={16}
			customAnchor={anchor}
			role="dialog"
			aria-label={label}
			class={cn(
				'z-50 flex w-[390px] max-w-[calc(100vw-48px)] flex-col gap-3.5 rounded-xl border border-line bg-surface p-4 text-ink shadow-pop outline-none',
				'motion-safe:data-[state=closed]:animate-out motion-safe:data-[state=closed]:fade-out-0 motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0 motion-safe:data-[state=open]:zoom-in-95',
				className
			)}
		>
			{@render children()}
			{#if pfeil}
				<Popover.Arrow>
					{#snippet child({ props })}
						<span
							{...props}
							class="block size-3 -translate-y-1.5 rotate-45 border-t border-l border-line bg-surface"
						></span>
					{/snippet}
				</Popover.Arrow>
			{/if}
		</Popover.Content>
	</Popover.Portal>
</Popover.Root>
