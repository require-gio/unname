import type { MessageKey, Translator } from '#lib/i18n/translate.js';
import type { CategoryId } from '#lib/masking/catalog.js';
import type { Method, Style } from '#lib/masking/rules.js';
import type { Segment } from '#lib/ui/segments.js';
import type { PatternTestHit } from '#lib/sidecar/api.gen.js';

/**
 * Settings dialog domain – pure and tested; UI components in `#lib/features/einstellungen/`
 * only display it.
 */

/** The nine sections in navigation order. */
export const SECTIONS = [
	'general',
	'masking',
	'languages',
	'secondOpinion',
	'names',
	'history',
	'table',
	'models',
	'technical'
] as const;

export type Section = (typeof SECTIONS)[number];

export function istSection(value: unknown): value is Section {
	return typeof value === 'string' && (SECTIONS as readonly string[]).includes(value);
}

/* ---------------------------------------------------------------------------
 * Example rendering (masking)
 * --------------------------------------------------------------------------- */

interface ExamplePlace {
	category: CategoryId;
	text: string;
	code: string;
	alias: string;
	placeholder: Record<'de' | 'en', string>;
}

/** Example sentence from the board; fabricated like all mockup names. */
const EXAMPLE: readonly (string | ExamplePlace)[] = [
	'Frau ',
	{
		category: 'names',
		text: 'Sabine Hofer',
		code: '<<PERSON_ba903855>>',
		alias: 'Petra Lang',
		placeholder: { de: '[Name]', en: '[Name]' }
	},
	' aus ',
	{
		category: 'locations',
		text: 'Wien',
		code: '<<LOCATION_2d1e7f40>>',
		alias: 'Graz',
		placeholder: { de: '[Ort]', en: '[Place]' }
	},
	', Kundin von ',
	{
		category: 'organizations',
		text: 'Holzbau Gruber',
		code: '<<ORGANIZATION_c93a0b15>>',
		alias: 'Tischlerei Lehner',
		placeholder: { de: '[Firma]', en: '[Company]' }
	},
	', hat am ',
	{
		category: 'dates',
		text: '3. April 2026',
		code: '<<DATE_TIME_5f7e2a90>>',
		alias: '12. Mai 2026',
		placeholder: { de: '[Datum]', en: '[Date]' }
	},
	' bezahlt.'
];

export const EXAMPLE_ORIGINAL = EXAMPLE.map((part) =>
	typeof part === 'string' ? part : part.text
).join('');

/**
 * Example sentence as rendered by the selected masking. Unselected categories stay;
 * “remove” omits the segment.
 */
export function exampleSegments(
	selection: { method: Method; style: Style; categories: ReadonlySet<CategoryId> },
	language: 'de' | 'en' = 'de'
): Segment[] {
	const segments: Segment[] = [];
	const plain = (text: string) => {
		const last = segments.at(-1);
		if (last?.kind === 'plain') last.text += text;
		else segments.push({ kind: 'plain', text });
	};
	for (const part of EXAMPLE) {
		if (typeof part === 'string' || !selection.categories.has(part.category)) {
			plain(typeof part === 'string' ? part : part.text);
			continue;
		}
		switch (selection.style) {
			case 'codes':
				segments.push({ kind: 'code', text: part.code, original: part.text });
				break;
			case 'surrogates':
				segments.push({ kind: 'placeholder', text: part.alias, original: part.text });
				break;
			case 'placeholder':
				segments.push({ kind: 'placeholder', text: part.placeholder[language] });
				break;
			case 'mask':
				segments.push({ kind: 'placeholder', text: '•'.repeat(part.text.length) });
				break;
			case 'remove':
				break;
		}
	}
	return segments;
}

/* ---------------------------------------------------------------------------
 * Try out (names & terms)
 * --------------------------------------------------------------------------- */

/**
 * Test sentence with sidecar matches as placeholder chips.
 *
 * Offsets are UTF-16 like JavaScript strings. A match that does not fit or
 * overlaps the previous one stays as plain text – better unmarked than wrong.
 */
export function testSegments(text: string, match: readonly PatternTestHit[]): Segment[] {
	const sorted = [...match].sort((a, b) => a.start - b.start);
	const segments: Segment[] = [];
	let cursor = 0;
	for (const hit of sorted) {
		if (hit.start < cursor || hit.end <= hit.start || hit.end > text.length) continue;
		if (hit.start > cursor) segments.push({ kind: 'plain', text: text.slice(cursor, hit.start) });
		segments.push({
			kind: 'pattern-hit',
			text: hit.label,
			type: hit.entity_type,
			original: text.slice(hit.start, hit.end)
		});
		cursor = hit.end;
	}
	if (cursor < text.length) segments.push({ kind: 'plain', text: text.slice(cursor) });
	return segments;
}

/* ---------------------------------------------------------------------------
 * Name lists: “State today, 03:00” · “State 9 days ago”
 * --------------------------------------------------------------------------- */

function dayGap(by: Date, until: Date): number {
	const mitternacht = (date: Date) =>
		new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
	// Rounded: days with DST change have 23 or 25 hours.
	return Math.round((mitternacht(until) - mitternacht(by)) / 86_400_000);
}

export function listenState(
	iso: string,
	now: Date,
	t: Translator,
	locale: string = 'de-DE'
): string {
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return '';
	const days = dayGap(date, now);
	const timeOfDay = new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(
		date
	);
	if (days <= 0) return t('names.state.today', { time: timeOfDay });
	if (days === 1) return t('names.state.yesterday', { time: timeOfDay });
	return t('names.state.days', { n: days });
}

/* ---------------------------------------------------------------------------
 * Error codes for phase H → sentences
 * --------------------------------------------------------------------------- */

const ERROR: Record<string, MessageKey> = {
	password_too_short: 'settings.error.passwordShort',
	password_too_long: 'settings.error.passwordLong',
	wrong_password: 'settings.error.passwordWrong',
	invalid_backup: 'settings.error.backupInvalid',
	list_format: 'settings.error.listFormat',
	list_empty: 'settings.error.listEmpty',
	list_unsupported: 'settings.error.listKind',
	list_encoding: 'settings.error.encoding',
	list_too_large: 'settings.error.tooLarge',
	too_large: 'settings.error.tooLarge',
	file_too_large: 'settings.error.tooLarge',
	too_many_lists: 'settings.error.tooMany',
	too_many_terms: 'settings.error.tooMany',
	too_many_patterns: 'settings.error.tooMany',
	too_many_templates: 'settings.error.tooMany',
	encoding: 'settings.error.encoding',
	unreadable: 'settings.error.unreadable',
	write: 'settings.error.write',
	term_too_short: 'settings.error.termShort',
	pattern_invalid: 'settings.error.patternInvalid',
	pattern_empty: 'settings.error.patternEmpty',
	pattern_slow: 'settings.error.patternSlow',
	pattern_broad: 'settings.error.patternWide',
	template_invalid: 'settings.error.templateInvalid',
	template_version: 'settings.error.templateVersion',
	template_example: 'settings.error.templateExample'
};

/** Sentence for UI; unknown means "currently not possible". */
export function errorSentence(code: string | undefined, t: Translator): string {
	return t((code && ERROR[code]) || 'settings.error.general');
}

/* ---------------------------------------------------------------------------
 * Diagnostics report (technical)
 * --------------------------------------------------------------------------- */

export interface DiagnosticsSource {
	status: string;
	restarts: number;
	version?: string;
	log?: number;
	python?: string;
	platform?: string;
	detection?: string | null;
	device?: string;
	pakete?: Readonly<Record<string, string | null>>;
	entries: readonly {
		timestamp: string;
		category: string;
		severity: string;
		source?: string;
		message: string;
	}[];
}

/**
 * Text for "Copy diagnostics report": only operational data – state,
 * versions, relapses. No document content or values (spec §10); the
 * log messages are already content-free.
 */
export function diagnosticsText(source: DiagnosticsSource, now: Date = new Date()): string {
	const lines = [
		'unname · Diagnosebericht',
		`Erstellt: ${now.toISOString()}`,
		'',
		`Lokale Verarbeitung: ${source.status}`,
		`Neustarts: ${source.restarts}`
	];
	if (source.version) {
		lines.push(`Sidecar: ${source.version} (Protokoll ${source.log ?? '?'})`);
	}
	if (source.python) lines.push(`Python: ${source.python} auf ${source.platform ?? '?'}`);
	if (source.detection !== undefined)
		lines.push(`Erkennung: ${source.detection ?? 'nicht geladen'}`);
	if (source.device) lines.push(`Gerät: ${source.device}`);
	const pakete = Object.entries(source.pakete ?? {}).filter(([, version]) => version);
	if (pakete.length > 0) {
		lines.push('', 'Pakete:');
		for (const [name, version] of pakete) lines.push(`  ${name} ${version}`);
	}
	lines.push('', `Protokoll (${source.entries.length}):`);
	for (const entry of source.entries) {
		const sourceText = entry.source === 'harness' ? 'Hülle' : 'Sidecar';
		lines.push(
			`  ${entry.timestamp} [${entry.severity}] ${entry.category} (${sourceText}): ${entry.message}`
		);
	}
	return lines.join('\n') + '\n';
}

/** Backup filename: `unname-tabelle-2026-10-24.unname-sicherung`. */
export function backupName(now: Date): string {
	const tag = [
		now.getFullYear(),
		String(now.getMonth() + 1).padStart(2, '0'),
		String(now.getDate()).padStart(2, '0')
	].join('-');
	return `unname-tabelle-${tag}.unname-sicherung`;
}

/** Sizes like "715 MB" (decimal, as shown by Finder and Explorer). */
export function size(bytes: number, locale: string = 'de-DE'): string {
	const format = (value: number, unit: string) =>
		`${new Intl.NumberFormat(locale, { maximumFractionDigits: value < 10 ? 1 : 0 }).format(value)} ${unit}`;
	if (bytes >= 1e9) return format(bytes / 1e9, 'GB');
	if (bytes >= 1e6) return format(bytes / 1e6, 'MB');
	if (bytes >= 1e3) return format(bytes / 1e3, 'kB');
	return format(bytes, 'B');
}
