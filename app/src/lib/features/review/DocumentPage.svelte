<script lang="ts">
	import { cn } from '#lib/utils.js';
	import type { Segment } from '#lib/ui/segments.js';
	import SegmentText from '#lib/ui/SegmentText.svelte';

	/**
	 * A document page (review): newsreader, page size, paragraph.
	 * For long documents the browser only renders the visible portion
	 * (using `content-visibility`). Each segment carries its anchor for
	 * highlighting; the code menu is shared and attached to the viewer,
	 * not here.
	 */
	let {
		paragraphs,
		active,
		onsegment,
		article = $bindable(),
		class: className
	}: {
		paragraphs: readonly (readonly Segment[])[];
		active?: string;
		onsegment?: (segment: Segment, element: HTMLElement, paragraph: number) => void;
		article?: HTMLElement;
		class?: string;
	} = $props();
</script>

<article
	bind:this={article}
	data-column="einzeln"
	class={cn(
		'rounded-xs bg-surface px-17 pt-15 pb-18 font-doc text-doc leading-[1.7] text-doc-ink shadow-page',
		className
	)}
>
	{#each paragraphs as paragraph, index (index)}
		<p class="mt-3.5 [contain-intrinsic-size:auto_3lh] [content-visibility:auto] first:mt-0">
			<SegmentText
				segments={paragraph}
				anchor={index}
				{active}
				onsegment={onsegment ? (segment, element) => onsegment(segment, element, index) : undefined}
			/>
		</p>
	{/each}
</article>
