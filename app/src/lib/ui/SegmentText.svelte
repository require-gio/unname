<script lang="ts">
	import { cn } from '#lib/utils.js';
	import type { Segment } from '#lib/ui/segments.js';
	import { t } from '#lib/state/i18n.svelte.js';
	import { segmentCaption } from './caption.js';
	import {
		ACTIVE_STYLE,
		OPERABLE_STYLE,
		QUESTION_NUMBER_STYLE,
		MARKIERBAR,
		SEGMENT_STYLE
	} from './segment-styles.js';

	/**
	 * Renderer for segmented text (spec §2): review, text mode,
	 * restore, example settings, pattern test.
	 *
	 * Segments act as buttons; `onsegment` receives the segment and the
	 * element where the shared menu should attach. The original code
	 * ("Original: Hofer") shows a pure CSS peek on hover or keyboard focus —
	 * no tooltip per code.
	 *
	 * With `anchor` (paragraph number) each segment gets
	 * `data-seg="<paragraph>:<index>"` in the DOM. This lets the review step
	 * highlight the location in the original (`#lib/review/auswahl.ts`). Segments
	 * with an ID also get `data-id` so Alt+N can jump to a question.
	 */
	let {
		segments,
		onsegment,
		active,
		mark = true,
		peek = true,
		peekOpen,
		anchor,
		class: className
	}: {
		segments: readonly Segment[];
		onsegment?: (segment: Segment, element: HTMLElement) => void;
		/** ID of the segment whose menu is currently open. */
		active?: string;
		/** Highlight used locations (restore). */
		mark?: boolean;
		peek?: boolean;
		/** Keep the peek visible on a segment – used on the components page. */
		peekOpen?: string;
		/** Paragraph number; enables `data-seg` on segments. */
		anchor?: number;
		class?: string;
	} = $props();

	const seg = (index: number) => (anchor === undefined ? undefined : `${anchor}:${index}`);

	function style(segment: Segment): string {
		return MARKIERBAR.has(segment.kind) && !mark ? '' : SEGMENT_STYLE[segment.kind];
	}

	function buttonClass(segment: Segment): string {
		return cn(
			'inline border-0 p-0 text-left align-baseline',
			style(segment),
			OPERABLE_STYLE,
			active === segment.id && ACTIVE_STYLE,
			segment.text === '' && 'inline-block h-[1em] w-1.5 align-middle'
		);
	}

	function spanClass(segment: Segment): string {
		return cn(style(segment), segment.id !== undefined && active === segment.id && ACTIVE_STYLE);
	}

	function peekClass(segment: Segment): string {
		return cn(
			'pointer-events-none absolute bottom-[calc(100%+6px)] left-0 z-10 hidden rounded-sm bg-bar px-2.25 py-1.25 font-sans text-caption leading-tight font-medium whitespace-nowrap text-bar-ink shadow-peek group-focus-within:block group-hover:block',
			peekOpen === segment.id && 'block'
		);
	}
</script>

<!--
	No whitespace from the source may appear between segments: Svelte turned it into a visible space, and the window then displayed a different text than the file. Therefore the nodes are placed tightly together here;
	`components.spec.ts` checks the rendered text.
-->
<span class={cn('whitespace-pre-wrap', className)}
	>{#each segments as segment, index (segment.id ?? `p${index}`)}{#if segment.operable && onsegment}<span
				class="group relative"
				>{#if peek && segment.original !== undefined && active !== segment.id}<span
						aria-hidden="true"
						class={peekClass(segment)}
						>{t('code.original', { text: segment.original || t('code.removed') })}</span
					>{/if}<button
					type="button"
					aria-haspopup="menu"
					aria-expanded={active === segment.id}
					aria-label={segmentCaption(segment)}
					data-seg={seg(index)}
					data-source={segment.source ? `${segment.source.start}:${segment.source.end}` : undefined}
					data-id={segment.id}
					onclick={(event) => onsegment(segment, event.currentTarget)}
					class={buttonClass(segment)}>{segment.text}</button
				></span
			>{:else if segment.kind === 'plain' && anchor === undefined}{segment.text}{:else if segment.kind === 'plain'}<span
				data-seg={seg(index)}
				data-source={segment.source ? `${segment.source.start}:${segment.source.end}` : undefined}
				>{segment.text}</span
			>{:else}<span
				class={spanClass(segment)}
				data-seg={seg(index)}
				data-id={segment.id}
				data-source={segment.source ? `${segment.source.start}:${segment.source.end}` : undefined}
				>{segment.text}</span
			>{#if segment.kind === 'question' && segment.number !== undefined}<sup
					class={QUESTION_NUMBER_STYLE}
					aria-label={t('question.number', { n: segment.number })}>{segment.number}</sup
				>{/if}{/if}{/each}</span
>
