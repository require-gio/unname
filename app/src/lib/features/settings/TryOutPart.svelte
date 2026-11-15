<script lang="ts">
	import SegmentText from '#lib/ui/SegmentText.svelte';
	import TextInput from '#lib/ui/TextInput.svelte';
	import { testSegments } from '#lib/settings/dialog.js';
	import { testPatterns } from '#lib/sidecar/api-v2.js';
	import type { PatternIn, PatternTestResponse } from '#lib/sidecar/api.gen.js';
	import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { names } from '#lib/state/names.svelte.js';

	/**
	 * “Try out”: a sentence that would be masked like lists, terms and patterns.
	 * Tested in the sidecar using the same regex engine that processes documents.
	 * No data is persisted.
	 */
	let { draft = null }: { draft?: PatternIn | null } = $props();

	const fieldId = $props.id();
	let text = $state('Schaden SCH-2026-00417 zur Polizze POL-4471920, besprochen im Projekt Falke.');
	let answer = $state<{ text: string; result: PatternTestResponse } | null>(null);
	let invalid = $state(false);

	$effect(() => {
		const connection = sidecarGate.connection;
		const sentence = text;
		// Re-run the check after each change, based on the saved state.
		const pattern: PatternIn[] | undefined = draft
			? [...names.pattern.map((item) => ({ ...item })), draft]
			: undefined;
		void names.terms.length;
		void names.pattern;
		void names.listen;
		if (!connection || !sentence.trim()) {
			answer = null;
			return;
		}
		const abort = new AbortController();
		const timer = setTimeout(() => {
			testPatterns(connection, sentence, pattern, abort.signal)
				.then((result) => {
					answer = { text: sentence, result };
					invalid = false;
				})
				.catch(() => {
					if (!abort.signal.aborted) invalid = draft !== null;
				});
		}, 250);
		return () => {
			clearTimeout(timer);
			abort.abort();
		};
	});

	const segments = $derived(answer ? testSegments(answer.text, answer.result.hits) : []);
	const count = $derived(
		!answer
			? ''
			: answer.result.count === 0
				? t('settings.try.none')
				: t('settings.try.match', { n: answer.result.count })
	);
</script>

<section class="flex flex-col gap-2.5 rounded-lg border border-line bg-surface-3 px-5 py-4">
	<label for={fieldId} class="text-fine font-semibold tracking-[0.04em] text-muted uppercase"
		>{t('settings.try')}</label
	>
	<TextInput id={fieldId} bind:value={text} maxlength={4000} />
	{#if segments.length > 0}
		<p class="font-doc text-doc text-doc-ink">
			<SegmentText {segments} mark={false} />
		</p>
	{/if}
	<span role="status" class="text-label text-muted">
		{invalid ? t('settings.try.draftInvalid') : count}
	</span>
</section>
