import { PAGE_SIZE, type HistoryScope, type HistorySort } from '#lib/history/history.js';

/**
 * Session-only state of the history view.
 *
 * The search text lives here in memory only — never in the URL, local
 * storage, logs or a persisted index. Scope and sort are in the URL. When an
 * entry is opened from the list, the list context is remembered so that the
 * “Back to history” link restores query, scope, sort, rendered limit and
 * scroll.
 */
export interface HistoryOrigin {
	entryId: string;
	scope: HistoryScope;
	sort: HistorySort;
	limit: number;
	scroll: number;
}

class HistoryView {
	query = $state('');
	limit = $state(PAGE_SIZE);
	/** The entry opened from the list, with the list context to return to. */
	origin = $state<HistoryOrigin | null>(null);
	/** Set by the back link; the history page consumes it once to restore scroll. */
	#returning: HistoryOrigin | null = null;

	/** Opened from the list, waiting for the navigation to arrive. */
	#pending: HistoryOrigin | null = null;

	/** Remember the list context before opening an entry from it. */
	open(origin: HistoryOrigin): void {
		this.#pending = origin;
	}

	/** The origin applies to this entry (and its review/restore routes). */
	originFor(entryId: string | undefined): HistoryOrigin | null {
		return entryId && this.origin?.entryId === entryId ? this.origin : null;
	}

	/** “Back to history” was used: the history page restores this context. */
	markReturning(): void {
		this.#returning = this.origin;
	}

	/** Called by the history page on arrival; `null` means start at the top. */
	takeReturning(): HistoryOrigin | null {
		const value = this.#returning;
		this.#returning = null;
		return value;
	}

	/**
	 * After every navigation: an entry opened from the list gets its origin;
	 * moving between that entry's own routes keeps it; reaching the entry any
	 * other way (recents, a direct link) or leaving it forgets it. The history
	 * page itself keeps it so the back link can restore the list.
	 */
	track(
		from: { routeId: string | null; entryId?: string } | null,
		to: { routeId: string | null; entryId?: string }
	): void {
		const entryOf = (place: { routeId: string | null; entryId?: string } | null) =>
			place?.routeId?.startsWith('/(app)/entry/[id]') ? place.entryId : undefined;
		const pending = this.#pending;
		this.#pending = null;
		if (pending && entryOf(to) === pending.entryId) {
			this.origin = pending;
			return;
		}
		if (!this.origin || to.routeId === '/(app)/history') return;
		const id = this.origin.entryId;
		if (entryOf(to) !== id || entryOf(from) !== id) this.origin = null;
	}

	resetLimit(): void {
		this.limit = PAGE_SIZE;
	}

	showMore(): void {
		this.limit += PAGE_SIZE;
	}
}

export const historyView = new HistoryView();
