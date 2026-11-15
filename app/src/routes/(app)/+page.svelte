<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import DropzoneIllustration from '#lib/features/start/DropzoneIllustration.svelte';
	import MaskCard from '#lib/features/start/MaskCard.svelte';
	import TextMasker from '#lib/features/start/TextMasker.svelte';
	import { Dropzone } from '#lib/features/start/dropzone.svelte.js';
	import Card from '#lib/ui/Card.svelte';
	import DropZone from '#lib/ui/DropZone.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import { ablehnungsText } from '#lib/inputs/rejection.js';
	import { samePreset } from '#lib/masking/rules.js';
	import { GATE_TEXT, gateNotice } from '#lib/sidecar/gate-notice.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { settings } from '#lib/state/settings.svelte.js';
	import { help } from '#lib/state/help.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { start } from '#lib/state/start.svelte.js';

	/**
	 * Board “1 · New”: drop documents or mask text, plus the
	 * masking settings for new items.
	 */
	const dropzone = new Dropzone();
	onMount(() => dropzone.listen());
	onMount(() => void dropzone.acceptGeoeffnete());
	$effect(() => dropzone.nachholen());
	$effect(() => dropzone.completeRequest());

	// Show onboarding hints only after confirming no initial setup is required.
	let helpByItself = false;
	$effect(() => {
		if (sidecarGate.snapshot.setup_required !== false) return;
		if (help.firstStart()) helpByItself = true;
	});
	onMount(() => () => {
		if (helpByItself) help.ausschalten();
	});

	const notice = $derived(
		gateNotice({ snapshot: sidecarGate.snapshot, outside: sidecarGate.outside })
	);
	// During startup the area stays open; items dropped now are released
	// with the “Ablage.vorgemerkt” flag.
	const lock = $derived(
		!sidecarGate.mayUpload && notice && !dropzone.starts
			? t(GATE_TEXT[notice.position].lock)
			: undefined
	);
	const busy = $derived(
		dropzone.busy
			? t('dropzone.accept')
			: dropzone.queued
				? t('dropzone.queued', { n: dropzone.queued.names.length })
				: undefined
	);

	const inputKinds = $derived([
		{ value: 'documents' as const, label: t('start.input.documents'), icon: 'document' as const },
		{ value: 'text' as const, label: t('start.input.text'), icon: 'text' as const }
	]);

	// Apply to all new items: after first load this becomes the default;
	// otherwise the initial state would overwrite the saved default.
	$effect(() => {
		const preset = start.masking.asPreset();
		const saved = settings.values?.masking;
		const connection = sidecarGate.connection;
		if (!start.presetLoaded || !connection || !saved) return;
		if (samePreset(preset, saved)) return;
		const timer = setTimeout(() => {
			settings
				.change(connection, { masking: preset })
				.catch(() => toast(t('start.mask.saveError')));
		}, 400);
		return () => clearTimeout(timer);
	});
</script>

<div class="mx-auto flex max-w-[960px] flex-col gap-7 px-10 pt-16 pb-10">
	<div class="flex max-w-190 flex-col gap-3">
		<h1 class="font-serif text-display-lg font-normal tracking-[-0.015em] text-balance text-ink">
			{t('start.title.start')}<em class="text-link">{t('start.title.emphasized')}</em>{t(
				'start.title.end'
			)}
		</h1>
		<p class="max-w-160 text-title text-pretty text-muted">{t('start.text')}</p>
	</div>

	<Segmented
		options={inputKinds}
		bind:value={start.kind}
		label={t('start.input')}
		size="lg"
		class="self-start"
	/>

	{#if start.kind === 'documents'}
		<DropZone
			text={t('dropzone.text')}
			formats={t('dropzone.formats')}
			active={dropzone.zieht}
			locked={lock}
			{busy}
			onfiles={() => void dropzone.select(false)}
			onfolder={() => void dropzone.select(true)}
		>
			{#snippet illustration()}<DropzoneIllustration />{/snippet}
		</DropZone>
		{#if dropzone.message}
			<Card tone="amber" role="status" class="gap-2">
				<p class="font-semibold text-amber-ink">{dropzone.message.text}</p>
				{#if dropzone.message.rejected.length > 0}
					<ul class="flex flex-col gap-1 text-label text-muted">
						{#each dropzone.message.rejected as file, index (index)}
							<li>
								<span class="font-medium text-ink">{file.name}</span> · {t(
									ablehnungsText(file.code)
								)}
							</li>
						{/each}
					</ul>
				{/if}
			</Card>
		{/if}
	{:else}
		<TextMasker />
	{/if}

	<MaskCard />
</div>
