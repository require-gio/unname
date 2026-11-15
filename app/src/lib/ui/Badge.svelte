<script lang="ts" module>
	import { tv, type VariantProps } from '#lib/utils.js';

	export const plakette = tv({
		base: 'inline-flex shrink-0 items-center gap-1.5 font-sans font-semibold whitespace-nowrap',
		variants: {
			tone: {
				neutral: '',
				tint: '',
				ink: '',
				amber: '',
				green: '',
				red: '',
				accent: ''
			},
			flat: {
				false: 'rounded-full px-2.5 py-0.5 text-meta',
				/** Colored text only, e.g. "aktuell" or "bitte aktualisieren" in lists. */
				true: 'text-meta font-medium'
			}
		},
		compoundVariants: [
			{ flat: false, tone: 'neutral', class: 'bg-chip text-muted' },
			{ flat: false, tone: 'tint', class: 'bg-tint-2 text-tint-ink' },
			{ flat: false, tone: 'ink', class: 'bg-ink text-on-ink' },
			{ flat: false, tone: 'amber', class: 'bg-amber-1 text-amber-ink-2' },
			{ flat: false, tone: 'green', class: 'bg-green-1 text-green-ink' },
			{ flat: false, tone: 'red', class: 'bg-red-1 text-red-ink' },
			{ flat: false, tone: 'accent', class: 'bg-accent text-on-accent' },
			{ flat: true, tone: 'neutral', class: 'text-subtle' },
			{ flat: true, tone: 'tint', class: 'text-tint-ink' },
			{ flat: true, tone: 'ink', class: 'text-ink' },
			{ flat: true, tone: 'amber', class: 'text-amber-ink-2' },
			{ flat: true, tone: 'green', class: 'text-green-ink' },
			{ flat: true, tone: 'red', class: 'text-red-ink' },
			{ flat: true, tone: 'accent', class: 'text-accent-fg' }
		],
		defaultVariants: { tone: 'neutral', flat: false }
	});

	export type BadgeTone = NonNullable<VariantProps<typeof plakette>['tone']>;
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';
	import type { IconName } from './icons.js';

	/** Short status label: "installed", "required", "recommended", "default", "suggestion", … */
	let {
		tone = 'neutral',
		flat = false,
		icon,
		children,
		class: className
	}: {
		tone?: BadgeTone;
		flat?: boolean;
		icon?: IconName;
		children: Snippet;
		class?: string;
	} = $props();
</script>

<span class={plakette({ tone, flat, class: className })}>
	{#if icon}<Icon name={icon} size={12} strokeWidth={2.2} />{/if}
	{@render children()}
</span>
