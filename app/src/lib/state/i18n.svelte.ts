import { browser } from '$app/env';
import { translate, type MessageKey, type Locale, type Values } from '#lib/i18n/translate.js';

/**
 * Application language.
 *
 * `t()` reads `language` and is reactive in templates: changing the language
 * re-renders all text. `<html lang>` updates accordingly – screen readers and
 * hyphenation follow. The choice is saved via the settings API; until then
 * German is used.
 */
class I18n {
	language = $state<Locale>('de');

	set(language: Locale): void {
		this.language = language;
		if (browser) document.documentElement.lang = language;
	}

	t = (key: MessageKey, values?: Values): string => translate(this.language, key, values);
}

export const i18n = new I18n();

/** Short alias for components: `t('allgemein.schliessen')`. */
export const t = i18n.t;
