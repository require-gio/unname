<script lang="ts" module>
	export interface Option<W extends string> {
		value: W;
		title: string;
		text?: string;
		/** Dense example (“Sabine Hofer → <<PERSON_…>>”). */
		example?: string;
		technicalTerm?: string;
		/** Short code inside a circle (language cards). */
		shortCode?: string;
	}
</script>

<script lang="ts" generics="T extends string">
	import { RadioGroup } from 'bits-ui';
	import { cn } from '#lib/utils.js';
	import { t } from '#lib/state/i18n.svelte.js';

	/**
	 * Render a selectable set as cards: masking styles, replacement style,
	 * AI policy, app language. Implemented as a radio group.
	 *
	 * - `grid`: cards side-by-side with title, text, example, technical term;
	 *   the selected one shows “Standard” when `standardMark` is true
	 * - `list`: rows with a radio dot on the left
	 * - `language`: short code in a circle, name, radio dot on the right
	 */

	let {
		options,
		value = $bindable(),
		label,
		labelledby,
		layout = 'grid',
		standardMark = false,
		onwechsel,
		class: className
	}: {
		options: readonly Option<T>[];
		value: T;
		label?: string;
		labelledby?: string;
		layout?: 'grid' | 'list' | 'language';
		standardMark?: boolean;
		onwechsel?: (value: T) => void;
		class?: string;
	} = $props();
</script>

{#snippet point(checked: boolean)}
	<span
		aria-hidden="true"
		class={cn(
			'block size-5 shrink-0 rounded-full bg-surface',
			checked ? 'border-[6px] border-accent' : 'border-[1.5px] border-line-dash'
		)}
	></span>
{/snippet}

<RadioGroup.Root
	bind:value
	onValueChange={(fresh) => onwechsel?.(fresh as T)}
	aria-label={label}
	aria-labelledby={labelledby}
	class={cn(
		layout === 'list'
			? 'flex flex-col gap-1.5'
			: 'grid grid-cols-[repeat(auto-fit,minmax(min(220px,100%),1fr))] gap-2.5',
		className
	)}
>
	{#each options as option (option.value)}
		<RadioGroup.Item
			value={option.value}
			class={cn(
				'flex cursor-pointer border-[1.5px] border-line bg-surface text-left font-sans text-ink transition-colors hover:border-line-dash aria-checked:border-accent',
				layout === 'grid' && 'flex-col gap-2 rounded-lg p-4 aria-checked:halo-accent',
				layout === 'list' && 'items-center gap-3 rounded-md px-3.5 py-2.5',
				layout === 'language' &&
					'items-center gap-3 rounded-lg px-4 py-3.5 aria-checked:halo-accent'
			)}
		>
			{#snippet children({ checked })}
				{#if layout === 'grid'}
					<span class="flex w-full items-center justify-between gap-2">
						<span class="text-lead font-semibold">{option.title}</span>
						{#if standardMark && checked}
							<span
								class="rounded-full bg-accent px-2.25 py-0.75 text-fine font-semibold text-on-accent"
								>{t('common.standard')}</span
							>
						{/if}
					</span>
					{#if option.text}<span class="text-label text-pretty text-muted">{option.text}</span>{/if}
					{#if option.example}
						<span class="block rounded-sm bg-bg px-2.25 py-1.75 font-mono text-fine text-ink-2"
							>{option.example}</span
						>
					{/if}
					{#if option.technicalTerm}
						<span class="text-fine text-subtle">{option.technicalTerm}</span>
					{/if}
				{:else if layout === 'list'}
					{@render point(checked)}
					<span class="font-semibold">{option.title}</span>
					{#if option.example}
						<span class="rounded-sm bg-chip px-2 py-0.5 font-mono text-caption text-ink-2"
							>{option.example}</span
						>
					{/if}
					{#if option.text}<span class="flex-1 text-label text-muted">{option.text}</span>{/if}
				{:else}
					<span
						aria-hidden="true"
						class={cn(
							'inline-flex size-9 shrink-0 items-center justify-center rounded-full font-mono text-caption font-medium',
							checked ? 'bg-accent text-on-accent' : 'bg-chip text-muted'
						)}>{option.shortCode}</span
					>
					<span class="flex flex-1 flex-col">
						<span class="text-lead font-semibold">{option.title}</span>
						{#if option.text}<span class="text-meta text-subtle">{option.text}</span>{/if}
					</span>
					{@render point(checked)}
				{/if}
			{/snippet}
		</RadioGroup.Item>
	{/each}
</RadioGroup.Root>
