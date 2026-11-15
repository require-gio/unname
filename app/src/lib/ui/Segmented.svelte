<script lang="ts" module>
	import type { IconName } from './icons.js';

	export interface SegmentOption<W extends string> {
		value: W;
		label: string;
		icon?: IconName;
		subline?: string;
		title?: string;
	}
</script>

<script lang="ts" generics="T extends string">
	import { RadioGroup } from 'bits-ui';
	import { cn } from '#lib/utils.js';
	import Icon from './Icon.svelte';

	/**
	 * Horizontal choice of a few options.
	 * Example: Dokumente | Text, Hell | Dunkel | System, Reversibel | Irreversibel …
	 *
	 * Implemented as a radio group: arrow keys change selection, Tab leaves the group.
	 *
	 * - `hell`: pill on light background (home, settings)
	 * - `leiste`: in the dark floating bar; with `onlyActiveBeschriftet` only the icon is shown
	 *   (the name is provided via `aria-label`)
	 * - `karte`: large fields with icon circle and subline (masking style)
	 */

	let {
		options,
		value = $bindable(),
		label,
		variant = 'light',
		size = 'md',
		onlyActiveBeschriftet = false,
		onwechsel,
		class: className
	}: {
		options: readonly SegmentOption<T>[];
		value: T;
		label: string;
		variant?: 'light' | 'bar' | 'map';
		size?: 'sm' | 'md' | 'lg';
		onlyActiveBeschriftet?: boolean;
		onwechsel?: (value: T) => void;
		class?: string;
	} = $props();

	const spur = $derived(
		variant === 'bar'
			? 'gap-1'
			: variant === 'map'
				? 'grid grid-cols-2 gap-1 rounded-lg bg-chip p-1'
				: size === 'lg'
					? 'gap-0.5 rounded-md bg-skeleton p-1'
					: 'gap-0.5 rounded-md bg-side p-0.75'
	);

	const field = $derived(
		variant === 'bar'
			? 'h-9.5 gap-2 rounded-md px-2.75 text-label text-bar-muted hover:text-bar-ink aria-checked:bg-bar-on aria-checked:font-semibold aria-checked:text-bar-on-ink'
			: variant === 'map'
				? 'min-h-13 gap-2.5 rounded-md border border-transparent px-3 py-2 text-left text-ink aria-checked:border-line-strong aria-checked:bg-surface aria-checked:shadow-raised'
				: cn(
						'font-medium text-muted hover:text-ink aria-checked:bg-surface aria-checked:font-semibold aria-checked:text-ink aria-checked:shadow-raised',
						size === 'lg' && 'gap-2 rounded-sm px-4.5 py-2.25 text-body',
						size === 'md' && 'gap-2 rounded-sm px-4 py-2 text-ui',
						size === 'sm' && 'gap-1.5 rounded-sm px-3 py-1.25 text-meta'
					)
	);

	const iconSize = $derived(variant === 'bar' ? 18 : size === 'sm' ? 14 : 16);
</script>

<RadioGroup.Root
	bind:value
	onValueChange={(fresh) => onwechsel?.(fresh as T)}
	orientation="horizontal"
	aria-label={label}
	class={cn('inline-flex items-stretch', spur, className)}
>
	{#each options as option (option.value)}
		<RadioGroup.Item
			value={option.value}
			title={option.title}
			aria-label={onlyActiveBeschriftet ? option.label : undefined}
			class={cn(
				'inline-flex min-w-0 cursor-pointer items-center font-sans transition-colors',
				variant !== 'map' && 'whitespace-nowrap',
				field
			)}
		>
			{#snippet children({ checked })}
				{#if variant === 'map'}
					<span
						aria-hidden="true"
						class={cn(
							'inline-flex size-7.5 shrink-0 items-center justify-center rounded-full',
							checked ? 'bg-accent text-on-accent' : 'bg-chip-strong text-muted'
						)}
					>
						{#if option.icon}<Icon name={option.icon} size={15} strokeWidth={1.9} />{/if}
					</span>
					<span class="flex min-w-0 flex-col">
						<span class="font-semibold">{option.label}</span>
						{#if option.subline}
							<span class="text-caption text-muted">{option.subline}</span>
						{/if}
					</span>
				{:else}
					{#if option.icon}
						<Icon name={option.icon} size={iconSize} strokeWidth={1.8} />
					{/if}
					{#if !onlyActiveBeschriftet || checked}
						<span>{option.label}</span>
					{/if}
				{/if}
			{/snippet}
		</RadioGroup.Item>
	{/each}
</RadioGroup.Root>
