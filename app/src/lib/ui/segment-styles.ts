import type { SegmentKind } from '#lib/ui/segments.js';

/**
 * Map of styles for all segment kinds – one entry for each of six text views.
 * Color indicates state: indigo = masked, amber = needs decision, underlines stay visible.
 *
 * Classes are written out so Tailwind can detect them in the source.
 */
export const SEGMENT_STYLE: Record<SegmentKind, string> = {
	plain: '',
	code: 'rounded-xs bg-tint-3 px-1.5 py-0.5 align-[0.06em] font-mono text-[0.74em] whitespace-nowrap text-tint-ink',
	placeholder:
		'rounded-xs bg-tint-3 px-1.75 py-px font-sans text-[0.84em] font-semibold whitespace-nowrap text-tint-ink',
	'masked-original': 'rounded-xs bg-tint-4 px-0.75 py-px text-tint-ink-strong box-decoration-clone',
	question:
		'rounded-xs bg-amber-2 px-0.75 py-px text-amber-ink outline-[1.5px] outline-offset-1 outline-amber-dash outline-dashed box-decoration-clone',
	'kept-category': 'border-b-[1.5px] border-dashed border-faint',
	'kept-here': 'border-b-[1.5px] border-dotted border-faint',
	'ai-kept': 'border-b-[1.5px] border-dotted border-ai-line',
	restored: 'bg-[linear-gradient(transparent_58%,var(--tint-5)_58%)] px-px',
	'restored-other': 'bg-[linear-gradient(transparent_58%,var(--green-2)_58%)] px-px',
	'unknown-code':
		'rounded-xs bg-amber-2 px-1.5 py-0.5 font-mono text-[0.74em] whitespace-nowrap text-amber-ink outline-[1.5px] outline-amber-dash outline-dashed',
	'pattern-hit': 'rounded-xs bg-tint-3 px-1.25 font-sans text-[0.86em] font-semibold text-tint-ink',
	selection: 'bg-blue-2 text-doc-ink'
};

/** Additional style for operable segments: ring on hover, stronger on open menu. */
export const OPERABLE_STYLE =
	'cursor-pointer hover:shadow-[0_0_0_1.5px_var(--tint-line)] focus-visible:outline-2 focus-visible:outline-offset-1';

export const ACTIVE_STYLE =
	'shadow-[0_0_0_2px_var(--accent)] hover:shadow-[0_0_0_2px_var(--accent)]';

/** Superscript number shown after an open question. */
export const QUESTION_NUMBER_STYLE =
	'ml-1 inline-flex h-3.75 min-w-3.75 items-center justify-center rounded-full bg-amber-strong px-0.75 align-super font-sans text-[9px] leading-none font-bold text-on-amber-strong ring-[1.5px] ring-paper';

/** Kinds that appear without highlight when “Mark used entries” is disabled. */
export const MARKIERBAR = new Set<SegmentKind>(['restored', 'restored-other']);

/**
 * Same kinds as overlay areas on a page view. Paper stays light in dark mode,
 * so colors stay vivid (`kontrast-paare.ts`, area `paper`). Stroke widths are
 * in screen pixels (`vector-effect="non-scaling-stroke"`).
 *
 * `area`: rectangle over the word; `stroke`: underline instead of area for entries that remain.
 */
export const PAGES_STYLE: Partial<Record<SegmentKind, { area?: string; strich?: string }>> = {
	code: { area: 'fill-accent/20 stroke-accent' },
	placeholder: { area: 'fill-accent/20 stroke-accent' },
	'masked-original': { area: 'fill-accent/20 stroke-accent' },
	question: { area: 'fill-amber-strong/35 stroke-on-amber-strong [stroke-dasharray:3_2]' },
	'kept-category': { strich: 'stroke-bar/70 [stroke-dasharray:4_3]' },
	'kept-here': { strich: 'stroke-bar/70 [stroke-dasharray:1.5_2]' },
	'ai-kept': { strich: 'stroke-accent [stroke-dasharray:1.5_2]' },
	selection: { area: 'fill-accent/25' }
};
