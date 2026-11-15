<script lang="ts">
	import { AlertDialog } from 'bits-ui';
	import { cn } from '#lib/utils.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { button } from './Button.svelte';

	/**
	 * Confirmation before a consequential action.
	 * The dialog lists the effects instead of just asking “Are you sure?”.
	 *
	 * Focus starts on “Cancel” – the safe choice is highlighted.
	 */
	let {
		open = $bindable(false),
		title,
		description,
		sequences = [],
		confirm,
		cancel,
		danger = false,
		onconfirm
	}: {
		open?: boolean;
		title: string;
		description: string;
		/** Each entry describes a post-action change. */
		sequences?: readonly string[];
		confirm: string;
		cancel?: string;
		danger?: boolean;
		onconfirm: () => void;
	} = $props();

	let cancelButton = $state<HTMLButtonElement | null>(null);
</script>

<AlertDialog.Root bind:open>
	<AlertDialog.Portal>
		<AlertDialog.Overlay
			class="fixed inset-0 z-50 bg-scrim motion-safe:data-[state=closed]:animate-out motion-safe:data-[state=closed]:fade-out-0 motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0"
		/>
		<AlertDialog.Content
			onOpenAutoFocus={(event) => {
				event.preventDefault();
				cancelButton?.focus();
			}}
			class="fixed top-1/2 left-1/2 z-50 flex w-[calc(100vw-48px)] max-w-[440px] -translate-x-1/2 -translate-y-1/2 flex-col gap-4 rounded-xl bg-surface p-6 text-ink shadow-dialog outline-none motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0 motion-safe:data-[state=open]:zoom-in-95"
		>
			<div class="flex flex-col gap-1.5">
				<AlertDialog.Title class="text-title font-semibold">{title}</AlertDialog.Title>
				<AlertDialog.Description class="text-pretty text-muted"
					>{description}</AlertDialog.Description
				>
			</div>
			{#if sequences.length > 0}
				<ul
					class={cn(
						'flex flex-col gap-1.5 rounded-md border px-4 py-3 text-ui',
						danger
							? 'border-red-line-soft bg-red-0 text-ink-2'
							: 'border-line bg-surface-3 text-ink-2'
					)}
				>
					{#each sequences as sequence (sequence)}
						<li class="flex gap-2">
							<span aria-hidden="true" class={danger ? 'text-red-ink' : 'text-subtle'}>–</span
							>{sequence}
						</li>
					{/each}
				</ul>
			{/if}
			<div class="flex justify-end gap-2">
				<AlertDialog.Cancel bind:ref={cancelButton} class={button({ variant: 'ghost' })}>
					{cancel ?? t('common.cancel')}
				</AlertDialog.Cancel>
				<AlertDialog.Action
					class={button({ variant: danger ? 'danger' : 'primary' })}
					onclick={() => {
						onconfirm();
						open = false;
					}}
				>
					{confirm}
				</AlertDialog.Action>
			</div>
		</AlertDialog.Content>
	</AlertDialog.Portal>
</AlertDialog.Root>
