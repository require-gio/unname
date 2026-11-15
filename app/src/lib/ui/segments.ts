/**
 * Typed segments for all text views.
 *
 * A renderer (`#lib/ui/SegmentText.svelte`) and a style map
 * (`#lib/ui/segment-styles.ts`) for review, text mode, restore and the pattern test.
 * The segment lists build the views themselves
 * (`#lib/review/segmente.ts`, `#lib/textmodus/ausgabe.ts`,
 * `#lib/restore/restore.ts`) from the sidecar output.
 *
 * All offsets are UTF-16 code units, as defined by the sidecar API.
 */

export const SEGMENT_KINDS = [
	'plain',
	'code',
	'placeholder',
	'masked-original',
	'question',
	'kept-category',
	'kept-here',
	'ai-kept',
	'restored',
	'restored-other',
	'unknown-code',
	'pattern-hit',
	'selection'
] as const;

export type SegmentKind = (typeof SEGMENT_KINDS)[number];

export interface Segment {
	kind: SegmentKind;
	text: string;
	/** Finding ID – present for any segment that maps to a location. */
	id?: string;
	/** Entity type, used for label and `aria-label`. */
	type?: string;
	/** Original value, for “Original: …” – only in the window. */
	original?: string;
	/** Sequential number of an open question. */
	number?: number;
	/** Opens a menu on click (code menu, “Remask” …). */
	operable?: boolean;
	/**
	 * Source range in the original text (UTF-16). Used to map a window
	 * highlight back to the original (review).
	 */
	source?: { start: number; end: number };
	/** Category from the catalog – for menu titles and “Keep all …”. */
	category?: string;
	/** Added manually or corrected within limits. */
	manual?: boolean;
	/**
	 * The hand entry this location belongs to. A manually marked value
	 * applies to all its occurrences; their segments keep their own unique
	 * `id` (key for rendering) and reference this single marking that
	 * “Do not mask” restores.
	 */
	hand?: string;
}

/** Reassembles segments into text – used in tests and for copying. */
export function segmentText(segments: readonly Segment[]): string {
	return segments.map((segment) => segment.text).join('');
}
