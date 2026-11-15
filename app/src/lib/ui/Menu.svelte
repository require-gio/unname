<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';

	/**
	 * Dropdown menu with optional heading and entries. Used for the code menu in the
	 * document ("Keep only here" / "Keep all names"). Arrow keys navigate,
	 * Enter selects, Escape closes.
	 *
	 * When `anchor` is provided the menu attaches to the currently active element
	 * instead of each code block creating its own menu.
	 *
	 * No scroll lock: the menu follows its anchor while scrolling. With a lock the
	 * page would ignore clicks briefly after closing, causing immediate text selection
	 * to fail.
	 */
	export interface MenuEntry {
		label: string;
		onselect: () => void;
		danger?: boolean;
	}

	let {
		open = $bindable(false),
		title,
		entries,
		trigger,
		anchor,
		alignment = 'start',
		class: className
	}: {
		open?: boolean;
		title?: string;
		entries: readonly MenuEntry[];
		trigger?: Snippet<[Record<string, unknown>]>;
		anchor?: HTMLElement | SVGElement | null;
		alignment?: 'start' | 'end';
		class?: string;
	} = $props();
</script>

<DropdownMenu.Root bind:open>
	{#if trigger}
		<DropdownMenu.Trigger>
			{#snippet child({ props })}{@render trigger(props)}{/snippet}
		</DropdownMenu.Trigger>
	{/if}
	<DropdownMenu.Portal>
		<DropdownMenu.Content
			sideOffset={8}
			align={alignment}
			collisionPadding={16}
			customAnchor={anchor}
			preventScroll={false}
			class={cn(
				'z-50 flex min-w-[230px] flex-col gap-px rounded-lg border border-line bg-surface p-1.5 font-sans text-ui text-ink shadow-menu outline-none',
				'motion-safe:data-[state=closed]:animate-out motion-safe:data-[state=closed]:fade-out-0 motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0',
				className
			)}
		>
			<DropdownMenu.Group>
				{#if title}
					<DropdownMenu.GroupHeading
						class="px-2.5 pt-1.5 pb-1 text-caption font-semibold text-muted"
					>
						{title}
					</DropdownMenu.GroupHeading>
				{/if}
				{#each entries as entry (entry.label)}
					<DropdownMenu.Item
						onSelect={entry.onselect}
						class={cn(
							'flex min-h-9.5 cursor-pointer items-center rounded-sm px-2.5 outline-none select-none data-highlighted:bg-tint-1 data-highlighted:text-tint-ink',
							entry.danger && 'text-red-ink data-highlighted:bg-red-0 data-highlighted:text-red-ink'
						)}
					>
						{entry.label}
					</DropdownMenu.Item>
				{/each}
			</DropdownMenu.Group>
		</DropdownMenu.Content>
	</DropdownMenu.Portal>
</DropdownMenu.Root>
