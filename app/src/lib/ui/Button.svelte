<script lang="ts" module>
	import { tv, type VariantProps } from '#lib/utils.js';

	export const button = tv({
		base: 'inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md border font-sans no-underline transition-colors select-none disabled:cursor-not-allowed disabled:opacity-55 aria-disabled:cursor-not-allowed aria-disabled:opacity-55',
		variants: {
			variant: {
				/** Primary action button for a view. */
				primary: 'border-ink bg-ink font-semibold text-on-ink hover:bg-ink-2',
				secondary:
					'border-line-strong bg-surface font-medium text-ink hover:border-faint hover:bg-surface-2',
				ghost:
					'border-transparent bg-transparent font-medium text-muted hover:bg-chip hover:text-ink',
				link: 'h-auto! border-transparent bg-transparent px-1! font-medium text-link hover:text-tint-ink-strong hover:underline',
				/** Accepts AI suggestions – indigo style for masked results. */
				tint: 'border-tint-line-soft bg-tint-2 font-semibold text-tint-ink hover:bg-tint-3',
				danger: 'border-red-line bg-surface font-medium text-red-ink hover:bg-red-0',
				/** Used in the dark floating bar. */
				bar: 'border-transparent bg-transparent font-medium text-bar-muted hover:bg-bar-raised hover:text-bar-ink'
			},
			size: {
				sm: 'h-control-sm px-3 text-label',
				md: 'h-control-md px-4 text-body',
				lg: 'h-control-lg px-5.5 text-lead'
			},
			wide: { true: 'w-full' }
		},
		defaultVariants: { variant: 'secondary', size: 'md' }
	});

	export type ButtonVariant = NonNullable<VariantProps<typeof button>['variant']>;
	export type ButtonSize = NonNullable<VariantProps<typeof button>['size']>;
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLAnchorAttributes, HTMLButtonAttributes } from 'svelte/elements';
	import Icon from './Icon.svelte';
	import Spinner from './Spinner.svelte';
	import type { IconName } from './icons.js';
	import { t } from '#lib/state/i18n.svelte.js';

	/**
	 * Button or link styled as a button.
	 * `busy` disables it and shows progress without changing size.
	 */
	type Shared = {
		variant?: ButtonVariant;
		size?: ButtonSize;
		wide?: boolean;
		icon?: IconName;
		iconRight?: IconName;
		busy?: boolean;
		class?: string;
		children?: Snippet;
	};

	type Props = Shared &
		(
			| ({ href: string } & Omit<HTMLAnchorAttributes, 'class' | 'children'>)
			| ({ href?: undefined } & Omit<HTMLButtonAttributes, 'class' | 'children'>)
		);

	let {
		variant,
		size,
		wide,
		icon,
		iconRight,
		busy = false,
		class: className,
		children,
		...rest
	}: Props = $props();

	const classes = $derived(button({ variant, size, wide, class: className }));
	const iconSize = $derived(size === 'sm' ? 15 : size === 'lg' ? 18 : 16);
</script>

{#snippet content()}
	{#if busy}
		<Spinner size={iconSize} />
	{:else if icon}
		<Icon name={icon} size={iconSize} />
	{/if}
	{@render children?.()}
	{#if iconRight}
		<Icon name={iconRight} size={iconSize} />
	{/if}
{/snippet}

{#if rest.href !== undefined}
	{@const { href, ...link } = rest as HTMLAnchorAttributes}
	<a
		{href}
		{...link}
		class={classes}
		aria-busy={busy || undefined}
		aria-disabled={busy || undefined}
	>
		{@render content()}
	</a>
{:else}
	{@const { type = 'button', disabled, ...buttonRest } = rest as HTMLButtonAttributes}
	<button
		{type}
		{...buttonRest}
		disabled={disabled || busy}
		class={classes}
		aria-busy={busy || undefined}
	>
		{@render content()}
		{#if busy}<span class="sr-only">{t('common.loads')}</span>{/if}
	</button>
{/if}
