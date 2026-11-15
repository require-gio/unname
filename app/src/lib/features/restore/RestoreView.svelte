<script lang="ts">
	import HistoryBackLink from '#lib/features/history/HistoryBackLink.svelte';
	import { isTauri } from '@tauri-apps/api/core';
	import { onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { resolve } from '$app/paths';
	import { discardRestoredFile, restoreText } from '#lib/sidecar/api-v2.js';
	import type { Entry, RestoreTextResponse } from '#lib/sidecar/api.gen.js';
	import { SidecarError } from '#lib/sidecar/client.js';
	import {
		listenDropzone,
		selectFiles,
		writeClipboard,
		type Selection
	} from '#lib/sidecar/inputs.js';
	import { GATE_TEXT, gateNotice } from '#lib/sidecar/gate-notice.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import {
		restoreFile,
		openSavedFile,
		saveRestoredFile,
		type FileRestoration
	} from '#lib/sidecar/restore.js';
	import { saveTextFile } from '#lib/sidecar/save.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { history } from '#lib/state/history.svelte.js';
	import Button from '#lib/ui/Button.svelte';
	import ConfirmDialog from '#lib/ui/ConfirmDialog.svelte';
	import {
		outputSegments,
		fileError as fileErrorText,
		errorCode,
		withAliases,
		count
	} from '#lib/restore/restore.js';
	import Breadcrumb from './Breadcrumb.svelte';
	import RestoreInput, { type Eingabeart } from './RestoreInput.svelte';
	import RestoreOutput from './RestoreOutput.svelte';
	import RestoreStats from './RestoreStats.svelte';

	/**
	 * Board “5 · AI answer restored”.
	 *
	 * Text is restored live (`POST /restore/text`, debounced, previous request
	 * cancelled) – only in memory, nothing is saved. Only clicking “Copy” or
	 * “Save as file” creates a restoration entry; the copied or saved text is
	 * exactly what is shown in the window.
	 *
	 * An AI-generated file arrives via dialog or dropzone onto the shell.
	 * The shell writes the new file with real names to the location chosen in
	 * the save dialog; the original AI file remains unchanged.
	 */
	let {
		entry,
		answer,
		selection
	}: {
		entry: Entry;
		/** From “Restore AI answer” in the Saved view. */
		answer?: string;
		/** A file stored there or selected from the AI. */
		selection?: Selection;
	} = $props();

	const ENTPRELLEN_MS = 250;

	// Carried values apply only when the view is opened.
	// svelte-ignore state_referenced_locally
	let mode = $state<Eingabeart>(selection ? 'file' : 'text');
	// svelte-ignore state_referenced_locally
	let input = $state(answer ?? '');
	// svelte-ignore state_referenced_locally
	let mitgebracht = $state<Selection | null>(selection ?? null);
	let mark = $state(true);
	let result = $state<{ text: string; data: RestoreTextResponse } | null>(null);
	let running = $state(false);
	let error = $state<string | null>(null);
	let file = $state<FileRestoration | null>(null);
	let fileRunning = $state(false);
	let fileError = $state<string | null>(null);
	let saved = $state<string | null>(null);
	let saves = $state(false);
	/** The selected target is synced; the shell waits for "Save anyway". */
	let cloudQuestion = $state(false);
	let copied = $state(false);
	let zieht = $state(false);

	const aliases = $derived(withAliases(entry));
	const overview = $derived(resolve('/(app)/entry/[id]', { id: entry.id }));
	const current = $derived(result !== null && result.text === input);
	const notice = $derived(
		gateNotice({ snapshot: sidecarGate.snapshot, outside: sidecarGate.outside })
	);
	const segments = $derived(
		mode === 'text'
			? result && input.trim() !== ''
				? outputSegments(result.data.text, result.data.spans)
				: []
			: file
				? outputSegments(file.preview.text, file.preview.spans)
				: []
	);
	const status = $derived(
		mode === 'text'
			? result && input.trim() !== ''
				? count(result.data.counts, result.data.origins, t)
				: null
			: file
				? count(file.counts, file.origins, t)
				: null
	);

	$effect(() => {
		const text = input;
		const connection = sidecarGate.connection;
		const entry_id = entry.id;
		const surrogates = aliases;
		if (text.trim() === '') {
			result = null;
			error = null;
			running = false;
			return;
		}
		if (!connection) return;
		const control = new AbortController();
		const timer = setTimeout(async () => {
			running = true;
			try {
				const data = await restoreText(connection, { text, entry_id, surrogates }, control.signal);
				result = { text, data };
				error = null;
			} catch (cause) {
				if (control.signal.aborted) return;
				error =
					cause instanceof SidecarError && cause.status === 413
						? t('restore.tooLarge')
						: t('restore.error');
			} finally {
				if (!control.signal.aborted) running = false;
			}
		}, ENTPRELLEN_MS);
		return () => {
			clearTimeout(timer);
			control.abort();
		};
	});

	// A file from the saved view: translate once the shell accepts files.
	$effect(() => {
		if (!mitgebracht || !sidecarGate.mayUpload) return;
		const existing = mitgebracht;
		mitgebracht = null;
		void acceptFile(existing);
	});

	$effect(() => {
		if (!isTauri()) return;
		const abmelden = listenDropzone({
			ziehen: (active) => (zieht = active),
			abgelegt: (fresh) => void acceptFile(fresh)
		});
		return () => void abmelden.then((ab) => ab());
	});

	// The new file stays in the sidecar storage until it is retrieved —
	// leaving the view means it is no longer needed.
	onDestroy(() => void discard());

	/** Increment the entry's restore count – only after the answer is used. */
	function tally(text: string): void {
		const connection = sidecarGate.connection;
		if (!connection) return;
		void restoreText(connection, {
			text,
			entry_id: entry.id,
			surrogates: aliases,
			record: true
		})
			.then(() => history.update())
			.catch(() => {
				// The answer is already copied or saved; no counter means nothing is lost.
			});
	}

	async function copy(): Promise<void> {
		if (!result || !current) return;
		const { text, data } = result;
		copied = true;
		try {
			const copy = await writeClipboard(data.text, 'restored');
			toast(
				copy.local_only && copy.clears_after_s
					? t('restore.copied.local', { minuten: Math.round(copy.clears_after_s / 60) })
					: t('restore.copied')
			);
			tally(text);
		} catch {
			toast.error(t('text.clipboard.error'));
		} finally {
			copied = false;
		}
	}

	async function saveAsFile(): Promise<void> {
		if (!result || !current) return;
		const { text, data } = result;
		saves = true;
		try {
			const path = await saveTextFile(
				t('restore.asFile.title'),
				t('restore.asFile.name'),
				data.text
			);
			if (path) {
				toast(t('restore.saved'));
				tally(text);
			}
		} catch {
			toast.error(t('restore.saveError'));
		} finally {
			saves = false;
		}
	}

	async function selectFile(): Promise<void> {
		if (fileRunning || !sidecarGate.mayUpload) return;
		try {
			const fresh = await selectFiles(false, t('restore.file.dialog'));
			if (fresh) await acceptFile(fresh);
		} catch {
			fileError = t('restore.file.error.otherwise');
		}
	}

	async function acceptFile(fresh: Selection): Promise<void> {
		mode = 'file';
		if (fileRunning || !sidecarGate.mayUpload) return;
		fileError = null;
		if (fresh.names.length === 0) {
			fileError = t('dropzone.noFiles');
			return;
		}
		await discard();
		fileRunning = true;
		try {
			file = await restoreFile(fresh.id, entry.id, aliases);
			if (file.skipped > 0) toast(t('restore.file.several'));
		} catch (cause) {
			fileError = fileErrorText(errorCode(cause), t);
		} finally {
			fileRunning = false;
		}
	}

	async function discard(): Promise<void> {
		const alt = file;
		file = null;
		saved = null;
		const connection = sidecarGate.connection;
		if (!alt?.output || !connection) return;
		try {
			await discardRestoredFile(connection, alt.output.id);
		} catch {
			// Unretrieved files expire in the sidecar after 30 minutes.
		}
	}

	async function saveUnder(cloudConfirmed = false): Promise<string | null> {
		if (!file?.output || saves) return null;
		saves = true;
		try {
			const path = await saveRestoredFile(
				file.output.id,
				t('restore.file.saveDialog'),
				cloudConfirmed
			);
			if (path) {
				saved = path;
				toast(t('restore.file.saved'));
				void history.update();
			}
			return path;
		} catch (cause) {
			const code = errorCode(cause);
			if (code === 'cloud_target') cloudQuestion = true;
			else toast.error(fileErrorText(code, t));
			return null;
		} finally {
			saves = false;
		}
	}

	/** “Open” requires a file at a location chosen by the user. */
	async function open(): Promise<void> {
		const path = saved ?? (await saveUnder());
		if (!path) return;
		try {
			await openSavedFile(path);
		} catch {
			toast.error(t('restore.file.openError'));
		}
	}

	function clear(): void {
		if (mode === 'text') input = '';
		else {
			fileError = null;
			void discard();
		}
	}
</script>

<div class="mx-auto flex max-w-[1120px] flex-col gap-4.5 px-8 pt-7 pb-9">
	<HistoryBackLink />
	<Breadcrumb back={overview} entry={entry.title} current={t('restore.title')} />

	<header class="flex flex-col gap-1">
		<h1 class="font-serif text-display font-normal tracking-[-0.01em] text-ink">
			{t('restore.title')}
		</h1>
		<p class="text-pretty text-muted">{t('restore.text')}</p>
	</header>

	<div class="grid grid-cols-[repeat(auto-fit,minmax(min(440px,100%),1fr))] gap-4">
		<RestoreInput
			bind:mode
			bind:text={input}
			{file}
			{fileRunning}
			{fileError}
			{zieht}
			locked={notice ? t(GATE_TEXT[notice.position].lock) : undefined}
			onclear={clear}
			onpickfile={selectFile}
			onremove={() => void discard()}
		/>
		<RestoreOutput
			{mode}
			bind:mark
			{segments}
			{running}
			{error}
			stale={mode === 'text' && !current}
			{file}
			{saved}
			{saves}
			onopen={open}
			onsave={() => void saveUnder()}
		/>
	</div>

	<ConfirmDialog
		bind:open={cloudQuestion}
		title={t('restore.file.cloud.title')}
		description={t('restore.file.cloud.text')}
		sequences={[t('restore.file.cloud.sequence.sync'), t('restore.file.cloud.sequence.devices')]}
		confirm={t('restore.file.cloud.go')}
		danger
		onconfirm={() => void saveUnder(true)}
	/>

	<div class="flex flex-wrap items-center justify-between gap-4">
		{#if status}<RestoreStats count={status} />{:else}<span></span>{/if}
		{#if mode === 'text'}
			<div class="ml-auto flex flex-wrap gap-2.5">
				<Button size="lg" busy={saves} disabled={!current || running} onclick={saveAsFile}>
					{t('restore.asFile')}
				</Button>
				<Button
					variant="primary"
					size="lg"
					icon="copy"
					busy={copied}
					disabled={!current || running}
					onclick={copy}
				>
					{t('common.copy')}
				</Button>
			</div>
		{/if}
	</div>
</div>
