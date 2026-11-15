<script lang="ts" module>
	import { tv, type VariantProps } from '#lib/utils.js';

	export const symbolButton = tv({
		base: 'inline-flex shrink-0 items-center justify-center border transition-colors disabled:cursor-not-allowed disabled:opacity-55',
		variants: {
			variant: {
				ghost: 'border-transparent bg-transparent text-muted hover:bg-chip hover:text-ink',
				secondary: 'border-line-strong bg-surface text-ink shadow-1 hover:bg-surface-2',
				quiet: 'border-transparent bg-bg text-muted hover:bg-chip hover:text-ink',
				link: 'border-transparent bg-transparent text-link hover:bg-tint-1',
				bar: 'border-transparent bg-transparent text-bar-muted hover:bg-bar-raised hover:text-bar-ink'
			},
			form: { eckig: 'rounded-md', rund: 'rounded-full' },
			size: { sm: 'size-control-sm', md: 'size-control-md' }
		},
		defaultVariants: { variant: 'ghost', form: 'eckig', size: 'md' }
	});

	export type SymbolButtonVariant = NonNullable<VariantProps<typeof symbolButton>['variant']>;
</script>

<script lang="ts">
	import type { HTMLAnchorAttributes, HTMLButtonAttributes } from 'svelte/elements';
	import Icon from './Icon.svelte';
	import type { IconName } from './icons.js';

	/**
	 * Button that displays only an icon. `label` is required: we set `aria-label`
	 * and tooltip title because an icon alone provides no information to screen readers.
	 */
	type Shared = {
		label: string;
		icon: IconName;
		variant?: SymbolButtonVariant;
		form?: 'eckig' | 'rund';
		size?: 'sm' | 'md';
		iconSize?: number;
		class?: string;
	};

	type Props = Shared &
		(
			| ({ href: string } & Omit<HTMLAnchorAttributes, 'class' | 'children' | 'aria-label'>)
			| ({ href?: undefined } & Omit<HTMLButtonAttributes, 'class' | 'children' | 'aria-label'>)
		);

	let {
		label,
		icon,
		variant,
		form,
		size,
		iconSize = 18,
		class: className,
		...rest
	}: Props = $props();

	const classes = $derived(symbolButton({ variant, form, size, class: className }));
</script>

{#if rest.href !== undefined}
	{@const { href, ...link } = rest as HTMLAnchorAttributes}
	<a {href} {...link} aria-label={label} title={label} class={classes}>
		<Icon name={icon} size={iconSize} strokeWidth={1.8} />
	</a>
{:else}
	{@const { type = 'button', ...button } = rest as HTMLButtonAttributes}
	<button {type} {...button} aria-label={label} title={label} class={classes}>
		<Icon name={icon} size={iconSize} strokeWidth={1.8} />
	</button>
{/if}
