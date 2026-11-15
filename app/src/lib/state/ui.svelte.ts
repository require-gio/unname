import { browser } from '$app/env';
import { MediaQuery } from 'svelte/reactivity';

/**
 * UI convenience: sidebar collapsed/expanded, default appearance for new entries
 * (page, pageFaithful or text). Existing entry state is stored encrypted in the app state.
 * Settings are kept in `localStorage` and are only a convenience – if storage is unavailable
 * (private window, blocked site data) the defaults apply. This does not affect documents.
 *
 * Below 1040 px the sidebar collapses so the document has space. It can be expanded while the
 * window remains narrow, but this does not overwrite the stored preference.
 *
 * In review mode the sidebar auto-collapses to give the document space. It can be expanded
 * until leaving review, without overwriting the stored preference.
 */
const SIDEBAR = 'unname.sidebar';
const APPEARANCE = 'unname.appearance';

/**
 * How review shows a document: as a page layout or as linear text.
 * “page” is used only where a layout map exists; otherwise the view defaults to text.
 * “pageFaithful” (spec §S) applies only to Word with the Office engine; otherwise it behaves like “page”.
 */
export type Appearance = 'page' | 'pageFaithful' | 'text';

function appearanceFrom(value: string | null): Appearance {
	return value === 'text' || value === 'pageFaithful' ? value : 'page';
}

function read(key: string): string | null {
	if (!browser) return null;
	try {
		return localStorage.getItem(key);
	} catch {
		return null;
	}
}

function write(key: string, value: string): void {
	if (!browser) return;
	try {
		localStorage.setItem(key, value);
	} catch {
		// Without storage the choice lasts only until reload.
	}
}

class Ui {
	sidebarOpen = $state(read(SIDEBAR) !== 'closed');
	appearance = $state<Appearance>(appearanceFrom(read(APPEARANCE)));
	#narrow = new MediaQuery('max-width: 1039px');
	#narrowOpen = $state(false);
	#focus = $state(false);
	#focusOpen = $state(false);

	/** Window is too narrow for sidebar and document side-by-side. */
	get narrow(): boolean {
		return this.#narrow.current;
	}

	get sidebarVisible(): boolean {
		if (this.narrow) return this.#narrowOpen;
		return this.#focus ? this.#focusOpen : this.sidebarOpen;
	}

	/** Review starts: collapse sidebar until review is exited. */
	focusBetreten(): () => void {
		this.#focus = true;
		this.#focusOpen = false;
		this.#narrowOpen = false;
		return () => {
			this.#focus = false;
		};
	}

	setAppearance(value: Appearance): void {
		this.appearance = value;
		write(APPEARANCE, value);
	}

	toggleSidebar(): void {
		if (this.narrow) {
			this.#narrowOpen = !this.#narrowOpen;
			return;
		}
		if (this.#focus) {
			this.#focusOpen = !this.#focusOpen;
			return;
		}
		this.sidebarOpen = !this.sidebarOpen;
		write(SIDEBAR, this.sidebarOpen ? 'open' : 'closed');
	}
}

export const ui = new Ui();
