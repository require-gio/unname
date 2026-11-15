<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import Button from './Button.svelte';
	import Icon from './Icon.svelte';
	import Spinner from './Spinner.svelte';

	/**
	 * Drop zone component with three layout variants:
	 *
	 * - `hero`: home page – illustration left, text and buttons right
	 * - `kompakt`: uploader inside viewer, file-restore centered
	 * - `inline`: single line (“Or drag a file from the AI here …”)
	 *
	 * The shell reads the files; `active` is true while something is dragged
	 * over the window. In the browser `onablegen` forwards the DOM event files.
	 * `locked` holds the reason (fail-closed gate) and then rejects input.
	 * `busy` indicates an ongoing operation and replaces the buttons.
	 * `knopf` renames the file button (“Choose file” for restore).
	 */
	let {
		variant = 'hero',
		title,
		text,
		formats,
		button,
		active = false,
		locked,
		busy,
		onfiles,
		onfolder,
		ondrop,
		illustration,
		class: className
	}: {
		variant?: 'hero' | 'kompakt' | 'inline';
		title?: string;
		text?: string;
		formats?: string;
		button?: string;
		active?: boolean;
		locked?: string;
		busy?: string;
		onfiles?: () => void;
		onfolder?: () => void;
		ondrop?: (files: File[]) => void;
		illustration?: Snippet;
		class?: string;
	} = $props();

	let about = $state(0);
	const hervorgehoben = $derived(!locked && (active || about > 0));

	function ziehen(event: DragEvent) {
		if (locked) return;
		event.preventDefault();
	}

	function drop(event: DragEvent) {
		event.preventDefault();
		about = 0;
		if (locked || !ondrop) return;
		const files = [...(event.dataTransfer?.files ?? [])];
		if (files.length > 0) ondrop(files);
	}
</script>

<section
	aria-label={title ?? t('dropzone.title')}
	data-locked={locked ? '' : undefined}
	ondragenter={() => (about += 1)}
	ondragleave={() => (about = Math.max(0, about - 1))}
	ondragover={ziehen}
	ondrop={drop}
	class={cn(
		'relative bg-surface transition-colors',
		variant !== 'inline' &&
			'bg-[radial-gradient(var(--dots)_1px,transparent_1px)] bg-size-[18px_18px]',
		hervorgehoben
			? 'border-2 border-dashed border-accent bg-tint-1'
			: 'border-[1.5px] border-dashed border-line-dash',
		locked && 'opacity-70',
		variant === 'hero' && 'flex flex-wrap items-center gap-11 rounded-xl px-11 py-9',
		variant === 'kompakt' &&
			'flex flex-col items-center gap-3.5 rounded-xl px-8 pt-12 pb-10 text-center',
		variant === 'inline' &&
			'flex flex-wrap items-center justify-between gap-3 rounded-md px-4 py-3 text-muted',
		className
	)}
>
	{#if variant === 'inline'}
		<span class="inline-flex items-center gap-2.5">
			<Icon name="fileHigh" size={18} strokeWidth={1.8} />{text ?? title}
		</span>
		{#if onfiles && !locked}
			<Button variant="link" size="sm" onclick={onfiles}>{button ?? t('dropzone.files')}</Button>
		{/if}
	{:else}
		{#if variant === 'hero' && illustration}
			<div aria-hidden="true" class="shrink-0">{@render illustration()}</div>
		{/if}
		{#if variant === 'kompakt'}
			<span
				aria-hidden="true"
				class="inline-flex size-14 items-center justify-center rounded-full bg-tint-2 text-link"
			>
				<Icon name="upload" size={26} strokeWidth={1.9} />
			</span>
		{/if}
		<div
			class={cn(
				'flex min-w-0 flex-col gap-3.5',
				variant === 'hero' ? 'flex-[1_1_320px]' : 'items-center'
			)}
		>
			<h2 class="text-heading font-semibold tracking-[-0.015em]">{title ?? t('dropzone.title')}</h2>
			{#if text}
				<p
					class={cn(
						'text-lead text-pretty text-muted',
						variant === 'hero' ? 'max-w-[460px]' : 'max-w-[400px]'
					)}
				>
					{text}
				</p>
			{/if}
			{#if locked}
				<p role="status" class="inline-flex items-center gap-2 font-medium text-amber-ink-2">
					<Icon name="warning" size={16} />{locked}
				</p>
			{:else if busy}
				<p
					role="status"
					class="inline-flex h-control-lg items-center gap-2.5 font-medium text-link"
				>
					<Spinner size={18} />{busy}
				</p>
			{:else}
				<div class="flex flex-wrap items-center gap-2.5">
					{#if onfiles}
						<Button
							variant={button ? 'secondary' : 'primary'}
							size={button ? 'md' : 'lg'}
							icon={button ? undefined : 'upload'}
							onclick={onfiles}
						>
							{button ?? t('dropzone.files')}
						</Button>
					{/if}
					{#if onfolder}
						<Button variant="secondary" size="lg" onclick={onfolder}>{t('dropzone.folder')}</Button>
					{/if}
				</div>
			{/if}
			{#if formats}<p class="text-label text-subtle">{formats}</p>{/if}
		</div>
	{/if}
</section>
