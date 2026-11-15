<script lang="ts">
	import { isTauri } from '@tauri-apps/api/core';
	import { toast } from 'svelte-sonner';
	import { readClipboard } from '#lib/sidecar/inputs.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import Button from '#lib/ui/Button.svelte';
	import Card from '#lib/ui/Card.svelte';
	import DropZone from '#lib/ui/DropZone.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import Textarea from '#lib/ui/Textarea.svelte';
	import { coachmark } from '#lib/ui/coachmark.svelte.js';

	/**
	 * UI for restoring a KI answer from a reversible entry (Board “4 · saved”).
	 * Light style with indigo border to show it is active.
	 *
	 * Only gathers the answer or file; the actual restore runs in its own view,
	 * in memory only. Files can be added via “Choose file” or by dropping onto
	 * the window (drag).
	 */
	let {
		locked = false,
		fileLocked = false,
		zieht = false,
		onrestore,
		onpickfile
	}: {
		locked?: boolean;
		fileLocked?: boolean;
		zieht?: boolean;
		onrestore: (answer: string) => void;
		onpickfile: () => void;
	} = $props();

	const fieldId = $props.id();
	let answer = $state('');
	let reads = $state(false);

	async function insert(): Promise<void> {
		if (reads) return;
		reads = true;
		try {
			const text = isTauri() ? await readClipboard() : await navigator.clipboard.readText();
			if (text?.trim()) answer = text;
			else toast(t('returnPath.empty'));
		} catch {
			toast(t('returnPath.empty'));
		} finally {
			reads = false;
		}
	}
</script>

<Card as="section" tone="accent" radius="xl" gap="lg" aria-labelledby="{fieldId}-title">
	<div class="flex items-center gap-3.5">
		<span
			aria-hidden="true"
			class="inline-flex size-11 shrink-0 items-center justify-center rounded-full bg-tint-2 text-link"
		>
			<Icon name="restore" size={20} />
		</span>
		<div class="flex flex-col gap-0.5">
			<span class="text-caption font-semibold tracking-[0.04em] text-link uppercase">
				{t('returnPath.eyebrow')}
			</span>
			<h2 id="{fieldId}-title" class="font-serif text-heading font-normal tracking-[-0.01em]">
				{t('returnPath.title')}
			</h2>
		</div>
	</div>
	<div {@attach coachmark(1, 'help.saved.answer', { page: 'right' })}>
		<label for={fieldId} class="sr-only">{t('returnPath.input')}</label>
		<Textarea
			id={fieldId}
			bind:value={answer}
			font="codes"
			spellcheck={false}
			placeholder={t('returnPath.placeholder')}
			class="h-30"
		/>
	</div>
	<DropZone
		variant="inline"
		text={t('returnPath.file')}
		button={t('returnPath.selectFile')}
		active={zieht}
		locked={fileLocked ? t('dropzone.locked') : undefined}
		onfiles={onpickfile}
	/>
	<div
		class="flex flex-wrap items-center justify-end gap-2"
		{@attach coachmark(2, 'help.restore.storage', { page: 'top' })}
	>
		<Button variant="secondary" size="lg" icon="insert" busy={reads} onclick={insert}>
			{t('returnPath.insert')}
		</Button>
		<Button
			variant="primary"
			size="lg"
			iconRight="next"
			disabled={locked || answer.trim() === ''}
			onclick={() => onrestore(answer)}
		>
			{t('returnPath.go')}
		</Button>
	</div>
</Card>
