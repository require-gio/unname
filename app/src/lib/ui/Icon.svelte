<script lang="ts">
	import { cn } from '#lib/utils.js';
	import { FILLED, ICONS, type IconName } from './icons.js';

	/**
	 * Decorative icon (`aria-hidden` when no `label` is provided).
	 * Meaning is supplied as adjacent text or via the element's `aria-label`.
	 */
	let {
		name,
		size = 16,
		strokeWidth = 2,
		label,
		class: className
	}: {
		name: IconName;
		size?: number;
		strokeWidth?: number;
		label?: string;
		class?: string;
	} = $props();

	const Lucide = $derived(name === 'lockFull' ? null : ICONS[name]);
	const filled = $derived(FILLED.has(name));
</script>

{#if Lucide}
	<Lucide
		{size}
		{strokeWidth}
		fill={filled ? 'currentColor' : 'none'}
		aria-hidden={label ? undefined : 'true'}
		aria-label={label}
		role={label ? 'img' : undefined}
		class={cn('shrink-0', className)}
	/>
{:else}
	<!-- Lock with filled body: icon for the highest masking level. -->
	<svg
		width={size}
		height={size}
		viewBox="0 0 24 24"
		fill="none"
		stroke="currentColor"
		stroke-width={strokeWidth}
		stroke-linecap="round"
		stroke-linejoin="round"
		aria-hidden={label ? undefined : 'true'}
		aria-label={label}
		role={label ? 'img' : undefined}
		class={cn('shrink-0', className)}
	>
		<rect x="5" y="11" width="14" height="9" rx="2" fill="currentColor" />
		<path d="M8 11V8a4 4 0 0 1 8 0v3" />
	</svg>
{/if}
