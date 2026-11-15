<script lang="ts">
	import type { Segment } from '#lib/ui/segments.js';
	import type { FileRestoration } from '#lib/sidecar/restore.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import Button from '#lib/ui/Button.svelte';
	import FileRow from '#lib/ui/FileRow.svelte';
	import SegmentText from '#lib/ui/SegmentText.svelte';
	import Switch from '#lib/ui/Switch.svelte';
	import { coachmark } from '#lib/ui/coachmark.svelte.js';
	import { cn } from '#lib/utils.js';
	import { folderShort } from '#lib/saved/saved.js';
	import type { Eingabeart } from './RestoreInput.svelte';

	/**
	 * "With real names": restored answer with highlights –
	 * indigo for this entry, green for others, unknown codes in amber.
	 * For a file: show the new file with "Open" and "Save as …"
	 * plus a preview below.
	 */
	let {
		mode,
		mark = $bindable(true),
		segments,
		running = false,
		error = null,
		stale = false,
		file,
		saved = null,
		saves = false,
		onopen,
		onsave
	}: {
		mode: Eingabeart;
		mark?: boolean;
		segments: readonly Segment[];
		running?: boolean;
		error?: string | null;
		/** The output still belongs to the previous input. */
		stale?: boolean;
		file: FileRestoration | null;
		/** Path where the new file will be saved. */
		saved?: string | null;
		saves?: boolean;
		onopen: () => void;
		onsave: () => void;
	} = $props();

	const id = $props.id();
</script>

<section
	aria-labelledby="{id}-title"
	class="relative flex min-w-0 flex-col rounded-xl border-[1.5px] border-accent bg-surface halo-accent"
>
	<div
		class="flex min-h-15 items-center justify-between gap-2.5 border-b border-line-soft px-4 py-3"
		{@attach coachmark(1, 'help.restore.view', { page: 'bottom' })}
	>
		<h2 id="{id}-title" class="text-body font-semibold">{t('restore.output')}</h2>
		<span class="inline-flex items-center gap-2.5 text-label text-muted">
			<span id="{id}-mark">{t('restore.mark')}</span>
			<Switch bind:checked={mark} labelledby="{id}-markieren" />
		</span>
	</div>
	{#if mode === 'text'}
		<div
			class="h-110 overflow-auto px-5.5 py-4.5 font-doc text-doc leading-[1.65] text-doc-ink"
			aria-live="polite"
			aria-busy={running}
		>
			{#if error}
				<p role="alert" class="font-sans text-body text-red-ink">{error}</p>
			{:else if segments.length > 0}
				<SegmentText
					{segments}
					{mark}
					peek={false}
					class={cn(stale && 'opacity-60 transition-opacity')}
				/>
			{:else}
				<p class="font-sans text-body text-subtle">{t('restore.empty')}</p>
			{/if}
		</div>
	{:else}
		<div class="flex h-110 flex-col gap-3 overflow-auto p-4.5">
			{#if file?.output}
				<ul aria-label={t('restore.output')}>
					<FileRow variant="kompakt" tone="tint" name={file.output.name}>
						{#snippet metaContent()}
							{#if saved}
								<span title={saved}
									>{t('restore.file.savedIn', {
										place: folderShort(saved.replace(/[\\/][^\\/]*$/, ''))
									})}</span
								>
							{:else}
								{t('restore.file.new')}
							{/if}
						{/snippet}
						{#snippet right()}
							<span class="ml-auto flex gap-2">
								<Button size="sm" disabled={saves} onclick={onopen}>
									{t('restore.file.open')}
								</Button>
								<Button variant="primary" size="sm" busy={saves} onclick={onsave}>
									{t('restore.file.saveUnder')}
								</Button>
							</span>
						{/snippet}
					</FileRow>
				</ul>
			{:else if file}
				<p class="text-body text-muted">{t('restore.file.noNew')}</p>
			{/if}
			{#if file && file.unreplaced > 0}
				<p class="text-label text-amber-ink-2">
					{t('restore.file.notReplaceable', { n: file.unreplaced })}
				</p>
			{/if}
			{#if file}
				<span class="text-fine font-semibold tracking-[0.04em] text-subtle uppercase">
					{t('restore.file.preview')}
				</span>
				<div
					class="rounded-xs bg-surface px-6.5 py-5.5 font-doc text-lead leading-[1.6] text-doc-ink shadow-page"
				>
					<SegmentText {segments} {mark} peek={false} />
				</div>
				{#if file.preview.truncated}
					<p class="text-label text-subtle">{t('restore.file.shortened')}</p>
				{/if}
			{:else}
				<p class="text-body text-subtle">{t('restore.empty')}</p>
			{/if}
		</div>
	{/if}
</section>
