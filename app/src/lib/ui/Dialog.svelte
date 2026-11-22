<script lang="ts">
	import { Dialog } from 'bits-ui';
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import IconButton from './IconButton.svelte';

	/**
	 * Modal dialog: settings, uploader. Handles focus trap,
	 * Escape and focus restoration via Bits UI.
	 *
	 * `withoutFrame` lets the content provide its own layout —
	 * the settings dialog adds navigation and header automatically.
	 */
	let {
		open = $bindable(false),
		title,
		description,
		width = 'md',
		withoutFrame = false,
		children,
		fuss,
		onOpenFocus,
		onCloseFocus,
		class: className
	}: {
		open?: boolean;
		title: string;
		description?: string;
		width?: 'sm' | 'md' | 'lg' | 'xl';
		withoutFrame?: boolean;
		children: Snippet;
		fuss?: Snippet;
		/**
		 * Where focus should go on opening; call `event.preventDefault()` and
		 * focus it yourself. Otherwise Bits UI focuses the first tabbable
		 * element one frame later, which is the close button.
		 */
		onOpenFocus?: (event: Event) => void;
		/**
		 * Where focus should go after closing; call `event.preventDefault()`
		 * to keep it (e.g., if the trigger element was removed).
		 */
		onCloseFocus?: (event: Event) => void;
		class?: string;
	} = $props();

	const WIDTH = {
		sm: 'max-w-[440px]',
		md: 'max-w-[560px]',
		lg: 'max-w-[760px]',
		xl: 'max-w-[1100px]'
	} as const;
</script>

<Dialog.Root bind:open>
	<Dialog.Portal>
		<Dialog.Overlay
			class="fixed inset-0 z-50 bg-scrim motion-safe:data-[state=closed]:animate-out motion-safe:data-[state=closed]:fade-out-0 motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0"
		/>
		<Dialog.Content
			onOpenAutoFocus={onOpenFocus}
			onCloseAutoFocus={onCloseFocus}
			class={cn(
				'fixed top-1/2 left-1/2 z-50 flex max-h-[calc(100vh-80px)] w-[calc(100vw-80px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl bg-surface text-ink shadow-dialog outline-none',
				'motion-safe:data-[state=closed]:animate-out motion-safe:data-[state=closed]:fade-out-0 motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0 motion-safe:data-[state=open]:zoom-in-95',
				WIDTH[width],
				className
			)}
		>
			{#if withoutFrame}
				<Dialog.Title class="sr-only">{title}</Dialog.Title>
				{#if description}<Dialog.Description class="sr-only">{description}</Dialog.Description>{/if}
				{@render children()}
			{:else}
				<div class="flex items-start justify-between gap-4 px-6 pt-6 pb-4">
					<div class="flex flex-col gap-1">
						<Dialog.Title class="text-heading font-semibold tracking-[-0.015em]"
							>{title}</Dialog.Title
						>
						{#if description}
							<Dialog.Description class="text-pretty text-muted">{description}</Dialog.Description>
						{/if}
					</div>
					<Dialog.Close>
						{#snippet child({ props })}
							<IconButton {...props} label={t('common.close')} icon="close" variant="quiet" />
						{/snippet}
					</Dialog.Close>
				</div>
				<div class="min-h-0 flex-1 overflow-auto px-6 pb-6">{@render children()}</div>
				{#if fuss}
					<div class="flex justify-end gap-2 border-t border-line-soft bg-surface-2 px-6 py-4">
						{@render fuss()}
					</div>
				{/if}
			{/if}
		</Dialog.Content>
	</Dialog.Portal>
</Dialog.Root>
