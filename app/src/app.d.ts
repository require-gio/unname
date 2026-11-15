// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
import type { RejectedFile } from '#lib/sidecar/api.gen.js';
import type { Section } from '#lib/settings/dialog.js';
import type { Selection } from '#lib/sidecar/inputs.js';

declare global {
	namespace App {
		// interface Error {}
		// interface Locals {}
		// interface PageData {}
		interface PageState {
			/**
			 * Data that didn't make it into the entry when dropping.
			 * Only present for the first view after drop; cleared on reload.
			 */
			inputs?: {
				rejected: RejectedFile[];
				skipped: number;
				shortened: boolean;
			};
			/**
			 * The AI's answer from “restore AI answer” in the saved view.
			 * Stored only in memory, never in the URL.
			 */
			answer?: string;
			/**
			 * A file from the AI, stored or selected – used as the shell identifier,
			 * never a path.
			 */
			file?: Selection;
			/**
			 * The currently open section of the settings dialog (shallow routing):
			 * “Back” closes the dialog while keeping the address unchanged.
			 */
			settings?: Section;
		}
		// interface Platform {}
	}
}

export {};
