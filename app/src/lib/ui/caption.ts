import { typeTitle } from '#lib/masking/types.js';
import { i18n, t } from '#lib/state/i18n.svelte.js';
import type { Segment } from './segments.js';

/**
 * Caption for a segment used by screen readers (e.g. "Name: Hofer", "Name, masked as …").
 * The caption is the same for the text and the page view.
 */
export function segmentCaption(segment: Segment): string {
	const kind = segment.type ? typeTitle(segment.type, i18n.language) : '';
	if (segment.original !== undefined) {
		return t('code.caption', { kind, original: segment.original || t('code.removed') });
	}
	return kind ? `${kind}: ${segment.text}` : segment.text;
}
