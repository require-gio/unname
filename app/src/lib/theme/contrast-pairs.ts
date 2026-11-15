/**
 * Which tokens may appear as text on which surfaces (spec §11).
 *
 * This list is the commitment: any pair listed here meets the required
 * contrast in both light and dark modes – `contrast.spec.ts` verifies it.
 * Add new combinations here when they are used in a component.
 *
 * - Text: 4.5:1 (WCAG 1.4.3)
 * - Graphics and controls: 3:1 (WCAG 1.4.11)
 *
 * `faint` is intentionally not a text token: on `bg` the mockup achieved only
 * 2.9:1, so it remains reserved for graphics and disabled elements.
 */

export interface ContrastPaar {
	front: string;
	area: string;
	/** Covering backing if `area` is semi-transparent. */
	backing?: string;
	atLeast: 4.5 | 3;
	/** Checked only in one mode because the shape is rendered differently there. */
	only?: 'light' | 'dark';
}

/** Neutral surfaces where regular text is placed. */
export const NEUTRAL_AREAS = [
	'bg',
	'side',
	'canvas',
	'surface',
	'surface-2',
	'surface-3',
	'field',
	'chip'
] as const;

/** Text tokens for neutral surfaces. */
export const TEXT_TOKENS = [
	'ink',
	'ink-2',
	'muted',
	'subtle',
	'doc-ink',
	'link',
	'accent-fg'
] as const;

function text(front: string, ...areas: string[]): ContrastPaar[] {
	return areas.map((area) => ({ front, area, atLeast: 4.5 }));
}

function grafik(front: string, ...areas: string[]): ContrastPaar[] {
	return areas.map((area) => ({ front, area, atLeast: 3 }));
}

export const CONTRAST_PAIRS: readonly ContrastPaar[] = [
	// Regular text on all neutral surfaces.
	...TEXT_TOKENS.flatMap((front) => text(front, ...NEUTRAL_AREAS)),
	...TEXT_TOKENS.map((front) => ({
		front,
		area: 'glass',
		backing: 'canvas',
		atLeast: 4.5 as const
	})),
	...text('ink', 'chip-strong'),
	...text('muted', 'chip-strong'),

	// Masked (Indigo): codes, placeholders, original background, AI box.
	...text('tint-ink', 'tint-1', 'tint-2', 'tint-3', 'tint-4', 'tint-5'),
	...text('tint-ink-strong', 'tint-1', 'tint-2', 'tint-3', 'tint-4', 'tint-5'),
	...text('link', 'tint-1', 'tint-2'),
	...text('accent-fg', 'tint-1', 'tint-2'),
	...text('muted', 'tint-1'),

	// Decision (Amber): question cards, notices, question numbers.
	...text('amber-ink', 'amber-1', 'amber-2', 'surface'),
	...text('amber-ink-2', 'amber-1', 'surface', 'surface-2', 'bg'),
	...text('on-amber-strong', 'amber-strong'),

	// Status and file types.
	...text('green-ink', 'green-1', 'surface', 'surface-2'),
	...text('red-ink', 'red-0', 'red-1', 'surface'),
	...text('blue-ink', 'blue-1'),
	...text('doc-ink', 'blue-2', 'green-2', 'tint-5'),

	// Filled surfaces.
	...text('on-ink', 'ink'),
	...text('on-accent', 'accent'),
	...text('brand-ink', 'brand'),

	// Dark bar.
	...text('bar-ink', 'bar'),
	...text('bar-muted', 'bar'),
	...text('bar-accent', 'bar'),
	...text('bar-on-ink', 'bar-on'),
	{ front: 'bar-ink', area: 'bar-raised', backing: 'bar', atLeast: 4.5 },

	// Graphics: separators, disabled elements, switches and bars.
	...grafik('faint', ...NEUTRAL_AREAS.filter((area) => area !== 'chip')),
	...grafik('accent', 'surface', 'bg'),

	// Highlights on the page image: paper stays light.
	...grafik('accent', 'paper'),
	...grafik('on-amber-strong', 'paper'),
	...grafik('bar', 'paper'),
	...text('on-amber-strong', 'paper'),

	// Logo: light mode uses the tile shape, dark mode uses the pixels —
	// the tile itself has minimal contrast in dark mode.
	...grafik('brand', 'side', 'bg', 'surface').map((paar) => ({ ...paar, only: 'light' as const })),
	...grafik('brand-ink', 'side', 'bg', 'surface').map((paar) => ({
		...paar,
		only: 'dark' as const
	}))
];
