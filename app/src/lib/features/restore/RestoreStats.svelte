<script lang="ts">
	import { t } from '#lib/state/i18n.svelte.js';
	import Icon from '#lib/ui/Icon.svelte';
	import type { Count } from '#lib/restore/restore.js';

	/**
	 * Shows replacement stats: e.g. "✓ 6 codes replaced · 5 from here · 1 from “rental agreement (scan)”".
	 * Unknown codes are listed with their source (Board “5”). Color markers match the
	 * underlines in the document: indigo for “here”, green for another entry.
	 * The text conveys the same info; color alone is not meaningful.
	 */
	let { count }: { count: Count } = $props();
</script>

<div
	role="status"
	aria-label={t('restore.count')}
	class="flex flex-wrap items-center gap-2.5 text-body"
>
	{#if count.noCode}
		<span class="rounded-md border border-line bg-surface px-3 py-2 text-muted">
			{t('restore.noCode')}
		</span>
	{:else}
		{#if count.replaced > 0}
			<span
				class="inline-flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-md border border-line bg-surface px-3 py-2"
			>
				<Icon name="haken" size={15} strokeWidth={2.2} />
				<span
					><strong class="font-semibold">{t('restore.codes', { n: count.replaced })}</strong>
					{t('restore.replaced')}</span
				>
				{#each count.origin as part (part.text)}
					<span class="inline-flex items-center gap-1.25 text-muted">
						<span
							aria-hidden="true"
							class={[
								'inline-block h-2 w-3 rounded-[2px]',
								part.kind === 'here' ? 'bg-tint-5' : 'bg-green-2'
							]}
						></span>{part.text}
					</span>
				{/each}
				{#if count.changed > 0}
					<span class="text-muted">{t('restore.changed', { n: count.changed })}</span>
				{/if}
			</span>
		{/if}
		{#if count.unknown > 0}
			<span
				class="inline-flex items-center gap-2 rounded-md border border-amber-line bg-amber-1 px-3 py-2 text-amber-ink"
			>
				<span aria-hidden="true" class="size-2 shrink-0 rounded-full bg-amber-strong"></span>
				<span
					><strong class="font-semibold">{t('restore.unknown', { n: count.unknown })}</strong>
					– {t('restore.unknown.reason')}</span
				>
			</span>
		{/if}
	{/if}
</div>
