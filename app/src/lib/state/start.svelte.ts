import { Masking } from './masking.svelte.js';
import { samePreset, type Preset } from '#lib/masking/rules.js';

/**
 * State of the start page (Board “1 · New”), persisted across navigation.
 *
 * `text` holds the text-mode content. It stays in memory so that viewing history
 * doesn't clear it and never goes to `localStorage`; it contains exactly the
 * data to protect.
 *
 * `masking` applies to all new items (documents and text). It follows the saved
 * default whenever that changes (settings, template, “Apply as default”, a later
 * first load); anything changed here reverts to the default (“Applies to all
 * new”).
 */
class Start {
	kind = $state<'documents' | 'text'>('documents');
	text = $state('');
	/**
	 * The entry “Text · kopiert” from the last copy operation. Only after this
	 * the codes appear in the table, and “Answer restore” navigates there.
	 */
	copied = $state<string | null>(null);
	readonly masking = new Masking();
	presetLoaded = $state(false);
	/**
	 * What the menu bar or dock request from the start page: a selection dialog
	 * or fetching documents from “Open with”. The start page handles it when
	 * open and then clears the request.
	 */
	angefordert = $state<'files' | 'folder' | 'opened' | null>(null);

	/**
	 * Adopt the saved default for every new state, not just on first load.
	 * It doesn't matter who loads the settings first (layout, setup, template):
	 * new documents always receive the saved default.
	 */
	acceptPreset(preset: Preset | null | undefined): void {
		if (!preset) return;
		if (!this.presetLoaded || !samePreset(this.masking.asPreset(), preset)) {
			this.masking.accept(preset);
		}
		this.presetLoaded = true;
	}
}

export const start = new Start();
