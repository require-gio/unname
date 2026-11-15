<script lang="ts">
	import type { FileRestoration } from '#lib/sidecar/restore.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import Button from '#lib/ui/Button.svelte';
	import DropZone from '#lib/ui/DropZone.svelte';
	import FileRow from '#lib/ui/FileRow.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import Textarea from '#lib/ui/Textarea.svelte';
	import { fileMeta } from '#lib/restore/restore.js';

	export type Eingabeart = 'text' | 'file';

	/**
	 * AI response: paste text or drop an AI-generated file.
	 * The shell reads the file; only its name and the sidecar findings are shown.
	 */
	let {
		mode = $bindable(),
		text = $bindable(),
		file,
		fileRunning = false,
		fileError = null,
		zieht = false,
		locked,
		onclear,
		onpickfile,
		onremove
	}: {
		mode: Eingabeart;
		text: string;
		file: FileRestoration | null;
		fileRunning?: boolean;
		fileError?: string | null;
		zieht?: boolean;
		/** Reason why a file is currently rejected (gate). */
		locked?: string;
		onclear: () => void;
		onpickfile: () => void;
		onremove: () => void;
	} = $props();

	const id = $props.id();
	const kinds = $derived([
		{ value: 'text' as const, label: t('restore.kind.text') },
		{ value: 'file' as const, label: t('restore.kind.file') }
	]);
	const empty = $derived(mode === 'text' ? text === '' : file === null);
</script>

<section
	aria-labelledby="{id}-title"
	class="flex min-w-0 flex-col overflow-hidden rounded-xl border border-line bg-surface"
>
	<div
		class="flex min-h-15 items-center justify-between gap-2.5 border-b border-line-soft px-4 py-3"
	>
		<div class="flex flex-wrap items-center gap-3">
			{#if mode === 'text'}
				<label id="{id}-title" for="{id}-field" class="text-body font-semibold"
					>{t('restore.input')}</label
				>
			{:else}
				<h2 id="{id}-title" class="text-body font-semibold">{t('restore.input')}</h2>
			{/if}
			<Segmented options={kinds} bind:value={mode} label={t('restore.kind')} size="sm" />
		</div>
		<Button variant="ghost" size="sm" disabled={empty} onclick={onclear}>
			{t('restore.clear')}
		</Button>
	</div>
	{#if mode === 'text'}
		<Textarea
			id="{id}-field"
			bind:value={text}
			font="codes"
			eingebettet
			spellcheck={false}
			placeholder={t('restore.placeholder')}
			class="h-110 bg-field"
		/>
	{:else}
		<div class="flex h-110 flex-col gap-3 bg-field p-4.5">
			<DropZone
				variant="kompakt"
				title={t('restore.file.drop')}
				text={t('restore.file.formats')}
				button={t('restore.file.select')}
				active={zieht}
				{locked}
				busy={fileRunning ? t('restore.file.running') : undefined}
				onfiles={onpickfile}
				class="min-h-0 flex-1 justify-center py-6"
			/>
			{#if fileError}
				<p role="alert" class="text-body text-red-ink">{fileError}</p>
			{/if}
			{#if file}
				<ul aria-label={t('restore.input')}>
					<FileRow variant="kompakt" name={file.source.name} meta={fileMeta(file, t)}>
						{#snippet right()}
							<Button variant="ghost" size="sm" onclick={onremove}>
								{t('common.remove')}
							</Button>
						{/snippet}
					</FileRow>
				</ul>
			{/if}
		</div>
	{/if}
</section>
