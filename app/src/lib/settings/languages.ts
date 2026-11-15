import type { MessageKey } from '#lib/i18n/translate.js';
import type { DocumentLanguageSettings, ModelComponent } from '#lib/sidecar/api.gen.js';
import type { ModelPart } from '#lib/sidecar/models.js';

/**
 * Settings › Document languages – pure and tested.
 *
 * Languages and countries are lists with at least one entry; the sidecar
 * validates the same (`DocumentLanguageSettings`). Order is fixed
 * (German before English, DE before AT): the first language is the default
 * for paragraphs whose language cannot be determined.
 */

export const DOCUMENT_LANGUAGES = ['de', 'en'] as const;
export type DocumentLanguage = (typeof DOCUMENT_LANGUAGES)[number];

/** Countries with dedicated recognizers; CH, UK, US follow. */
export const COUNTRIES = ['DE', 'AT'] as const;
export type Country = (typeof COUNTRIES)[number];
export const COUNTRIES_SEQUENCES = ['CH', 'UK', 'US'] as const;

type Languages = NonNullable<DocumentLanguageSettings['languages']>;
type Countries = NonNullable<DocumentLanguageSettings['countries']>;

/** Default state before the sidecar provides any data. */
export const LANGUAGE_PRESET: Required<DocumentLanguageSettings> = {
	languages: ['de', 'en'],
	per_paragraph: true,
	countries: ['DE', 'AT'],
	placeholders_in_document_language: true
};

function toggle<T extends string>(
	order: readonly T[],
	current: readonly T[],
	value: T
): T[] | null {
	const fresh = current.includes(value)
		? current.filter((item) => item !== value)
		: [...current, value];
	if (fresh.length === 0) return null;
	return order.filter((item) => fresh.includes(item));
}

/** Toggle language; `null` if it would be the last one. */
export function toggleLanguage(current: readonly DocumentLanguage[], language: DocumentLanguage) {
	return toggle(DOCUMENT_LANGUAGES, current, language) as Languages | null;
}

/** Toggle country; `null` if it would be the last one. */
export function toggleCountry(current: readonly Country[], country: Country) {
	return toggle(COUNTRIES, current, country) as Countries | null;
}

/** Current state of the language package on this device. */
export type PackageState =
	| { kind: 'running'; percent: number }
	| { kind: 'installed' }
	| { kind: 'bundled'; source: 'bundle' | 'local' }
	| { kind: 'missing'; started: boolean };

/**
 * State of a language package from the shell (`part`) and sidecar (`sidecar`).
 * The shell knows what it has loaded; the sidecar only knows the bundled
 * and development versions.
 */
export function packageState(
	part: Pick<ModelPart, 'state' | 'received' | 'download_size'> | undefined,
	sidecar: Pick<ModelComponent, 'state' | 'source'> | undefined
): PackageState {
	if (part && ['queued', 'downloading', 'verifying', 'installing'].includes(part.state)) {
		const percent =
			part.download_size > 0
				? Math.min(100, Math.floor((part.received / part.download_size) * 100))
				: 0;
		return { kind: 'running', percent };
	}
	if (part?.state === 'installed') return { kind: 'installed' };
	if (sidecar?.state === 'ready') {
		if (sidecar.source === 'download') return { kind: 'installed' };
		// `system` exists only for OCR (spec §U), never for languages.
		return { kind: 'bundled', source: sidecar.source === 'bundle' ? 'bundle' : 'local' };
	}
	return { kind: 'missing', started: part?.state === 'partial' };
}

/** Dictionary entry for a language package. */
export function packageText(language: DocumentLanguage): { name: MessageKey; text: MessageKey } {
	return language === 'de'
		? { name: 'settings.languages.package.de', text: 'settings.languages.de' }
		: { name: 'settings.languages.package.en', text: 'settings.languages.en' };
}
