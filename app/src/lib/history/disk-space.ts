import { toast } from 'svelte-sonner';
import { date, fileSize } from '#lib/format.js';
import type { Translator } from '#lib/i18n/translate.js';
import { fetchStorage } from '#lib/sidecar/api-v2.js';
import type { StorageResponse } from '#lib/sidecar/api.gen.js';
import type { SidecarConnection } from '#lib/sidecar/types.js';

/**
 * Storage used by the draft: each entry keeps an encrypted copy of its originals
 * until deletion. Space is visible and controllable – there is no hard quota.
 */

/** Low free space? Then the dropzone warns before loading, without blocking. */
export function fewSpace(storage: Pick<StorageResponse, 'free_bytes' | 'low_space_bytes'>) {
	const free = storage.free_bytes;
	return free !== null && free !== undefined && free < storage.low_space_bytes;
}

/**
 * Example notice for a history entry, e.g. "1.2 MB saved · will be deleted on 2026-12-14".
 * Without sidecar data the notice stays empty.
 */
export function entryNotice(
	storage: StorageResponse | null,
	entryId: string,
	t: Translator
): string | undefined {
	const entry = storage?.entries.find((kandidat) => kandidat.entry_id === entryId);
	if (!entry) return undefined;
	const parts: string[] = [];
	if (entry.bytes > 0) parts.push(t('historyList.notice.size', { size: fileSize(entry.bytes) }));
	parts.push(
		entry.delete_at
			? t('historyList.notice.deletion', { date: date(new Date(entry.delete_at)) })
			: t('historyList.notice.never')
	);
	return parts.join(' · ');
}

/**
 * Before loading: if space is low show a notice but continue. An error during the
 * review step does not stop anything.
 */
export async function spaceReview(connection: SidecarConnection, t: Translator): Promise<void> {
	try {
		const storage = await fetchStorage(connection);
		if (fewSpace(storage)) {
			toast.warning(t('dropzone.fewSpace', { free: fileSize(storage.free_bytes ?? 0) }));
		}
	} catch {
		// Just a notice.
	}
}
