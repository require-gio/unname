<script lang="ts">
	import FileBadge from '#lib/ui/FileBadge.svelte';
	import { t } from '#lib/state/i18n.svelte.js';
	import type { Method } from '#lib/masking/rules.js';

	/**
	 * Example: “Invoice.pdf → Invoice_protected.pdf”. Shows how
	 * “Sabine Hofer” is masked: reversible uses codes, irreversible uses
	 * placeholders, and “mask everything” also hides location and date.
	 * This is a pure graphic; the explanatory text is shown beside it.
	 *
	 * Font size is smaller than the scale because the pages are thumbnails.
	 */
	let { method, all }: { method: Method; all: boolean } = $props();

	const reversible = $derived(method === 'reversible');
	const sheet =
		'absolute flex flex-col gap-2.25 rounded-md border bg-surface px-3.5 pt-3 pb-4 text-caption text-muted';
	const header = 'flex items-center gap-1.5 border-b border-line-soft pb-2 text-fine font-semibold';
	const balken = 'block h-1.5 rounded-xs bg-skeleton';
	const marked = 'rounded-xs bg-tint-4 px-1 text-tint-ink-strong';
	const replacement = $derived(
		reversible
			? 'rounded-xs bg-tint-3 px-1.25 py-px font-mono text-[10.5px] whitespace-nowrap text-tint-ink'
			: 'rounded-xs bg-tint-3 px-1.5 text-fine font-semibold whitespace-nowrap text-tint-ink'
	);
</script>

<div aria-hidden="true" class="relative h-55.5 w-100 max-w-full shrink-0">
	<div class="{sheet} top-2.5 left-1 w-59 -rotate-4 border-line shadow-float">
		<span class={header}>
			<FileBadge name="x.pdf" size="small" />{t('start.example.original')}
		</span>
		<span class="{balken} w-[78%]"></span>
		<span class="flex items-center gap-1.5">
			{t('start.example.salutation')}<span class="{marked} font-doc text-body"
				>{t('start.example.name')}</span
			>
		</span>
		<span class="flex items-center gap-1.5">
			{t('start.example.iban')}<span
				class="{marked} py-px font-mono text-[10.5px] whitespace-nowrap">AT61 1904 3002 …</span
			>
		</span>
		<span class="flex items-center">
			<span class={all ? marked : ''}>{t('start.example.place')}</span><span class="mr-1">,</span
			><span class={all ? marked : ''}>{t('start.example.date')}</span>
		</span>
		<span class="{balken} w-[64%]"></span>
	</div>
	<div class="{sheet} top-15.5 left-37.5 w-61.5 rotate-[2.5deg] border-line-strong shadow-page">
		<span class={header}>
			<FileBadge name="x.pdf" size="small" />{t('start.example.protected')}
		</span>
		<span class="{balken} w-[72%]"></span>
		<span class="flex items-center gap-1.5">
			{t('start.example.salutation')}<span class={replacement}
				>{reversible ? '<<PERSON_ba903855>>' : t('start.example.placeholder.name')}</span
			>
		</span>
		<span class="flex items-center gap-1.5">
			{t('start.example.iban')}<span class={replacement}
				>{reversible ? '<<IBAN_CODE_e8b14c52>>' : t('start.example.placeholder.iban')}</span
			>
		</span>
		{#if all}
			<span class="flex items-center">
				<span class={replacement}>{t('start.example.placeholder.place')}</span><span class="mr-1"
					>,</span
				><span class={replacement}>{t('start.example.placeholder.date')}</span>
			</span>
		{:else}
			<span>{t('start.example.place')}, {t('start.example.date')}</span>
		{/if}
		<span class="{balken} w-[58%]"></span>
	</div>
</div>
