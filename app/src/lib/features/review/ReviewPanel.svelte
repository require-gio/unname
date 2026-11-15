<script lang="ts">
	import { t } from '#lib/state/i18n.svelte.js';
	import { aiView, type Action, type Question } from '#lib/review/state.js';
	import Button from '#lib/ui/Button.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import IconButton from '#lib/ui/IconButton.svelte';
	import Spinner from '#lib/ui/Spinner.svelte';
	import DecidedRow from './DecidedRow.svelte';
	import QuestionCard from './QuestionCard.svelte';

	/**
	 * Right-hand review bar: header, open questions as cards, decided items with an
	 * “Edit” button for AI-resolved findings, and a bottom notice “Mask if in doubt”
	 * with “Save all n files”. When collapsed only a strip with the counter remains.
	 *
	 * AI assessment comes from the sidecar (`assessment`). If the second-opinion model
	 * is unavailable, the AI panel, “Apply all AI suggestions”, and “Resolved by local AI”
	 * are omitted.
	 *
	 * Saved files are placed alongside originals; “Save to another folder …” opens the
	 * system folder picker.
	 */
	let {
		open = $bindable(true),
		ready = true,
		questions,
		files,
		otherOpen,
		suggestions,
		saveLock,
		saves = false,
		again = false,
		onsave,
		onOtherFolder,
		ondecide,
		onundo,
		onsuggestions
	}: {
		open?: boolean;
		/** Findings for the file are loaded; nothing to show before that. */
		ready?: boolean;
		questions: readonly Question[];
		/** Files belonging to the entry, for “Save all n files”. */
		files: number;
		/** Additional files with open questions; `null` when there is only one file. */
		otherOpen: number | null;
		suggestions: number;
		/** Reason saving is currently blocked; otherwise `undefined`. */
		saveLock?: string;
		saves?: boolean;
		/** Entry already saved: show “Save again”. */
		again?: boolean;
		onsave: () => void;
		onOtherFolder: () => void;
		ondecide: (findingId: string, action: Action) => void;
		onundo: (findingId: string) => void;
		onsuggestions: () => void;
	} = $props();

	const byHand = $derived(
		questions.filter((question) => question.decision?.by !== 'ai-suggestion')
	);
	const byAi = $derived(questions.filter((question) => question.decision?.by === 'ai-suggestion'));
	const openCount = $derived(questions.filter((question) => !question.decision).length);

	const subline = $derived.by(() => {
		const sentence = t(openCount > 0 ? 'review.open.text' : 'review.done.text');
		if (otherOpen === null) return sentence;
		const other =
			otherOpen > 0
				? t('review.other.open', { n: otherOpen })
				: t('review.other.done', { n: files - 1 });
		return `${sentence} ${other}`;
	});

	function aiBox(question: Question) {
		const view = aiView(question.finding);
		if (!view) return undefined;
		return {
			safety: view.safety,
			text: t(view.sentence),
			...(view.suggestion
				? {
						suggestion: view.suggestion === 'accept' ? ('mask' as const) : ('keep' as const)
					}
				: {})
		};
	}

	function rationale(question: Question): string | undefined {
		const view = aiView(question.finding);
		if (!view) return undefined;
		return t('ai.rationale', {
			short: t(view.short),
			percent: Math.round(view.safety * 100)
		});
	}

	/** Short snippet around the position, trimmed to word boundaries. */
	function shorten(text: string, page: 'before' | 'after'): string {
		const MAX = 42;
		const singleLine = text.replace(/\s+/g, ' ');
		if (singleLine.length <= MAX) return singleLine;
		if (page === 'before') {
			const rest = singleLine.slice(-MAX);
			return rest.slice(rest.indexOf(' ') + 1);
		}
		const rest = singleLine.slice(0, MAX);
		return rest.slice(0, rest.lastIndexOf(' '));
	}
</script>

{#if open}
	<aside
		aria-label={t('review.reviewBar')}
		class="flex max-w-[390px] min-w-0 flex-[1_1_360px] flex-col border-l border-line bg-surface-2"
	>
		<div class="flex items-center justify-between pt-2.5 pr-2.5 pl-4.5">
			<span class="text-fine font-semibold tracking-[0.04em] text-muted uppercase"
				>{t('review.reviewBar')}</span
			>
			<IconButton
				label={t('review.reviewBar.collapse')}
				icon="reviewBar"
				size="sm"
				onclick={() => (open = false)}
			/>
		</div>
		<div class="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-4.5 pt-1.5 pb-5">
			{#if !ready}
				<Spinner size={18} label={t('common.loads')} class="mt-2 text-subtle" />
			{:else}
				<div class="flex flex-col gap-2">
					<h2 class="font-serif text-heading font-normal tracking-[-0.01em]">
						{t('review.open.title', { n: openCount })}
					</h2>
					<p class="text-ui text-muted">{subline}</p>
					{#if suggestions > 0}
						<Button
							variant="tint"
							size="sm"
							icon="ai"
							class="mt-1 self-start"
							onclick={onsuggestions}
						>
							{t('review.acceptAi')}
						</Button>
					{/if}
				</div>

				{#if byHand.length > 0}
					<ul class="flex flex-col gap-2.5">
						{#each byHand as question (question.finding.id)}
							<li data-question={question.finding.id}>
								{#if question.decision}
									<DecidedRow
										value={question.finding.text}
										result={question.decision.action === 'accept' ? 'masked' : 'keep'}
										onaction={() => onundo(question.finding.id)}
									/>
								{:else}
									<QuestionCard
										number={question.number}
										kind={question.kind}
										type={question.finding.type}
										category={question.finding.category}
										value={question.finding.text}
										context={{
											before: shorten(question.finding.context.before, 'before'),
											after: shorten(question.finding.context.after, 'after')
										}}
										ai={aiBox(question)}
										onmask={() => ondecide(question.finding.id, 'accept')}
										onkeep={() => ondecide(question.finding.id, 'reject')}
									/>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}

				{#if byAi.length > 0}
					<section aria-labelledby="ai-resolved" class="flex flex-col gap-2">
						<h3
							id="ai-resolved"
							class="text-caption font-semibold tracking-[0.03em] text-muted uppercase"
						>
							{t('review.aiResolved')}
						</h3>
						{#each byAi as question (question.finding.id)}
							<DecidedRow
								byAi
								value={question.finding.text}
								rationale={rationale(question)}
								result={question.decision?.action === 'accept' ? 'masked' : 'keep'}
								action={t(
									question.decision?.action === 'accept'
										? 'question.anywayKeep'
										: 'question.maskAnyway'
								)}
								onaction={() =>
									ondecide(
										question.finding.id,
										question.decision?.action === 'accept' ? 'reject' : 'accept'
									)}
							/>
						{/each}
					</section>
				{/if}
			{/if}
		</div>
		<div class="flex flex-col gap-2.5 border-t border-line bg-surface px-4.5 pt-3.5 pb-4.5">
			{#if !ready}
				<!-- No messages yet. -->
			{:else if openCount > 0}
				<p class="flex items-start gap-2 text-meta text-amber-ink">
					<span aria-hidden="true" class="mt-1.25 size-2 shrink-0 rounded-full bg-amber-strong"
					></span>
					{t('review.notice.open', { n: openCount })}
				</p>
			{:else}
				<p class="flex items-center gap-2 text-meta text-muted">
					<Icon name="haken" size={15} strokeWidth={2.2} />{t('review.notice.done')}
				</p>
			{/if}
			<Button
				variant="primary"
				size="lg"
				icon="save"
				disabled={saveLock !== undefined}
				busy={saves}
				title={saveLock}
				onclick={onsave}
			>
				{again ? t('review.saveAgain') : t('review.save', { n: files })}
			</Button>
			{#if saveLock}
				<p class="text-caption text-subtle">{saveLock}</p>
			{:else}
				<Button
					variant="link"
					size="sm"
					class="self-center"
					disabled={saves}
					onclick={onOtherFolder}
				>
					{t('save.other')}
				</Button>
			{/if}
		</div>
	</aside>
{:else}
	<aside
		aria-label={t('review.reviewBar')}
		class="flex w-14 shrink-0 flex-col items-center gap-3 border-l border-line bg-surface-2 py-3"
	>
		<IconButton
			label={t('review.reviewBar.expand')}
			icon="reviewBar"
			onclick={() => (open = true)}
		/>
		{#if ready && openCount > 0}
			<button
				type="button"
				aria-label={t('review.questionsOpen', { n: openCount })}
				title={t('review.questionsOpen', { n: openCount })}
				onclick={() => (open = true)}
				class="inline-flex size-7.5 cursor-pointer items-center justify-center rounded-full bg-amber-strong text-label font-bold text-on-amber-strong"
				>{openCount}</button
			>
		{/if}
		<span
			aria-hidden="true"
			class="rotate-180 text-fine font-semibold tracking-[0.06em] text-muted uppercase [writing-mode:vertical-rl]"
			>{t('review.reviewBar')}</span
		>
	</aside>
{/if}
