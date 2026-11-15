/**
 * Fonts used in the Word island.
 *
 * Office documents typically use Calibri, Cambria, Arial, Times New Roman, or
 * Courier New. If any are missing, the browser falls back to an arbitrary
 * replacement font, causing line and page breaks to differ from Word. Therefore
 * for each font we prefer the installed version, otherwise a metrically
 * compatible bundled font (Carlito, Caladea, Arimo, Tinos, Cousine; OFL-1.1,
 * only `latin` and `latin-ext`). Characters outside those ranges fall back to
 * the installed font if it exists.
 *
 * Vite bundles the files (`font-src 'self'`); the island loads them via
 * `url()` relative to the app. License notice: THIRD_PARTY_NOTICES/fonts.
 */

const FILES = import.meta.glob<string>(
	'/node_modules/@fontsource/{carlito,caladea,arimo,tinos,cousine}/files/*-latin{,-ext}-{400,700}-{normal,italic}.woff2',
	{ query: '?url', import: 'default', eager: true, exhaustive: true }
);

interface Replacement {
	/** Font name in the document. */
	familie: string;
	/** Metrically compatible bundled font. */
	replacement: string;
	bundle: string;
}

export const REPLACEMENT_FONTS: readonly Replacement[] = [
	{ familie: 'Calibri', replacement: 'Carlito', bundle: 'carlito' },
	{ familie: 'Cambria', replacement: 'Caladea', bundle: 'caladea' },
	{ familie: 'Arial', replacement: 'Arimo', bundle: 'arimo' },
	{ familie: 'Times New Roman', replacement: 'Tinos', bundle: 'tinos' },
	{ familie: 'Courier New', replacement: 'Cousine', bundle: 'cousine' }
];

/** Unicode ranges copied unchanged from the Fontsource packages. */
const SECTIONS = {
	latin:
		'U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD',
	'latin-ext':
		'U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF'
} as const;

const CUTS = [
	{ weight: 400, style: 'normal', addition: [] },
	{ weight: 700, style: 'normal', addition: ['Bold'] },
	{ weight: 400, style: 'italic', addition: ['Italic'] },
	{ weight: 700, style: 'italic', addition: ['Bold', 'Italic'] }
] as const;

/** Names under which `local()` finds an installed font (full and PostScript name). */
function local(name: string, addition: readonly string[]): string {
	if (addition.length === 0) return `local('${name}')`;
	const full = `${name} ${addition.join(' ')}`;
	const postscript = `${name.replaceAll(' ', '')}-${addition.join('')}`;
	return `local('${full}'), local('${postscript}')`;
}

/**
 * `@font-face` rules for the island. For each weight/style we first emit a rule
 * using only the installed font (all characters), then for each subset we add
 * the installed font, the bundled replacement, and the font file. The browser
 * prefers later rules.
 */
export function fontRules(): string {
	const rules: string[] = [];
	for (const { familie, replacement, bundle } of REPLACEMENT_FONTS) {
		for (const { weight, style, addition } of CUTS) {
			const header = `font-family: '${familie}'; font-weight: ${weight}; font-style: ${style}; font-display: block;`;
			rules.push(`@font-face { ${header} src: ${local(familie, addition)}; }`);
			for (const [part, section] of Object.entries(SECTIONS)) {
				const file =
					FILES[
						`/node_modules/@fontsource/${bundle}/files/${bundle}-${part}-${weight}-${style}.woff2`
					];
				if (!file) continue;
				rules.push(
					`@font-face { ${header} src: ${local(familie, addition)}, ${local(replacement, addition)}, url('${file}') format('woff2'); unicode-range: ${section}; }`
				);
			}
		}
	}
	return rules.join('\n');
}

const GENERISCH = new Set([
	'serif',
	'sans-serif',
	'monospace',
	'cursive',
	'fantasy',
	'system-ui',
	'inherit',
	'initial'
]);

/**
 * Fonts used in the document that are missing on this device and have no
 * metrically compatible replacement (e.g., Aptos). The browser falls back to
 * another font, which can change line and page breaks.
 *
 * We check the first font family of each text element on a Canvas: a missing
 * font measures like its generic fallback.
 */
export function missingFonts(wurzel: HTMLElement): string[] {
	const viewport = wurzel.ownerDocument.defaultView;
	if (!viewport) return [];
	const familien = new Set<string>();
	for (const element of wurzel.querySelectorAll<HTMLElement>('span, p')) {
		if (!element.firstChild || element.firstChild.nodeType !== 3) continue;
		const first = viewport.getComputedStyle(element).fontFamily.split(',')[0];
		const name = first?.trim().replace(/^["']|["']$/g, '');
		if (name && !GENERISCH.has(name.toLowerCase())) familien.add(name);
	}
	const covered = new Set(REPLACEMENT_FONTS.map((entry) => entry.familie.toLowerCase()));
	return [...familien]
		.filter((name) => !covered.has(name.toLowerCase()) && !installed(name))
		.sort((a, b) => a.localeCompare(b));
}

let measuringArea: CanvasRenderingContext2D | null | undefined;

function installed(name: string): boolean {
	measuringArea ??= document.createElement('canvas').getContext('2d');
	if (!measuringArea) return true;
	const probe = 'mmmmmmmmmmlliWWQ@#0123456789';
	return ['monospace', 'serif', 'sans-serif'].some((generisch) => {
		measuringArea!.font = `72px ${generisch}`;
		const without = measuringArea!.measureText(probe).width;
		measuringArea!.font = `72px "${name.replaceAll('"', '')}", ${generisch}`;
		return measuringArea!.measureText(probe).width !== without;
	});
}
