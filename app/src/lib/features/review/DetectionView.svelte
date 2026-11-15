<script lang="ts">
	import HistoryBackLink from '#lib/features/history/HistoryBackLink.svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import Button from '#lib/ui/Button.svelte';
	import FileRow from '#lib/ui/FileRow.svelte';
	import StatGrid from '#lib/ui/StatGrid.svelte';
	import { ablehnungsText, missingOcr } from '#lib/inputs/rejection.js';
	import {
		previousFound,
		fileMeta,
		detectionFinding,
		maskingLine,
		phasesText
	} from '#lib/review/detection.js';
	import { entryViewModel } from '#lib/state/entry.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { fileStatus } from '#lib/history/entry-meta.js';
	import ScanIllustration from './ScanIllustration.svelte';
	import OcrNotice from './OcrNotice.svelte';

	/**
	 * Board "2 · Detection running": files with status and interim count,
	 * "Found so far" across all files, including masking, "Cancel"
	 * and "Review" — show as soon as the first file finishes, not only at the end.
	 *
	 * When everything is done, the view stays as the entry overview;
	 * "Back to overview" in the review step returns here.
	 */
	const view = entryViewModel;

	const entry = $derived(view.entry);
	const progress = $derived(view.progress);
	const running = $derived(view.running);
	const files = $derived(entry?.files ?? []);
	const ready = $derived(progress?.ready ?? files.some((file) => file.status === 'review'));
	const failed = $derived(
		!running && files.length > 0 && files.every((file) => file.status !== 'review')
	);
	const inputs = $derived(page.state.inputs);
	let breaks = $state(false);

	const lines = $derived(
		files.map((file) => {
			const status = progress?.files.find((item) => item.id === file.id);
			const fileState = fileStatus(status ?? file);
			const phase =
				fileState.status === 'running' ? phasesText(status?.phase, file.name, t) : undefined;
			return {
				id: file.id,
				name: file.name,
				fileStatus: file.status,
				meta: fileMeta(
					file,
					status ?? {
						status: file.status ?? 'queued',
						counts: file.counts ?? {},
						questions: null
					},
					t
				),
				status: fileState.status,
				statusText:
					phase ??
					(fileState.status === 'done'
						? t('file.done')
						: fileState.text
							? t(fileState.text)
							: undefined),
				progress:
					fileState.status === 'running'
						? (status?.progress ?? 0)
						: fileState.status === 'done'
							? 100
							: undefined
			};
		})
	);

	const finding = $derived(
		detectionFinding(
			running,
			files.map((file) => {
				const status = progress?.files.find((item) => item.id === file.id);
				return {
					status: status?.status ?? file.status ?? 'queued',
					counts: status?.counts ?? file.counts ?? {},
					questions: status?.questions ?? null
				};
			})
		)
	);

	const withoutOcr = $derived(
		missingOcr([
			...files.map((file) => file.error_code),
			...(progress?.files.map((file) => file.error_code) ?? []),
			...(inputs?.rejected.map((file) => file.code) ?? [])
		])
	);
	const values = $derived(previousFound(progress?.totals, t));
	const line = $derived(maskingLine(view.masking.method, view.masking.all, view.istStandard, t));
	const reviewTarget = $derived(
		entry ? resolve('/(app)/entry/[id]/review', { id: entry.id }) : resolve('/(app)')
	);

	async function cancel(): Promise<void> {
		if (breaks) return;
		breaks = true;
		try {
			const deleted = await view.cancel();
			toast(t('detection.cancelled'));
			if (deleted) await goto(resolve('/(app)'));
		} catch {
			toast.error(t('detection.cancelError'));
		} finally {
			breaks = false;
		}
	}

	async function retryFile(fileId: string): Promise<void> {
		try {
			await view.retryFile(fileId);
		} catch {
			toast.error(t('review.file.retryError'));
		}
	}

	async function removeFile(fileId: string): Promise<void> {
		try {
			await view.removeFile(fileId);
		} catch {
			toast.error(t('review.file.removeError'));
		}
	}

	async function chooseOriginal(): Promise<void> {
		try {
			await view.reimportOriginals(t('saved.dialog.originals'));
		} catch {
			toast.error(t('saved.newImportError'));
		}
	}
</script>

<div class="mx-auto flex max-w-[1060px] flex-wrap items-start gap-13 px-8 pt-14 pb-10">
	<ScanIllustration {finding} />

	<div class="flex min-w-0 flex-[1_1_480px] flex-col gap-6.5">
		<div class="flex flex-col gap-2.5">
			<HistoryBackLink class="mb-1" />
			<h1 class="font-serif text-display-lg font-normal tracking-[-0.015em] text-ink">
				{running
					? t('detection.title')
					: failed
						? t('detection.failed.title')
						: t('detection.done.title')}
			</h1>
			<p class="text-title text-pretty text-muted">
				{running
					? t('detection.text')
					: failed
						? t('detection.failed.text')
						: t('detection.done.text')}
			</p>
		</div>

		<ul aria-label={t('entry.files')} class="flex flex-col gap-2.5">
			{#each lines as file (file.id)}
				<FileRow
					variant="map"
					name={file.name}
					status={file.status}
					statusText={file.statusText}
					progress={file.progress}
					hervorgehoben={file.status === 'running'}
				>
					{#snippet metaContent()}
						{file.meta.text}{#if file.meta.questions}{file.meta.text ? ' · ' : ''}<strong
								class="font-semibold text-amber-ink-2">{file.meta.questions}</strong
							>{/if}
					{/snippet}
					{#snippet right()}
						{#if file.fileStatus === 'failed' || file.fileStatus === 'cancelled'}
							<div class="flex flex-wrap gap-2 pt-1">
								<Button
									variant="secondary"
									size="sm"
									busy={view.pendingFiles.has(file.id)}
									disabled={!sidecarGate.mayUpload}
									onclick={() => retryFile(file.id)}
								>
									{t('review.file.retry')}
								</Button>
							</div>
						{:else if file.fileStatus === 'unavailable'}
							<p class="text-label text-muted">{t('review.file.unavailable')}</p>
							<div class="flex flex-wrap gap-2 pt-1">
								<Button
									variant="secondary"
									size="sm"
									icon="fileHigh"
									busy={view.rereads}
									disabled={!sidecarGate.mayUpload}
									onclick={chooseOriginal}
								>
									{t('review.file.chooseOriginal')}
								</Button>
								<Button
									variant="ghost"
									size="sm"
									busy={view.pendingFiles.has(file.id)}
									onclick={() => removeFile(file.id)}
								>
									{t('review.file.remove')}
								</Button>
							</div>
						{/if}
					{/snippet}
				</FileRow>
			{/each}
		</ul>

		{#if inputs && (inputs.rejected.length > 0 || inputs.skipped > 0 || inputs.shortened)}
			<section aria-labelledby="entry-rejected" class="flex flex-col gap-2">
				<h2 id="entry-rejected" class="text-title font-semibold">{t('entry.rejected')}</h2>
				{#if inputs.rejected.length > 0}
					<ul class="flex flex-col">
						{#each inputs.rejected as file, index (index)}
							<FileRow name={file.name} status="error" statusText={t(ablehnungsText(file.code))} />
						{/each}
					</ul>
				{/if}
				{#if inputs.skipped > 0}
					<p class="text-label text-muted">
						{t('dropzone.skipped', { n: inputs.skipped })}
					</p>
				{/if}
				{#if inputs.shortened}
					<p class="text-label text-muted">{t('dropzone.shortened', { max: 200 })}</p>
				{/if}
			</section>
		{/if}

		{#if withoutOcr}
			<OcrNotice />
		{/if}

		{#if values.length > 0}
			<StatGrid title={t('detection.previousFound')} {values} />
		{/if}

		<div class="flex flex-wrap items-center justify-between gap-4 pt-1">
			<span class="text-label text-subtle">{line}</span>
			<div class="flex flex-wrap gap-2.5">
				{#if running}
					<Button variant="ghost" size="lg" busy={breaks} onclick={cancel}>
						{t('common.cancel')}
					</Button>
				{/if}
				{#if ready}
					<Button href={reviewTarget} variant="primary" size="lg" iconRight="next">
						{t('detection.review')}
					</Button>
				{:else if files.length === 0}
					<Button href={reviewTarget} variant="primary" size="lg" icon="fileHigh">
						{t('review.further')}
					</Button>
				{/if}
			</div>
		</div>
	</div>
</div>
