<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';

	/**
	 * Label, notice and error handling for an input field.
	 * The field receives `id` and `describedby` via the snippet and attaches them:
	 *
	 * ```svelte
	 * <Field label="Quelle">{#snippet children({ id })}<Select {id} … />{/snippet}</Field>
	 * ```
	 */
	let {
		label,
		notice,
		error,
		hidden = false,
		children,
		class: className
	}: {
		label: string;
		notice?: string;
		error?: string;
		/** Label only for screen readers. */
		hidden?: boolean;
		children: Snippet<[{ id: string; describedby: string | undefined; invalid: boolean }]>;
		class?: string;
	} = $props();

	const basis = $props.id();
	const id = `${basis}-field`;
	const noticeId = `${basis}-notice`;
	const errorId = `${basis}-error`;
	const describedby = $derived(
		[error ? errorId : null, notice ? noticeId : null].filter(Boolean).join(' ') || undefined
	);
</script>

<div class={cn('flex flex-col gap-1.5', className)}>
	<label for={id} class={hidden ? 'sr-only' : 'text-ui font-semibold text-ink'}>{label}</label>
	{@render children({ id, describedby, invalid: !!error })}
	{#if notice}<p id={noticeId} class="text-meta text-subtle">{notice}</p>{/if}
	{#if error}<p id={errorId} class="text-meta font-medium text-red-ink">{error}</p>{/if}
</div>
