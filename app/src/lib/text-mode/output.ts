import type { MessageKey, Translator } from '#lib/i18n/translate.js';
import type { Method } from '#lib/masking/rules.js';
import type { Segment } from '#lib/ui/segments.js';
import type { ApplyResult } from '#lib/sidecar/api.gen.js';

/**
 * Text mode output (Board “1 · New”, toggle “Text”).
 *
 * Shows exactly the sidecar response—no extra calculation in the frontend.
 * From this we only create segments for `SegmentText`, the header and the count line.
 */

/** Modes whose replacement appears as code; everything else as placeholder. */
const CODE_MODI = new Set(['token_deterministic', 'encrypt', 'hash']);

/**
 * Segments derived from output offsets of replaced places.
 *
 * A place that does not fit the output text or overlaps the previous one stays
 * plain text: better unmarked than incorrectly marked. The text itself remains
 * intact—the segments always reassemble to exactly `result.text`. Removed places
 * (length 0) produce no segment.
 */
export function outputSegments(result: ApplyResult, original: string): Segment[] {
	const places = [...result.applied_findings].sort((a, b) => a.output_start - b.output_start);
	const segments: Segment[] = [];
	let cursor = 0;
	for (const place of places) {
		const { output_start: start, output_end: end } = place;
		if (start < cursor || end < start || end > result.text.length) continue;
		if (start > cursor) segments.push({ kind: 'plain', text: result.text.slice(cursor, start) });
		if (end > start) {
			segments.push({
				kind: CODE_MODI.has(place.mode) ? 'code' : 'placeholder',
				text: result.text.slice(start, end),
				id: place.finding_id,
				type: place.entity_type,
				original: original.slice(place.original_start, place.original_end)
			});
		}
		cursor = end;
	}
	if (cursor < result.text.length)
		segments.push({ kind: 'plain', text: result.text.slice(cursor) });
	return segments;
}

/**
 * Header above the output: the label the sidecar assigns to the result;
 * before the first result the selected method.
 */
export function outputTitle(method: Method, result?: ApplyResult | null): MessageKey {
	const label = result?.labels.label;
	if (label === 'anonymous') return 'label.level3';
	const kind = label ? (label === 'reversible' ? 'reversible' : 'irreversible') : method;
	return kind === 'reversible' ? 'text.output.reversible' : 'text.output.irreversible';
}

/** "4 masked places · no questions". */
export function outputCount(result: ApplyResult, t: Translator): string {
	const places = t('masking.places', { n: result.applied_findings.length });
	const inDoubt = result.labels.masked_without_decision;
	return `${places} · ${
		inDoubt > 0 ? t('text.questions.masked', { n: inDoubt }) : t('text.questions.no')
	}`;
}
