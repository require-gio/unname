import { resolve } from '$app/paths';
import type { Entry } from '#lib/sidecar/api.gen.js';
import { entryTarget } from '#lib/history/entry-meta.js';
import { historyQuery, type HistoryScope, type HistorySort } from './history.js';

/**
 * One target helper for recent entries and history rows. It follows
 * `entryTarget`: an entry to review opens the review directly; everything
 * else opens the entry screen, which restores a failed file's review itself.
 */
export function entryHref(entry: Pick<Entry, 'id' | 'kind' | 'status'>): string {
	return entryTarget(entry as Entry) === 'review'
		? resolve('/(app)/entry/[id]/review', { id: entry.id })
		: resolve('/(app)/entry/[id]', { id: entry.id });
}

export function historyHref(scope: HistoryScope = 'all', sort: HistorySort = 'newest'): string {
	return `${resolve('/(app)/history')}${historyQuery(scope, sort)}`;
}
