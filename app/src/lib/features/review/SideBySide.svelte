<script lang="ts">
	import { cn } from '#lib/utils.js';
	import type { Segment } from '#lib/ui/segments.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import SegmentText from '#lib/ui/SegmentText.svelte';

	/**
	 * Side-by-side view (Board "review"): original and result paragraph
	 * pairs from the sidecar `blocks`. One line per paragraph, scrolling both
	 * columns together. The right column is interactive; the left shows the
	 * original for comparison.
	 */
	let {
		lines,
		active,
		onsegment,
		article = $bindable(),
		class: className
	}: {
		lines: readonly { links: readonly Segment[]; right: readonly Segment[] }[];
		active?: string;
		onsegment?: (segment: Segment, element: HTMLElement, paragraph: number) => void;
		article?: HTMLElement;
		class?: string;
	} = $props();
</script>

<article
	bind:this={article}
	class={cn(
		'rounded-xs bg-surface px-8 pt-7.5 pb-11 font-doc text-ui leading-[1.65] text-doc-ink shadow-page',
		className
	)}
>
	<div
		aria-hidden="true"
		class="mb-1.5 grid grid-cols-2 border-b border-line-soft pb-3 font-sans text-fine font-semibold tracking-[0.04em] text-muted uppercase"
	>
		<span class="pr-5">{t('review.column.original')}</span>
		<span class="border-l border-line-soft pl-5">{t('review.column.result')}</span>
	</div>
	{#each lines as line, index (index)}
		<div class="grid grid-cols-2 [contain-intrinsic-size:auto_3lh] [content-visibility:auto]">
			<div data-column="original" class="py-1.75 pr-5">
				<p class="sr-only">{t('review.column.original')}</p>
				<p><SegmentText segments={line.links} anchor={index} peek={false} /></p>
			</div>
			<div data-column="result" class="border-l border-line-soft py-1.75 pl-5">
				<p class="sr-only">{t('review.column.result')}</p>
				<p>
					<SegmentText
						segments={line.right}
						anchor={index}
						{active}
						onsegment={onsegment
							? (segment, element) => onsegment(segment, element, index)
							: undefined}
					/>
				</p>
			</div>
		</div>
	{/each}
</article>
