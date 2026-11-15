/**
 * Color math for token contrast checks (spec §9.6, spec §11).
 *
 * No extra dependencies: the app should stay lightweight, and the conversion is short
 * enough to be readable and auditable in this repository.
 *
 * Reads the notations from `tokens.css`: `#rgb`, `#rrggbb`,
 * `#rrggbbaa`, `rgb(…)` with or without alpha and `oklch(…)`.
 */

export interface Oklch {
	l: number;
	c: number;
	h: number;
	/** Alpha 0…1. Must be 1 for contrast calculation. */
	alpha: number;
}

/** Gamma-encoded sRGB, channels 0…1 as displayed on screen. */
export interface Rgb {
	r: number;
	g: number;
	b: number;
	alpha: number;
}

const OKLCH_PATTERN =
	/oklch\(\s*([0-9.]+%?)\s+([0-9.]+%?)\s+([0-9.]+)(?:deg)?\s*(?:\/\s*([0-9.]+%?)\s*)?\)/i;

const RGB_PATTERN =
	/rgba?\(\s*([0-9.]+%?)\s*[\s,]\s*([0-9.]+%?)\s*[\s,]\s*([0-9.]+%?)\s*(?:[/,]\s*([0-9.]+%?)\s*)?\)/i;

const HEX_PATTERN = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;

function toNumber(raw: string, percentBase: number): number {
	return raw.endsWith('%') ? (parseFloat(raw) / 100) * percentBase : parseFloat(raw);
}

/** Parse an `oklch(…)` function value. Throws if the string does not match. */
export function parseOklch(value: string): Oklch {
	const match = OKLCH_PATTERN.exec(value.trim());
	if (!match) throw new Error(`Kein oklch()-Wert: ${value}`);
	return {
		l: toNumber(match[1], 1),
		c: toNumber(match[2], 0.4),
		h: parseFloat(match[3]),
		alpha: match[4] === undefined ? 1 : toNumber(match[4], 1)
	};
}

/** Parse `#rgb`, `#rgba`, `#rrggbb` or `#rrggbbaa`. */
export function parseHex(value: string): Rgb {
	const match = HEX_PATTERN.exec(value.trim());
	if (!match) throw new Error(`Kein Hex-Farbwert: ${value}`);
	let hex = match[1];
	if (hex.length <= 4) hex = [...hex].map((digit) => digit + digit).join('');
	const kanal = (index: number) => parseInt(hex.slice(index, index + 2), 16) / 255;
	return { r: kanal(0), g: kanal(2), b: kanal(4), alpha: hex.length === 8 ? kanal(6) : 1 };
}

/** Parse `rgb(r g b / a)` or `rgba(r, g, b, a)`. */
export function parseRgb(value: string): Rgb {
	const match = RGB_PATTERN.exec(value.trim());
	if (!match) throw new Error(`Kein rgb()-Wert: ${value}`);
	const kanal = (raw: string) => toNumber(raw, 255) / 255;
	return {
		r: kanal(match[1]),
		g: kanal(match[2]),
		b: kanal(match[3]),
		alpha: match[4] === undefined ? 1 : toNumber(match[4], 1)
	};
}

/**
 * OKLCH → linear sRGB. Values outside the sRGB gamut are clipped to [0,1],
 * matching what a screen actually shows.
 */
export function oklchToLinearSrgb({
	l,
	c,
	h
}: Pick<Oklch, 'l' | 'c' | 'h'>): [number, number, number] {
	const hRad = (h * Math.PI) / 180;
	const a = c * Math.cos(hRad);
	const b = c * Math.sin(hRad);

	const lms = [
		l + 0.3963377774 * a + 0.2158037573 * b,
		l - 0.1055613458 * a - 0.0638541728 * b,
		l - 0.0894841775 * a - 1.291485548 * b
	].map((v) => v ** 3) as [number, number, number];

	const [L, M, S] = lms;
	const rgb: [number, number, number] = [
		4.0767416621 * L - 3.3077115913 * M + 0.2309699292 * S,
		-1.2684380046 * L + 2.6097574011 * M - 0.3413193965 * S,
		-0.0041960863 * L - 0.7034186147 * M + 1.707614701 * S
	];

	return rgb.map((v) => Math.min(1, Math.max(0, v))) as [number, number, number];
}

function toLinear(kanal: number): number {
	return kanal <= 0.04045 ? kanal / 12.92 : ((kanal + 0.055) / 1.055) ** 2.4;
}

function toGamma(kanal: number): number {
	const value = kanal <= 0.0031308 ? kanal * 12.92 : 1.055 * kanal ** (1 / 2.4) - 0.055;
	return Math.min(1, Math.max(0, value));
}

/** Any supported notation → sRGB with alpha. */
export function parseColor(value: string): Rgb {
	const text = value.trim();
	if (text.startsWith('#')) return parseHex(text);
	if (/^rgba?\(/i.test(text)) return parseRgb(text);
	if (/^oklch\(/i.test(text)) {
		const oklch = parseOklch(text);
		const [r, g, b] = oklchToLinearSrgb(oklch).map(toGamma);
		return { r, g, b, alpha: oklch.alpha };
	}
	if (text === 'transparent') return { r: 0, g: 0, b: 0, alpha: 0 };
	throw new Error(`Unbekannte Farbschreibweise: ${value}`);
}

/**
 * Composite a semi-transparent color over an opaque one, as browsers do:
 * mix in gamma-encoded sRGB.
 */
export function composite(top: Rgb, bottom: Rgb): Rgb {
	if (bottom.alpha < 1) throw new Error('Die Unterlage muss deckend sein.');
	const mix = (a: number, b: number) => a * top.alpha + b * (1 - top.alpha);
	return {
		r: mix(top.r, bottom.r),
		g: mix(top.g, bottom.g),
		b: mix(top.b, bottom.b),
		alpha: 1
	};
}

/** Relative luminance per WCAG 2.1 from linear sRGB. */
export function relativeLuminance([r, g, b]: [number, number, number]): number {
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function leuchtdichte(color: Rgb): number {
	return relativeLuminance([toLinear(color.r), toLinear(color.g), toLinear(color.b)]);
}

function asRgb(color: string | Oklch | Rgb): Rgb {
	if (typeof color === 'string') return parseColor(color);
	if ('l' in color) {
		const [r, g, b] = oklchToLinearSrgb(color).map(toGamma);
		return { r, g, b, alpha: color.alpha };
	}
	return color;
}

/**
 * Contrast ratio per WCAG 2.1 (range 1–21). Required minimums:
 * 4.5:1 for text and 3:1 for graphics. Both colors must be opaque —
 * blend any semi-transparent layer with `composite` first.
 */
export function contrastRatio(a: string | Oklch | Rgb, b: string | Oklch | Rgb): number {
	const colorA = asRgb(a);
	const colorB = asRgb(b);
	if (colorA.alpha < 1 || colorB.alpha < 1) {
		throw new Error('Kontrast nur zwischen deckenden Farben; erst mit composite() mischen.');
	}
	const lumA = leuchtdichte(colorA);
	const lumB = leuchtdichte(colorB);
	const [hi, lo] = lumA >= lumB ? [lumA, lumB] : [lumB, lumA];
	return (hi + 0.05) / (lo + 0.05);
}
