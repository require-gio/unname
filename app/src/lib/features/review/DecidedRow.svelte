<script lang="ts">
	import { t } from '#lib/state/i18n.svelte.js';
	import Button from '#lib/ui/Button.svelte';
	import Icon from '#lib/ui/Icon.svelte';
	import { cn } from '#lib/utils.js';

	/**
	 * A decided entry in the question bar (board “components”):
	 * “Pattern · stays – Change”. When the local AI decides, the value gets a dotted line
	 * and a rationale.
	 */
	let {
		value,
		result,
		rationale,
		byAi = false,
		action,
		onaction
	}: {
		value: string;
		result: 'masked' | 'keep';
		/** “Wood type, no name · 97 %” – only for AI decisions. */
		rationale?: string;
		byAi?: boolean;
		/** Button label; default “Change”. */
		action?: string;
		onaction: () => void;
	} = $props();
</script>

<div class="flex items-center gap-2.5 rounded-md border border-line bg-surface px-3.5 py-2.75">
	{#if !byAi}<Icon name="haken" size={16} strokeWidth={2.2} class="text-ink" />{/if}
	<span class="flex min-w-0 flex-1 flex-col gap-px">
		<span>
			<span class={cn('font-semibold', byAi && 'border-b-[1.5px] border-dotted border-ai-line')}
				>{value}</span
			><span class="text-muted"
				>{` · ${t(result === 'masked' ? 'question.isMasked' : 'question.staysAsIs')}`}</span
			>
		</span>
		{#if rationale}<span class="text-caption text-subtle">{rationale}</span>{/if}
	</span>
	<Button variant="link" size="sm" class="py-1.5 text-meta" onclick={onaction}>
		{action ?? t('common.change')}
	</Button>
</div>
