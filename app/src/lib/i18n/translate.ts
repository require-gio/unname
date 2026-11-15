import { de, type Message, type PluralForm, type MessageKey } from './de.js';
import { en } from './en.js';

/**
 * Simple translation without external library.
 *
 * Typed dictionary per locale, placeholders `{name}` and plural forms via
 * `Intl.PluralRules`. Small enough to read and avoids a build plugin that could
 * break on kit updates.
 *
 * App UI language ≠ document language: this function only translates the UI
 * and the review report.
 */

export const LOCALES = ['de', 'en'] as const;
export type Locale = (typeof LOCALES)[number];
export type { MessageKey } from './de.js';

export type Values = Readonly<Record<string, string | number>>;

/** Pass `t()` as a parameter to keep domain functions pure and testable. */
export type Translator = (key: MessageKey, values?: Values) => string;

const DICTIONARIES: Record<Locale, Partial<Record<MessageKey, Message>>> = { de, en };

const LOCALE: Record<Locale, string> = { de: 'de-DE', en: 'en-GB' };

const pluralRules = new Map<Locale, Intl.PluralRules>();
const numberFormats = new Map<Locale, Intl.NumberFormat>();

function rules(language: Locale): Intl.PluralRules {
	let rule = pluralRules.get(language);
	if (!rule) {
		rule = new Intl.PluralRules(LOCALE[language]);
		pluralRules.set(language, rule);
	}
	return rule;
}

function numberFormat(language: Locale): Intl.NumberFormat {
	let format = numberFormats.get(language);
	if (!format) {
		format = new Intl.NumberFormat(LOCALE[language]);
		numberFormats.set(language, format);
	}
	return format;
}

export function isLocale(value: unknown): value is Locale {
	return typeof value === 'string' && (LOCALES as readonly string[]).includes(value);
}

function pluralForm(language: Locale, entry: PluralForm, values: Values | undefined): string {
	const n = values?.n;
	if (typeof n !== 'number') return entry.other;
	if (n === 0 && entry.zero !== undefined) return entry.zero;
	const category = rules(language).select(n);
	return category === 'one' ? entry.one : entry.other;
}

/** Get text for a key; fall back to German if missing in the requested language. */
export function translate(language: Locale, key: MessageKey, values?: Values): string {
	const entry: Message = DICTIONARIES[language][key] ?? de[key];
	const template = typeof entry === 'string' ? entry : pluralForm(language, entry, values);
	if (!values) return template;
	return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (placeholder, name: string) => {
		const value = values[name];
		if (value === undefined) return placeholder;
		return typeof value === 'number' ? numberFormat(language).format(value) : value;
	});
}

/** Extract placeholders from an entry for completeness testing. */
export function placeholder(entry: Message): string[] {
	const texts = typeof entry === 'string' ? [entry] : Object.values(entry);
	const names = new Set<string>();
	for (const text of texts) {
		for (const match of text.matchAll(/\{([a-zA-Z0-9_]+)\}/g)) names.add(match[1]);
	}
	return [...names].sort();
}
