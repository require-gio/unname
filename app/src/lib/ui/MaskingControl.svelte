<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';
	import type { CategoryId } from '#lib/masking/catalog.js';
	import { STILE, type Method, type Style } from '#lib/masking/rules.js';
	import type { Masking } from '#lib/state/masking.svelte.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import type { MessageKey } from '#lib/i18n/translate.js';
	import Chip from './Chip.svelte';
	import Icon from './Icon.svelte';
	import OptionCards from './OptionCards.svelte';
	import Segmented from './Segmented.svelte';
	import Switch from './Switch.svelte';

	/**
	 * Method + "mask all" + categories — component for
	 * home (popover), review (toolbar) and settings.
	 *
	 * - `compact`: toggle with icon, switch box, optional replacement style
	 *   and chips for "personal data" and "circumstances", applicability note
	 *   with reference
	 * - `cards`: masking types as cards with example and technical term,
	 *   always followed by chips and replacement style
	 *
	 * Rules live in `Maskierung`; this component only displays and forwards them.
	 */
	let {
		masking,
		variant = 'kompakt',
		categories = false,
		stile = false,
		count,
		replaced,
		usability,
		scope,
		reference,
		eyebrow = false,
		class: className
	}: {
		masking: Masking;
		variant?: 'kompakt' | 'cards';
		/** Show category chips (review menu; always in `cards`). */
		categories?: boolean;
		/** Allow choosing replacement style (review menu; always in `cards`). */
		stile?: boolean;
		/** Number of found items per category, shown on the chip. */
		count?: Partial<Record<CategoryId, number>>;
		/** "17 items masked" — displayed to the right of the chips. */
		replaced?: string;
		/** Usability line (spec §6): "x % of text masked". */
		usability?: string;
		scope?: string;
		/** Reference links on the right footer: "More settings", "Change default". */
		reference?: Snippet;
		eyebrow?: boolean;
		class?: string;
	} = $props();

	const switchId = $props.id();

	const method = $derived(masking.method);
	const all = $derived(masking.all);

	const explanation = $derived(
		all
			? t('masking.all.on')
			: method === 'reversible'
				? t('masking.all.fromReversible')
				: t('masking.all.from')
	);

	const METHODS_TEXT: Record<
		Method,
		{ title: MessageKey; short: MessageKey; text: MessageKey; subject: MessageKey; example: string }
	> = {
		reversible: {
			title: 'masking.reversible',
			short: 'masking.reversible.short',
			text: 'masking.reversible.explanation',
			subject: 'masking.reversible.technicalTerm',
			example: 'Sabine Hofer → <<PERSON_ba903855>>'
		},
		irreversible: {
			title: 'masking.irreversible',
			short: 'masking.irreversible.short',
			text: 'masking.irreversible.explanation',
			subject: 'masking.irreversible.technicalTerm',
			example: 'Sabine Hofer → [Name]'
		}
	};

	const STYLE_TEXT: Record<Style, { title: MessageKey; text: MessageKey; example: string }> = {
		codes: {
			title: 'masking.style.codes',
			text: 'masking.style.codes.explanation',
			example: '<<PERSON_ba903855>>'
		},
		surrogates: {
			title: 'masking.style.surrogates',
			text: 'masking.style.surrogates.explanation',
			example: 'Petra Lang'
		},
		placeholder: {
			title: 'masking.style.placeholder',
			text: 'masking.style.placeholder.explanation',
			example: '[Name]'
		},
		mask: {
			title: 'masking.style.mask',
			text: 'masking.style.mask.explanation',
			example: '••••••'
		},
		remove: {
			title: 'masking.style.remove',
			text: 'masking.style.remove.explanation',
			example: '—'
		}
	};

	const methodsOptions = $derived(
		(['reversible', 'irreversible'] as const).map((value) => ({
			value,
			label: t(METHODS_TEXT[value].title),
			subline: t(METHODS_TEXT[value].short),
			icon: value === 'reversible' ? ('restore' as const) : ('lock' as const),
			title: t(METHODS_TEXT[value].short),
			text: t(METHODS_TEXT[value].text),
			example: METHODS_TEXT[value].example,
			technicalTerm: t(METHODS_TEXT[value].subject)
		}))
	);

	const styleOptions = $derived(
		STILE[method].map((value) => ({
			value,
			title: t(STYLE_TEXT[value].title),
			text: t(STYLE_TEXT[value].text),
			example: STYLE_TEXT[value].example
		}))
	);
	const styleSegments = $derived(
		styleOptions.map((option) => ({
			value: option.value,
			label: option.title,
			title: `${option.text} · ${option.example}`
		}))
	);
	const styleDescription = $derived(styleOptions.find((option) => option.value === masking.style));

	const person = $derived(masking.catalog.filter((k) => !k.circumstances));
	const circumstances = $derived(masking.catalog.filter((k) => k.circumstances));
</script>

{#snippet chip(category: (typeof masking.catalog)[number])}
	<Chip
		on={masking.categories.has(category.id)}
		locked={category.locked}
		num={count?.[category.id]}
		size={variant === 'cards' ? 'md' : 'sm'}
		onclick={() => masking.toggleCategory(category.id)}
	>
		{t(`category.${category.id}`)}{#if category.locked}<span class="font-normal"
				>{`· ${t('masking.always')}`}</span
			>{/if}
	</Chip>
{/snippet}

{#snippet chips()}
	<div class="flex flex-col gap-2">
		<div class="flex items-baseline justify-between gap-2">
			<span class="text-fine font-semibold tracking-[0.04em] text-muted uppercase"
				>{t('masking.whatGetsMasked')}</span
			>
			{#if replaced}<span class="text-caption text-subtle">{replaced}</span>{/if}
		</div>
		<div role="group" aria-label={t('masking.personDetails')} class="flex flex-wrap gap-1.5">
			{#each person as category (category.id)}{@render chip(category)}{/each}
		</div>
		<div
			role="group"
			aria-label={t('masking.circumstances')}
			class="flex flex-wrap items-center gap-1.5"
		>
			<span class="mr-0.5 text-caption text-subtle">{t('masking.circumstances.label')}</span>
			{#each circumstances as category (category.id)}{@render chip(category)}{/each}
		</div>
		{#if usability}<p class="text-caption text-subtle">{usability}</p>{/if}
	</div>
{/snippet}

{#snippet allBox(large: boolean)}
	<div
		class={cn(
			'flex items-center gap-3 border',
			large ? 'gap-3.5 rounded-lg border-[1.5px] px-4 py-3.5' : 'rounded-lg px-3.5 py-3',
			all
				? 'border-tint-line bg-tint-1'
				: large
					? 'border-line bg-surface'
					: 'border-line bg-surface-2'
		)}
	>
		<span
			aria-hidden="true"
			class={cn(
				'inline-flex shrink-0 items-center justify-center rounded-full',
				large ? 'size-8.5' : 'size-7.5',
				all ? 'bg-ink text-on-ink' : 'bg-chip text-muted'
			)}
		>
			<Icon name="lockFull" size={large ? 16 : 14} />
		</span>
		<span class="flex min-w-0 flex-1 flex-col gap-0.5">
			<span id={switchId} class={cn('font-semibold', large && 'text-lead')}>{t('masking.all')}</span
			>
			<span class={cn('text-pretty text-muted', large ? 'text-label' : 'text-meta')}
				>{explanation}</span
			>
			{#if large}<span class="text-fine text-subtle">{t('masking.all.technicalTerm')}</span>{/if}
		</span>
		<Switch
			checked={all}
			labelledby={switchId}
			size={large ? 'md' : 'sm'}
			oncheckedchange={() => masking.toggleAll()}
		/>
	</div>
{/snippet}

<div class={cn('flex flex-col', variant === 'cards' ? 'gap-5' : 'gap-4', className)}>
	{#if variant === 'kompakt'}
		<div class="flex flex-col gap-2">
			{#if eyebrow}
				<span class="text-fine font-semibold tracking-[0.04em] text-muted uppercase"
					>{t('masking.title')}</span
				>
			{/if}
			<Segmented
				variant="map"
				label={t('masking.kind')}
				options={methodsOptions}
				value={method}
				onwechsel={(fresh) => masking.selectMethod(fresh)}
			/>
			{#if categories}
				<p class="text-meta text-pretty text-muted">{t(METHODS_TEXT[method].text)}</p>
			{/if}
		</div>
		{@render allBox(false)}
		{#if stile}
			<div class="flex flex-col gap-2">
				<span class="text-fine font-semibold tracking-[0.04em] text-muted uppercase"
					>{t('masking.style.short')}</span
				>
				<Segmented
					size="sm"
					label={t('masking.style')}
					options={styleSegments}
					value={masking.style}
					onwechsel={(fresh) => masking.selectStyle(fresh)}
					class="self-start"
				/>
				{#if styleDescription}
					<p class="text-caption text-subtle">
						<span class="font-mono">{styleDescription.example}</span> · {styleDescription.text}
					</p>
				{/if}
			</div>
		{/if}
		{#if categories}{@render chips()}{/if}
		{#if scope || reference}
			<div
				class={cn(
					'flex items-center justify-between gap-2 text-caption text-subtle',
					categories && 'border-t border-line-soft pt-3'
				)}
			>
				<span>{scope}</span>
				{@render reference?.()}
			</div>
		{/if}
	{:else}
		<OptionCards
			label={t('masking.kind')}
			options={methodsOptions.map((option) => ({ ...option, title: option.label }))}
			value={method}
			standardMark
			onwechsel={(fresh) => masking.selectMethod(fresh)}
		/>
		{@render allBox(true)}
		<div class="flex flex-col gap-2">
			<h4 class="text-body font-semibold">{t('masking.whatHappens')}</h4>
			<div class="flex flex-wrap gap-2">
				{#each masking.catalog as category (category.id)}{@render chip(category)}{/each}
			</div>
		</div>
		<div class="flex flex-col gap-2">
			<h4 class="text-body font-semibold">{t('masking.style')}</h4>
			<OptionCards
				layout="list"
				label={t('masking.style')}
				options={styleOptions}
				value={masking.style}
				onwechsel={(fresh) => masking.selectStyle(fresh)}
			/>
		</div>
	{/if}
</div>
