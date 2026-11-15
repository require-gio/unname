<script lang="ts">
	import { arrow, autoUpdate, computePosition, flip, offset, shift } from '@floating-ui/dom';
	import { cn } from '#lib/utils.js';
	import type { CoachMarkPage } from '#lib/state/help.svelte.js';

	/**
	 * A numbered help hint.
	 *
	 * With `anchor` it floats next to the element and follows scrolling;
	 * without it stays fixed – as shown on the components page.
	 * It does not accept clicks: it explains, it does not act.
	 */
	let {
		number,
		text,
		page = 'bottom',
		anchor,
		id,
		class: className
	}: {
		number: number;
		text: string;
		page?: CoachMarkPage;
		anchor?: HTMLElement;
		id?: string;
		class?: string;
	} = $props();

	let notiz = $state<HTMLElement>();
	let pfeil = $state<HTMLElement>();
	/** The actual placement – `flip()` will change it if there isn’t enough space. */
	let placed = $derived<CoachMarkPage>(page);

	$effect(() => {
		if (!anchor || !notiz || !pfeil) return;
		const target = anchor;
		const element = notiz;
		const spitze = pfeil;
		return autoUpdate(target, element, async () => {
			const position = await computePosition(target, element, {
				strategy: 'fixed',
				placement: page,
				middleware: [
					offset(14),
					flip(),
					shift({ padding: 12 }),
					arrow({ element: spitze, padding: 14 })
				]
			});
			placed = position.placement.split('-')[0] as CoachMarkPage;
			Object.assign(element.style, { left: `${position.x}px`, top: `${position.y}px` });
			const versatz = position.middlewareData.arrow;
			Object.assign(spitze.style, {
				left: versatz?.x !== undefined ? `${versatz.x}px` : '',
				top: versatz?.y !== undefined ? `${versatz.y}px` : ''
			});
		});
	});

	/** The arrow points to the target, i.e., opposite the placement. */
	const SPITZE: Record<CoachMarkPage, string> = {
		bottom: '-top-1.5 left-5',
		top: '-bottom-1.5 left-5',
		right: '-left-1.5 top-3.5',
		left: '-right-1.5 top-3.5'
	};
</script>

<div
	bind:this={notiz}
	{id}
	role="note"
	class={cn(
		'pointer-events-none z-40 flex w-max max-w-[270px] items-start gap-2.5 rounded-md bg-accent px-3.5 py-3 font-sans text-label leading-snug font-medium text-on-accent shadow-coach',
		anchor ? 'fixed top-0 left-0' : 'relative',
		className
	)}
>
	<span
		bind:this={pfeil}
		aria-hidden="true"
		class={cn('absolute size-3 rotate-45 bg-accent', SPITZE[placed])}
	></span>
	<span
		aria-hidden="true"
		class="relative inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-surface text-fine font-bold text-link"
		>{number}</span
	>
	<span class="relative">{text}</span>
</div>
