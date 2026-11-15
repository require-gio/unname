<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import type { FileKind } from './file.js';
	import FileBadge from './FileBadge.svelte';
	import Icon from './Icon.svelte';
	import Progress from './Progress.svelte';

	/**
	 * Render a file as a row: badge, name, meta, status, actions.
	 *
	 * - `map`: detection list – own card, progress bar, status on the right;
	 *   `highlighted` for the file currently processing
	 * - `list`: saved list, uploader – separator line instead of card
	 * - `compact`: single file during restore
	 */
	let {
		name,
		kind,
		meta,
		status,
		statusText,
		progress,
		variant = 'list',
		hervorgehoben = false,
		tone = 'standard',
		right,
		metaContent,
		class: className
	}: {
		name: string;
		kind?: FileKind;
		meta?: string;
		status?: 'waits' | 'running' | 'done' | 'error';
		/** Text shown on the right; defaults are used per status if omitted. */
		statusText?: string;
		/** 0–100; only used in `map`. */
		progress?: number;
		variant?: 'map' | 'list' | 'kompakt';
		hervorgehoben?: boolean;
		tone?: 'standard' | 'tint';
		right?: Snippet;
		/** Use instead of `meta` when the row needs custom highlights. */
		metaContent?: Snippet;
		class?: string;
	} = $props();

	const STATUS_TEXT = {
		waits: 'file.waits',
		running: 'file.running',
		done: 'file.done',
		error: 'file.error'
	} as const;
</script>

{#snippet metaLine()}
	{#if metaContent}
		<span class="text-label text-muted">{@render metaContent()}</span>
	{:else if meta}
		<span class={cn('text-muted', variant === 'map' ? 'text-label' : 'text-meta')}>{meta}</span>
	{/if}
{/snippet}

{#snippet statusDisplay()}
	{#if status}
		<span
			class={cn(
				'inline-flex items-center gap-1.5 text-label font-semibold',
				status === 'done' && 'text-ink',
				status === 'running' && 'text-link motion-safe:animate-pulse',
				status === 'waits' && 'font-medium text-subtle',
				status === 'error' && 'text-red-ink'
			)}
		>
			{#if status === 'done'}<Icon name="haken" size={15} strokeWidth={2.2} />{/if}
			{#if status === 'error'}<Icon name="warning" size={15} />{/if}
			{statusText ?? t(STATUS_TEXT[status])}
		</span>
	{/if}
{/snippet}

{#if variant === 'map'}
	<li
		class={cn(
			'flex items-start gap-4 rounded-lg bg-surface px-4.5 py-4',
			hervorgehoben ? 'border-[1.5px] border-accent halo-accent' : 'border border-line',
			className
		)}
	>
		<FileBadge {name} {kind} size="large" />
		<div class="flex min-w-0 flex-1 flex-col gap-2">
			<div class="flex flex-wrap justify-between gap-3">
				<span class="min-w-0 truncate font-semibold">{name}</span>
				{@render statusDisplay()}
			</div>
			{#if progress !== undefined}
				<Progress value={progress} label={name} tone={status === 'done' ? 'ink' : 'accent'} />
			{/if}
			{@render metaLine()}
			{@render right?.()}
		</div>
	</li>
{:else}
	<li
		class={cn(
			'flex flex-wrap items-center gap-3.5',
			variant === 'list' && 'border-b border-line-soft px-0.5 py-3',
			variant === 'kompakt' && 'rounded-md border px-3.5 py-3',
			variant === 'kompakt' &&
				(tone === 'tint' ? 'border-tint-line-soft bg-tint-1' : 'border-line bg-surface'),
			className
		)}
	>
		<FileBadge {name} {kind} size="mittel" />
		<span class="flex min-w-0 flex-[1_1_220px] flex-col">
			<span class="truncate font-semibold">{name}</span>
			{@render metaLine()}
		</span>
		{@render statusDisplay()}
		{@render right?.()}
	</li>
{/if}
