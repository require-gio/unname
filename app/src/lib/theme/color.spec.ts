import { describe, expect, it } from 'vitest';
import { composite, contrastRatio, parseColor, parseHex, parseRgb } from './color.js';

/**
 * Core color math: calculations here that the browser doesn't expose.
 */

describe('Read color values', () => {
	it('parses hex in short and long form, with and without alpha', () => {
		expect(parseHex('#fff')).toEqual({ r: 1, g: 1, b: 1, alpha: 1 });
		expect(parseHex('#000000')).toEqual({ r: 0, g: 0, b: 0, alpha: 1 });
		expect(parseHex('#4338CA').b).toBeCloseTo(202 / 255);
		expect(parseHex('#00000080').alpha).toBeCloseTo(128 / 255);
	});

	it('parses rgb() in both syntaxes', () => {
		expect(parseRgb('rgb(255 0 0 / 0.5)')).toEqual({ r: 1, g: 0, b: 0, alpha: 0.5 });
		expect(parseRgb('rgba(0, 255, 0, 0.25)')).toEqual({ r: 0, g: 1, b: 0, alpha: 0.25 });
		expect(parseRgb('rgb(0 0 255)').alpha).toBe(1);
	});

	it('throws on unknown format instead of silently returning black', () => {
		expect(() => parseColor('hsl(0 0% 0%)')).toThrow();
		expect(() => parseHex('#12')).toThrow();
	});
});

describe('Contrast', () => {
	it('calculates WCAG threshold values', () => {
		expect(contrastRatio('#ffffff', '#000000')).toBeCloseTo(21, 5);
		expect(contrastRatio('#777777', '#ffffff')).toBeCloseTo(4.48, 2);
	});

	it('is symmetric and format-independent', () => {
		expect(contrastRatio('#4338ca', '#fff')).toBeCloseTo(contrastRatio('#ffffff', '#4338CA'), 10);
		expect(contrastRatio('oklch(1 0 0)', '#000')).toBeCloseTo(21, 3);
	});

	it('blends semi-transparent layers onto their background first', () => {
		const glass = parseColor('rgb(255 255 255 / 0.5)');
		expect(() => contrastRatio('#000', glass)).toThrow();
		const gemischt = composite(glass, parseColor('#000000'));
		expect(gemischt.r).toBeCloseTo(0.5);
		expect(gemischt.alpha).toBe(1);
	});
});
