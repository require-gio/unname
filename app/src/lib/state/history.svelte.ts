import { fetchEntries, fetchStorage } from '#lib/sidecar/api-v2.js';
import type { Entry, StorageResponse } from '#lib/sidecar/api.gen.js';
import type { SidecarConnection } from '#lib/sidecar/types.js';
import { runningStill } from '#lib/history/entry-meta.js';

/**
 * History (GET /entries) is grouped and labeled in `#lib/history/entry-meta.ts`.
 *
 * If the connection drops, the list stays static: reading does not lock the gate,
 * and an empty sidebar on each hiccup would look like the history was deleted.
 */
class History {
	entries = $state<Entry[]>([]);
	/** Space used per entry and deletion date; `null` while unknown. */
	storage = $state<StorageResponse | null>(null);
	loaded = $state(false);
	error = $state(false);
	#connection: SidecarConnection | null = null;
	/** A delayed response must not overwrite a newer one. */
	#revision = 0;

	/** While a detection runs, the shell polls regularly. */
	get running(): boolean {
		return runningStill(this.entries);
	}

	async load(connection: SidecarConnection | null): Promise<void> {
		this.#connection = connection;
		if (connection) await this.update();
	}

	/**
	 * Take over one entry the sidecar just returned (rename, H4). A list
	 * response that was already on its way is older than this entry and is
	 * dropped, so it cannot bring the previous title back; the refresh then
	 * reconciles with the sidecar.
	 */
	upsert(entry: Entry): void {
		this.#revision++;
		const index = this.entries.findIndex((item) => item.id === entry.id);
		this.entries =
			index === -1
				? [entry, ...this.entries]
				: this.entries.map((item) => (item.id === entry.id ? entry : item));
		void this.update();
	}

	async update(): Promise<void> {
		const connection = this.#connection;
		if (!connection) return;
		const revision = ++this.#revision;
		// The space entry is only a hint: if missing, the history remains.
		const storage = fetchStorage(connection).catch(() => null);
		try {
			const answer = await fetchEntries(connection);
			if (revision !== this.#revision) return;
			this.entries = answer.entries;
			this.loaded = true;
			this.error = false;
		} catch {
			if (revision === this.#revision) this.error = true;
		}
		const space = await storage;
		if (revision === this.#revision && space) this.storage = space;
	}
}

export const history = new History();
