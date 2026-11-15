<script lang="ts">
	import HistoryBackLink from '#lib/features/history/HistoryBackLink.svelte';
	import { isTauri } from '@tauri-apps/api/core';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { toast } from 'svelte-sonner';
	import { timePoint } from '#lib/format.js';
	import { entryReport, entryReportAsText } from '#lib/export/entry-report.js';
	import {
		reportFileName,
		savedNum,
		savedLines,
		methodText,
		folderShort,
		returnPath,
		sinceTheSaveChanged,
		savings,
		summary,
		type Saving
	} from '#lib/saved/saved.js';
	import { fetchEntryReport } from '#lib/sidecar/api-v2.js';
	import { listenDropzone, selectFiles, type Selection } from '#lib/sidecar/inputs.js';
	import type { Entry } from '#lib/sidecar/api.gen.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { openFolder, saveTextFile } from '#lib/sidecar/save.js';
	import { entryViewModel } from '#lib/state/entry.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import Button from '#lib/ui/Button.svelte';
	import Card from '#lib/ui/Card.svelte';
	import FileRow from '#lib/ui/FileRow.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import MaskLabel from '#lib/ui/MaskLabel.svelte';
	import RestoreCard from './RestoreCard.svelte';

	/**
	 * Board “4 · Saved”: shows where the entry was stored, the files with
	 * labels, the decision made, and the “Save report” action. Below that
	 * are the available actions: restore (if reversible) or an explanation
	 * why restoration is not possible.
	 *
	 * “Edit again” jumps straight to the review step. The draft keeps the
	 * original, extraction and detection data across restarts. Only legacy
	 * items or unreadable copies need the original re-imported; the shell
	 * handles that first.
	 *
	 * A saved entry is a draft with exports. The latest save appears at the
	 * top, earlier saves follow—each with its folder and report. If work has
	 * continued since the last save, a notice indicates that.
	 */
	let { entry }: { entry: Entry } = $props();

	const view = entryViewModel;
	const all = $derived(savings(entry));
	const last = $derived(all[0] ?? null);
	const earlier = $derived(all.slice(1));
	const lines = $derived(last ? savedLines(last, t, entry) : []);
	const num = $derived(last ? savedNum(last) : 0);
	const parts = $derived(last ? summary(last, t) : []);
	const weg = $derived(returnPath(entry));
	const folder = $derived(last?.folder ?? entry.output_dir ?? null);
	const changed = $derived(sinceTheSaveChanged(entry));
	let reportRunning = $state<string | null>(null);
	let bearbeitet = $state(false);
	let zieht = $state(false);

	const reviewTarget = (number?: number) =>
		`${resolve('/(app)/entry/[id]/review', { id: entry.id })}${number ? `?file=${number}` : ''}`;

	async function editAgain(): Promise<void> {
		if (bearbeitet) return;
		bearbeitet = true;
		try {
			if (view.filesNeedingOriginal.length > 0) {
				const result = await view.reimportOriginals(t('saved.dialog.originals'));
				if (result) {
					if (result.replaced.length > 0) {
						toast(t('saved.newImported', { n: result.replaced.length }));
					}
					const missing = [...result.missing, ...result.failed];
					if (missing.length > 0) {
						toast.error(t('saved.missing', { n: missing.length, names: missing.join(', ') }));
					}
				}
			}
			await goto(reviewTarget());
		} catch {
			toast.error(t('saved.newImportError'));
		} finally {
			bearbeitet = false;
		}
	}

	async function open(path: string | null = folder): Promise<void> {
		if (!path) return;
		try {
			await openFolder(path);
		} catch {
			toast.error(t('saved.folderError'));
		}
	}

	/** Key for the current run: the saving, otherwise the last. */
	const reportKey = (saving: Saving | null) => saving?.id ?? 'last';

	async function saveReport(saving: Saving | null = last): Promise<void> {
		const connection = sidecarGate.connection;
		if (!connection || reportRunning) return;
		reportRunning = reportKey(saving);
		try {
			const facts = await fetchEntryReport(connection, entry.id, saving?.id ?? undefined);
			const text = entryReportAsText(entryReport(facts, t, (iso) => timePoint(new Date(iso))));
			const path = await saveTextFile(t('saved.report.title'), reportFileName(entry, t), text);
			if (path) toast(t('saved.report.ok'));
		} catch {
			toast.error(t('saved.report.error'));
		} finally {
			reportRunning = null;
		}
	}

	/** Example: "11/14/2026, 5:20 PM · Reversible · Codes · 3 files" for a previous saving. */
	function savingMeta(saving: Saving): string {
		const parts = [
			saving.time ? timePoint(new Date(saving.time)) : null,
			methodText(saving.masking, t),
			t('historyList.files', { n: savedNum(saving) })
		];
		return parts.filter((part) => part !== null).join(' · ');
	}

	const backTarget = $derived(resolve('/(app)/entry/[id]/restore', { id: entry.id }));

	function restore(answer: string): void {
		void goto(backTarget, { state: { answer } });
	}

	/** A KI file – selected or dropped onto the window. */
	function restoreFile(file: Selection): void {
		void goto(backTarget, { state: { file } });
	}

	async function selectFile(): Promise<void> {
		try {
			const file = await selectFiles(false, t('restore.file.dialog'));
			if (file) restoreFile(file);
		} catch {
			toast.error(t('restore.file.error.otherwise'));
		}
	}

	// In reversible mode the card also accepts a KI file dropped onto the window.
	$effect(() => {
		if (weg !== 'restore' || !isTauri()) return;
		const abmelden = listenDropzone({
			ziehen: (active) => (zieht = active),
			abgelegt: restoreFile
		});
		return () => void abmelden.then((ab) => ab());
	});
</script>

<div class="mx-auto flex max-w-[960px] flex-col gap-5.5 px-8 pt-10 pb-10">
	<HistoryBackLink class="-mb-2" />
	<Card as="section" radius="xl" gap="lg" aria-labelledby="saved-title">
		<div class="flex flex-wrap items-center justify-between gap-4.5">
			<div class="flex min-w-0 items-center gap-3.5">
				<span
					aria-hidden="true"
					class="inline-flex size-11 shrink-0 items-center justify-center rounded-full bg-ink text-on-ink"
				>
					<Icon name="haken" size={22} strokeWidth={2.4} />
				</span>
				<div class="flex min-w-0 flex-col gap-0.5">
					<h1
						id="saved-title"
						class="font-serif text-display font-normal tracking-[-0.015em] text-ink"
					>
						{t('saved.title', { n: num })}
					</h1>
					<span class="text-label text-muted">
						{#if folder}
							{t('saved.in')}
							<span class="font-mono text-caption text-ink" title={folder}
								>{folderShort(folder)}</span
							>
							·
						{/if}
						{t('saved.originals')}
						{#if last?.time && earlier.length > 0}
							· {t('saved.last', { time: timePoint(new Date(last.time)) })}
						{/if}
					</span>
				</div>
			</div>
			<div class="flex flex-wrap gap-2">
				<Button busy={bearbeitet || view.rereads} onclick={editAgain}>
					{t('saved.again')}
				</Button>
				{#if folder}
					<Button variant="primary" icon="folder" onclick={() => open()}>
						{t('saved.openFolder')}
					</Button>
				{/if}
			</div>
		</div>

		{#if changed}
			<p
				role="note"
				class="flex items-start gap-2 rounded-md border border-amber-line bg-amber-1 px-4 py-3 text-label text-amber-ink"
			>
				<Icon name="info" size={15} class="mt-0.5 shrink-0" />
				<span
					><span class="font-semibold">{t('saved.sinceSaving')}</span> · {t(
						'saved.sinceSaving.text'
					)}</span
				>
			</p>
		{/if}

		<ul aria-label={t('saved.list')} class="flex flex-col border-t border-line-soft">
			{#each lines as line (line.id)}
				<FileRow name={line.name} meta={line.meta}>
					{#snippet right()}
						{#if line.label}
							<MaskLabel
								label={line.label}
								inventedNames={line.inventedNames}
								class="self-center"
							/>
						{/if}
						{#if line.number > 0}
							<Button href={reviewTarget(line.number)} variant="link" size="sm">
								{t('common.edit')}
							</Button>
						{/if}
					{/snippet}
				</FileRow>
			{/each}
		</ul>

		<p class="flex flex-wrap items-center gap-x-1.5 text-meta text-subtle">
			<span>{parts.join(' · ')}</span>
			<span aria-hidden="true">·</span>
			<Button
				variant="link"
				size="sm"
				class="text-meta"
				busy={reportRunning === reportKey(last)}
				disabled={!sidecarGate.mayRead}
				onclick={() => saveReport()}
			>
				{t('saved.report')}
			</Button>
		</p>
	</Card>

	{#if earlier.length > 0}
		<Card as="section" radius="xl" gap="lg" aria-labelledby="saved-earlier">
			<h2 id="saved-earlier" class="text-title font-semibold">
				{t('saved.earlier')}
			</h2>
			<ul class="flex flex-col border-t border-line-soft">
				{#each earlier as saving (saving.id)}
					<li
						class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 border-b border-line-soft py-3 last:border-b-0"
					>
						<div class="flex min-w-0 flex-col gap-0.5">
							<span class="text-ui text-ink">{savingMeta(saving)}</span>
							{#if saving.folder}
								<span class="truncate font-mono text-caption text-muted" title={saving.folder}
									>{folderShort(saving.folder)}</span
								>
							{/if}
						</div>
						<div class="flex flex-wrap gap-2">
							{#if saving.folder}
								<Button variant="link" size="sm" icon="folder" onclick={() => open(saving.folder)}>
									{t('saved.openFolder.short')}
								</Button>
							{/if}
							<Button
								variant="link"
								size="sm"
								busy={reportRunning === reportKey(saving)}
								disabled={!sidecarGate.mayRead}
								onclick={() => saveReport(saving)}
							>
								{t('saved.report.short')}
							</Button>
						</div>
					</li>
				{/each}
			</ul>
		</Card>
	{/if}

	{#if weg === 'restore'}
		<RestoreCard
			locked={!sidecarGate.mayRead}
			fileLocked={!sidecarGate.mayUpload}
			{zieht}
			onrestore={restore}
			onpickfile={selectFile}
		/>
	{:else}
		<Card as="section" radius="xl" gap="lg" class="flex-row gap-3.5" aria-labelledby="final">
			<span
				aria-hidden="true"
				class="inline-flex size-9 shrink-0 items-center justify-center rounded-full bg-chip text-muted"
			>
				<Icon name={weg === 'level3' ? 'lockFull' : 'lock'} size={17} />
			</span>
			<div class="flex flex-col gap-1">
				<h2 id="final" class="text-title font-semibold">
					{t(weg === 'level3' ? 'final.level3.title' : 'final.irreversible.title')}
				</h2>
				<p class="text-pretty text-muted">
					{t(weg === 'level3' ? 'final.level3.text' : 'final.irreversible.text')}
				</p>
			</div>
		</Card>
	{/if}
</div>
