import { goto } from '$app/navigation';
import { page } from '$app/state';
import { setMode } from 'mode-watcher';
import { istSection, type Section } from '#lib/settings/dialog.js';
import { isLocale } from '#lib/i18n/translate.js';
import { fetchSettings, updateSettings } from '#lib/sidecar/api-v2.js';
import type { SettingsResponse } from '#lib/sidecar/api.gen.js';
import type { SidecarConnection } from '#lib/sidecar/types.js';
import { i18n } from './i18n.svelte.js';

/**
 * The settings from `GET` / `PUT /settings` and the settings dialog.
 *
 * Optimistic: changes apply immediately in the window and are rolled back
 * if the sidecar rejects them (“changes apply immediately”). App language
 * and appearance follow the stored values once loaded.
 *
 * The dialog is tied to history (shallow routing): opening creates an entry,
 * “Back” closes it; the address bar does not reflect this.
 */
type Part = {
	[Section in keyof SettingsResponse]?: Partial<NonNullable<SettingsResponse[Section]>>;
};

class Settings {
	values = $state<SettingsResponse | null>(null);
	/** The dialog was opened here; “Close” will pop it from history. */
	#gepusht = false;

	async load(connection: SidecarConnection): Promise<SettingsResponse | null> {
		try {
			this.values = await fetchSettings(connection);
			this.#apply(this.values);
		} catch {
			// Without a response keep the previous values—or defaults.
		}
		return this.values;
	}

	/** Apply only the specified sections and fields. */
	async change(connection: SidecarConnection, part: Part): Promise<void> {
		const before = this.values;
		if (before) this.values = merge(before, part);
		this.#apply(this.values);
		try {
			this.values = await updateSettings(connection, part as SettingsResponse);
			this.#apply(this.values);
		} catch (error) {
			this.values = before;
			this.#apply(before);
			throw error;
		}
	}

	/** A sidecar response that carries the settings (template selected). */
	set(values: SettingsResponse): void {
		this.values = values;
		this.#apply(values);
	}

	#apply(values: SettingsResponse | null): void {
		const language = values?.general?.language;
		if (isLocale(language) && language !== i18n.language) i18n.set(language);
		const appearance = values?.general?.theme;
		if (appearance) setMode(appearance);
	}

	/**
	 * Days until automatic history deletion; `null` means never.
	 * While nothing is loaded, the sidecar default (30 days) applies.
	 */
	get historyRetention(): number | null {
		const retention = this.values?.history?.retention_days;
		return retention === undefined ? 30 : retention;
	}

	// -- Dialog --------------------------------------------------------------

	/** The open section, or `null` if the dialog is closed. */
	get section(): Section | null {
		const section = page.state.settings;
		return istSection(section) ? section : null;
	}

	open(section: Section = 'general'): void {
		if (this.section) {
			this.switch(section);
			return;
		}
		this.#gepusht = true;
		void goto(page.url.href, { shallow: true, state: { ...page.state, settings: section } });
	}

	/** Switch section without adding a new history entry. */
	switch(section: Section): void {
		void goto(page.url.href, {
			shallow: true,
			replace: true,
			state: { ...page.state, settings: section }
		});
	}

	/**
	 * The content behind the dialog is cleared (history emptied): go to the start page,
	 * keep the dialog open. “Back” would otherwise lead to a deleted entry.
	 */
	toTheStartPage(start: string): void {
		const section = this.section;
		this.#gepusht = false;
		void goto(start, { replace: true, state: section ? { settings: section } : {} });
	}

	close(): void {
		if (!this.section) return;
		if (this.#gepusht) {
			this.#gepusht = false;
			history.back();
			return;
		}
		// After a reload with `persistState` there would be no prior entry.
		const rest = { ...page.state };
		delete rest.settings;
		void goto(page.url.href, { shallow: true, replace: true, state: rest });
	}
}

function merge(before: SettingsResponse, part: Part): SettingsResponse {
	const fresh: Record<string, unknown> = { ...before };
	for (const [section, values] of Object.entries(part)) {
		const alt = (before as Record<string, unknown>)[section];
		fresh[section] =
			typeof alt === 'object' && alt !== null && typeof values === 'object'
				? { ...alt, ...values }
				: values;
	}
	return fresh as SettingsResponse;
}

export const settings = new Settings();
