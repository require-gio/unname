import { untrack } from 'svelte';
import { browser } from '$app/env';
import type { MessageKey, Values } from '#lib/i18n/translate.js';

/**
 * On-demand help.
 *
 * Explanatory texts are no longer static on pages. Clicking “?” enables
 * `CoachMarks`; they show numbered hints at locations that have registered a
 * `{@attach coachmark(…)}` attachment. Texts come from the dictionary.
 *
 * On the very first launch the start page enables help once automatically.
 * This is stored in `localStorage` for convenience: without storage the hints
 * appear on every launch.
 */

const SHOWN = 'unname.help.shown';

function alreadyShown(): boolean {
	if (!browser) return true;
	try {
		return localStorage.getItem(SHOWN) !== null;
	} catch {
		return false;
	}
}

function remember(): void {
	try {
		localStorage.setItem(SHOWN, '1');
	} catch {
		// Without storage the “shown” flag only lasts until reload.
	}
}

export type CoachMarkPage = 'top' | 'bottom' | 'left' | 'right';

export interface CoachMarkEntry {
	id: number;
	number: number;
	key: MessageKey;
	values?: Values;
	target: HTMLElement;
	page: CoachMarkPage;
}

class Help {
	on = $state(false);
	marken = $state<CoachMarkEntry[]>([]);
	#nextId = 1;
	#firstStartReviewed = false;

	toggle(): void {
		this.on = !this.on;
	}

	ausschalten(): void {
		this.on = false;
	}

	/**
	 * On first launch enable help automatically. Returns `true` when help
	 * is turned on for this reason – the start page will turn it off again
	 * when leaving, rather than carrying it to the next view.
	 */
	firstStart(): boolean {
		if (this.#firstStartReviewed) return false;
		this.#firstStartReviewed = true;
		if (alreadyShown()) return false;
		remember();
		this.on = true;
		return true;
	}

	/**
	 * Register a hint; `abmelden` removes it.
	 *
	 * Registration happens from an attachment, i.e., inside an effect. If we
	 * read `marken` directly it would re-run on each registration – therefore
	 * we use `untrack`.
	 */
	register(entry: Omit<CoachMarkEntry, 'id'>): { id: number; abmelden: () => void } {
		const id = this.#nextId++;
		untrack(() => {
			this.marken = [...this.marken, { ...entry, id }].sort((a, b) => a.number - b.number);
		});
		return {
			id,
			abmelden: () =>
				untrack(() => {
					this.marken = this.marken.filter((mark) => mark.id !== id);
				})
		};
	}

	/** The DOM ID of the note targeted by `aria-describedby`. */
	notizId(id: number): string {
		return `coachmark-${id}`;
	}
}

export const help = new Help();
