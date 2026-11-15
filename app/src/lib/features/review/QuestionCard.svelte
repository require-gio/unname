<script lang="ts">
	import { questionTitle } from '#lib/review/question-title.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import Button from '#lib/ui/Button.svelte';
	import Card from '#lib/ui/Card.svelte';
	import Meter from '#lib/ui/Meter.svelte';
	import { cn } from '#lib/utils.js';

	/**
	 * An open question detected with context and, from spec §K onward, a local-AI assessment.
	 *
	 * If the AI makes a suggestion, this button is highlighted; if it is uncertain,
	 * both buttons appear the same. Without an assessment (second opinion disabled,
	 * model missing) the AI panel is omitted.
	 *
	 * The header describes what the location represents – e.g. “Detected as phone
	 * number”, not a generic “Detected as name”. Numbers, dates and places can also be uncertain.
	 */
	export interface AiAssessment {
		/** 0…1 */
		safety: number;
		text: string;
		suggestion?: 'mask' | 'keep';
	}

	let {
		number,
		kind,
		type,
		category,
		value,
		context,
		ai,
		onmask,
		onkeep
	}: {
		/** Header is omitted when no number and kind are provided. */
		number?: number;
		kind?: 'suspicion' | 'uncertain';
		/** Entity type of the finding (`PHONE_NUMBER`, …); defaults to name if omitted. */
		type?: string;
		/** Category from the sidecar response (`contact`, `dates`, …). */
		category?: string | null;
		value: string;
		context?: { before: string; after: string };
		ai?: AiAssessment;
		onmask: () => void;
		onkeep: () => void;
	} = $props();

	const title = $derived(kind ? questionTitle(t, kind, type, category) : '');
	const safe = $derived(ai?.suggestion !== undefined);
	const id = $props.id();
</script>

<Card as="article" tone="amber" gap="none" aria-labelledby={id} class="gap-2.75 p-4">
	{#if kind}
		<div class="flex items-center gap-2">
			{#if number !== undefined}
				<span
					aria-hidden="true"
					class="inline-flex size-5 items-center justify-center rounded-full bg-amber-strong text-fine font-bold text-on-amber-strong"
					>{number}</span
				>
			{/if}
			<span class="text-meta font-semibold text-amber-ink-2">
				{#if number !== undefined}<span class="sr-only"
						>{t('question.number', { n: number })}:
					</span>{/if}{title}
			</span>
		</div>
	{/if}
	<div {id} class="font-doc text-heading leading-[1.15]">{value}</div>
	{#if context}
		<p class="text-label leading-normal text-muted">
			…{context.before}<mark class="rounded-xs bg-amber-2 px-0.5 text-amber-ink">{value}</mark
			>{context.after}…
		</p>
	{/if}
	{#if ai}
		<div class={cn('rounded-md px-3 py-2.5', safe ? 'bg-tint-1' : 'bg-surface-3')}>
			<Meter
				value={ai.safety}
				label={t('question.localAi')}
				valueText={t('question.safety', { percent: Math.round(ai.safety * 100) })}
				icon="ai"
				{safe}
			>
				<span class={cn('text-ui', safe ? 'text-tint-ink-strong' : 'text-ink-2')}>{ai.text}</span>
			</Meter>
		</div>
	{/if}
	<div class="flex gap-2">
		{#each ['mask', 'keep'] as const as action (action)}
			{@const vorgeschlagen = ai?.suggestion === action}
			<Button
				variant={vorgeschlagen ? 'primary' : 'secondary'}
				class="h-10.5 flex-1 font-semibold"
				onclick={action === 'mask' ? onmask : onkeep}
			>
				{t(action === 'mask' ? 'question.mask' : 'question.keep')}
				{#if vorgeschlagen}
					<span class="rounded-full bg-on-ink/20 px-1.75 py-0.5 text-fine font-semibold"
						>{t('question.suggestion')}</span
					>
				{/if}
			</Button>
		{/each}
	</div>
</Card>
