<script lang="ts">
	import { resolve } from '$app/paths';
	import { toast } from 'svelte-sonner';
	import { cn } from '#lib/utils.js';
	import Button from '#lib/ui/Button.svelte';
	import SegmentText from '#lib/ui/SegmentText.svelte';
	import Textarea from '#lib/ui/Textarea.svelte';
	import { commitText, maskText } from '#lib/sidecar/api-v2.js';
	import type { TextMaskResponse } from '#lib/sidecar/api.gen.js';
	import { SidecarError } from '#lib/sidecar/client.js';
	import { readClipboard, writeClipboard } from '#lib/sidecar/inputs.js';
	import { GATE_TEXT, gateNotice } from '#lib/sidecar/gate-notice.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { start } from '#lib/state/start.svelte.js';
	import { history } from '#lib/state/history.svelte.js';
	import { outputSegments, outputTitle, outputCount } from '#lib/text-mode/output.js';

	/**
	 * Text mode (Board “1 · New”): paste text, see live masking,
	 * copy. Uses same codes as documents.
	 *
	 * “Live” means: `POST /text/mask` is debounced on every text or
	 * masking change; the previous request is cancelled. Nothing is
	 * persisted. Only “Copy” writes the assignments (`/text/commit`)
	 * and creates a history entry “Text · copied”. The clipboard receives
	 * the text from this response – it also appears in the window:
	 * what you see is what was copied. Afterwards “Restore answer”
	 * creates a new entry.
	 */
	const ENTPRELLEN_MS = 300;
	const fieldId = $props.id();

	/** The most recent answer and its scope. */
	let answer = $state<{ text: string; job: string; data: TextMaskResponse } | null>(null);
	let running = $state(false);
	let error = $state<string | null>(null);
	let copiedCurrent = $state(false);

	const job = $derived(start.masking.asRequest());
	const jobKey = $derived(JSON.stringify(job));
	const empty = $derived(start.text.trim() === '');
	/** The output matches the current text and masking. */
	const current = $derived(answer !== null && answer.text === start.text && answer.job === jobKey);
	const notice = $derived(
		gateNotice({ snapshot: sidecarGate.snapshot, outside: sidecarGate.outside })
	);
	const segments = $derived(answer ? outputSegments(answer.data.result, answer.text) : []);
	const title = $derived(outputTitle(start.masking.method, answer?.data.result));
	const backTarget = $derived(
		start.copied ? resolve('/(app)/entry/[id]/restore', { id: start.copied }) : null
	);

	$effect(() => {
		const text = start.text;
		const masking = job;
		const key = jobKey;
		const connection = sidecarGate.connection;
		if (text.trim() === '') {
			answer = null;
			error = null;
			running = false;
			return;
		}
		if (!connection || !sidecarGate.mayUpload) return;

		const control = new AbortController();
		const timer = setTimeout(async () => {
			running = true;
			try {
				const data = await maskText(
					connection,
					{ text, masking, review: { unresolved: 'mask' } },
					control.signal
				);
				answer = { text, job: key, data };
				error = null;
			} catch (cause) {
				if (control.signal.aborted) return;
				error =
					cause instanceof SidecarError && cause.status === 413
						? t('text.tooLarge')
						: t('text.error');
			} finally {
				if (!control.signal.aborted) running = false;
			}
		}, ENTPRELLEN_MS);
		return () => {
			clearTimeout(timer);
			control.abort();
		};
	});

	async function insert(): Promise<void> {
		try {
			const text = await readClipboard();
			if (!text) {
				toast(t('text.clipboard.empty'));
				return;
			}
			start.text = start.text.trim() === '' ? text : `${start.text.trimEnd()}\n\n${text}`;
		} catch {
			toast(t('text.clipboard.empty'));
		}
	}

	async function copy(): Promise<void> {
		const connection = sidecarGate.connection;
		if (!connection || !sidecarGate.mayExport || !current) return;
		const text = start.text;
		const masking = job;
		copiedCurrent = true;
		try {
			const result = await commitText(connection, {
				text,
				masking,
				review: { unresolved: 'mask' }
			});
			if (answer) answer = { ...answer, data: { ...answer.data, result: result.result } };
			start.copied = result.entry.id;
			void history.update();
			try {
				await writeClipboard(result.result.text, 'masked');
				toast(t('text.copied'));
			} catch {
				toast(t('text.clipboard.error'));
			}
		} catch {
			toast(t('text.copy.error'));
		} finally {
			copiedCurrent = false;
		}
	}
</script>

<div class="flex flex-col gap-2.5">
	<section
		aria-label={t('start.input.text')}
		class="grid grid-cols-[repeat(auto-fit,minmax(min(380px,100%),1fr))] gap-4"
	>
		<div class="flex flex-col overflow-hidden rounded-xl border border-line bg-surface">
			<div
				class="flex min-h-15 items-center justify-between gap-2.5 border-b border-line-soft px-4 py-3"
			>
				<label for={fieldId} class="font-semibold">{t('text.input')}</label>
				<Button variant="secondary" size="sm" icon="insert" onclick={insert}>
					{t('text.insert')}
				</Button>
			</div>
			<Textarea
				id={fieldId}
				bind:value={start.text}
				font="document"
				eingebettet
				spellcheck={false}
				placeholder={t('text.input.placeholder')}
				class="min-h-62.5 flex-1"
			/>
		</div>

		<div
			class="flex flex-col overflow-hidden rounded-xl border-[1.5px] border-accent bg-surface halo-accent"
		>
			<div
				class="flex min-h-15 items-center justify-between gap-2.5 border-b border-line-soft px-4 py-3"
			>
				<h2 class="text-body font-semibold">{t(title)}</h2>
				<span class="inline-flex items-center gap-1.5 text-caption font-semibold text-link">
					<span
						aria-hidden="true"
						class={cn('size-1.75 rounded-full bg-accent', running && 'motion-safe:animate-pulse')}
					></span>
					{running ? t('text.running') : t('text.live')}
				</span>
			</div>
			<div
				class="h-49 overflow-auto px-5 py-4.5 font-doc text-doc leading-[1.6] text-doc-ink"
				aria-live="polite"
				aria-busy={running}
			>
				{#if empty}
					<p class="font-sans text-body text-subtle">{t('text.empty')}</p>
				{:else if error}
					<p role="alert" class="font-sans text-body text-red-ink">{error}</p>
				{:else if answer}
					<SegmentText {segments} peek={false} class={cn(!current && 'opacity-60')} />
				{:else if notice}
					<p class="font-sans text-body text-amber-ink-2">{t(GATE_TEXT[notice.position].lock)}</p>
				{/if}
			</div>
			<div
				class="flex flex-wrap items-center justify-between gap-2.5 border-t border-line-soft px-4 py-2.5"
			>
				<span class="text-label text-muted">
					{answer && !empty ? outputCount(answer.data.result, t) : ''}
				</span>
				<div class="flex gap-2">
					{#if start.masking.method === 'reversible' && backTarget}
						<Button href={backTarget} variant="secondary" size="sm">{t('text.restore')}</Button>
					{/if}
					<Button
						variant="primary"
						size="sm"
						icon="copy"
						busy={copiedCurrent}
						disabled={empty || !current || running || !sidecarGate.mayExport}
						onclick={copy}
					>
						{t('common.copy')}
					</Button>
				</div>
			</div>
		</div>
	</section>
	<p class="text-label text-subtle">{t('text.notice')}</p>
</div>
