<script lang="ts" module>
	import { tv, type VariantProps } from '#lib/utils.js';

	export const card = tv({
		base: 'flex flex-col bg-surface',
		variants: {
			tone: {
				standard: 'border border-line',
				/** Accent tone: indigo border with halo effect. */
				accent: 'halo-accent border-[1.5px] border-accent',
				/** Amber tone: amber border for question state. */
				amber: 'border border-amber-line shadow-card',
				danger: 'border border-red-line-soft bg-red-0',
				quiet: 'border border-line bg-surface-3',
				tint: 'border border-tint-line-soft bg-tint-1',
				/** Flat tone: no border, used on alternative surface. */
				flat: 'border-0'
			},
			radius: { md: 'rounded-md', lg: 'rounded-lg', xl: 'rounded-xl' },
			gap: { none: '', sm: 'gap-2.5 p-3.5', md: 'gap-3 p-4', lg: 'gap-4.5 p-6' }
		},
		defaultVariants: { tone: 'standard', radius: 'lg', gap: 'md' }
	});

	export type CardTone = NonNullable<VariantProps<typeof card>['tone']>;
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLAttributes } from 'svelte/elements';

	/** Surface with border. `as` selects the element — `section`, `article`, `li` … */
	let {
		as = 'div',
		tone,
		radius,
		gap,
		children,
		class: className,
		...rest
	}: {
		as?: 'div' | 'section' | 'article' | 'li' | 'aside';
		tone?: CardTone;
		radius?: 'md' | 'lg' | 'xl';
		gap?: 'none' | 'sm' | 'md' | 'lg';
		children: Snippet;
		class?: string;
	} & Omit<HTMLAttributes<HTMLElement>, 'class' | 'children'> = $props();
</script>

<svelte:element this={as} {...rest} class={card({ tone, radius, gap, class: className })}>
	{@render children()}
</svelte:element>
