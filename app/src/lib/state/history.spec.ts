import { describe, expect, it, vi } from 'vitest';
import type { Entry, EntryListResponse } from '#lib/sidecar/api.gen.js';
import type { SidecarConnection } from '#lib/sidecar/types.js';

const { fetchEntriesMock, fetchStorageMock } = vi.hoisted(() => ({
	fetchEntriesMock: vi.fn<() => Promise<EntryListResponse>>(),
	fetchStorageMock: vi.fn(() => Promise.reject(new Error('no storage')))
}));

vi.mock('#lib/sidecar/api-v2.js', () => ({
	fetchEntries: fetchEntriesMock,
	fetchStorage: fetchStorageMock
}));

import { history } from './history.svelte.js';

const CONNECTION: SidecarConnection = { base_url: 'unname-api://localhost', generation: 1 };

function entry(title: string): Entry {
	return {
		id: 'e-1',
		display_ref: 'K7M2QX',
		title,
		kind: 'documents',
		status: 'review',
		created_at: '2026-11-14T10:00:00Z',
		updated_at: '2026-11-14T10:00:00Z',
		masking: { method: 'reversible', style: 'codes', categories: ['names'], anonymize: false }
	};
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((done) => (resolve = done));
	return { promise, resolve };
}

describe('history store', () => {
	it('drops a list response that was in flight before a rename', async () => {
		fetchEntriesMock.mockResolvedValueOnce({ entries: [entry('Dokument')], count: 1 });
		await history.load(CONNECTION);
		expect(history.entries[0].title).toBe('Dokument');

		// A poll is under way with the old title …
		const stale = deferred<EntryListResponse>();
		fetchEntriesMock.mockReturnValueOnce(stale.promise);
		const polling = history.update();

		// … the rename answers first; its refresh brings the new title.
		const fresh = deferred<EntryListResponse>();
		fetchEntriesMock.mockReturnValueOnce(fresh.promise);
		history.upsert(entry('Projekt Nord'));
		expect(history.entries[0].title).toBe('Projekt Nord');

		stale.resolve({ entries: [entry('Dokument')], count: 1 });
		await polling;
		expect(history.entries[0].title).toBe('Projekt Nord');

		fresh.resolve({ entries: [entry('Projekt Nord')], count: 1 });
		await vi.waitFor(() => expect(fetchEntriesMock).toHaveBeenCalledTimes(3));
		expect(history.entries[0].title).toBe('Projekt Nord');
		expect(history.entries).toHaveLength(1);
	});

	it('keeps the last list when a refresh fails', async () => {
		fetchEntriesMock.mockRejectedValueOnce(new Error('offline'));
		await history.update();
		expect(history.error).toBe(true);
		expect(history.loaded).toBe(true);
		expect(history.entries).toHaveLength(1);
	});
});
