import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { composite, contrastRatio, parseColor } from './color.js';
import { CONTRAST_PAIRS, type ContrastPaar } from './contrast-pairs.js';
import { readTokens, tokenValue, type Mode } from './token-file.js';

/**
 * Verify contrast for all allowed text/background pairs in both modes
 * (spec §11).
 *
 * The test reads `tokens.css` directly so the values are defined in one place
 * and cannot diverge.
 */

const CSS = readFileSync(fileURLToPath(new URL('../styles/tokens.css', import.meta.url)), 'utf8');
const TOKENS = readTokens(CSS);

function ratio(mode: Mode, paar: ContrastPaar): number {
	const table = TOKENS[mode];
	const front = parseColor(tokenValue(table, paar.front));
	let area = parseColor(tokenValue(table, paar.area));
	if (paar.backing) area = composite(area, parseColor(tokenValue(table, paar.backing)));
	return contrastRatio(front, area);
}

const MODI: Mode[] = ['light', 'dark'];

describe.each(MODI)('Tokens (%s)', (mode) => {
	const pairs = CONTRAST_PAIRS.filter((paar) => !paar.only || paar.only === mode);

	it.each(pairs.map((paar) => [`${paar.front} auf ${paar.area}`, paar] as const))(
		'%s',
		(_, paar) => {
			expect(ratio(mode, paar)).toBeGreaterThanOrEqual(paar.atLeast);
		}
	);
});

describe('tokens.css', () => {
	it('defines every token in both modes without cycles', () => {
		for (const mode of MODI) {
			for (const name of TOKENS[mode].keys()) {
				expect(() => tokenValue(TOKENS[mode], name), `${mode}: --${name}`).not.toThrow();
			}
		}
	});

	it('overwrites every color used for text or background in dark mode', () => {
		// A missing token would appear dark with a light value—unreadable on a dark
		// background, and the pair test would only catch it if the pair is listed.
		const dark = new Set(
			[...CSS.matchAll(/\.dark\s*\{([^}]*)\}/g)].flatMap((block) =>
				[...block[1].matchAll(/--([a-z0-9-]+)\s*:/gi)].map((match) => match[1])
			)
		);
		const lightDirect = [...TOKENS.light.entries()]
			.filter(([, value]) => !value.startsWith('var('))
			.map(([name]) => name)
			.filter((name) => !name.startsWith('entity-') && name !== 'radius');
		const fehlend = lightDirect.filter((name) => !dark.has(name));
		expect(fehlend).toEqual([]);
	});

	it('does not exceed the 72 color variables of the mockup', () => {
		// The nearly identical mockup variants are merged; the hard-coded hex values
		// (e.g., bar, brand, question number, KI line …) are now tokens. A new token
		// replaces an old one or has a reason documented in tokens.css.
		const colors = [...TOKENS.light.entries()].filter(
			([name, value]) =>
				!name.startsWith('shadow-') && !value.startsWith('var(') && name !== 'radius'
		);
		expect(colors.length).toBeLessThanOrEqual(72);
	});
});
