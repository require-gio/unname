import { ablehnungsText } from '#lib/inputs/rejection.js';
import { sinceTheSaveChanged } from '#lib/saved/saved.js';
import type { MessageKey, Translator } from '#lib/i18n/translate.js';
import type { Entry, EntryFile, EntryView } from '#lib/sidecar/api.gen.js';

/**
 * Help for entry views: where opening an entry leads, its meta line
 * ("3 Dateien · gespeichert", "Text · kopiert") and a file's state.
 * Navigation and overall history view are in `#lib/history/history.ts`.
 * The title comes from the sidecar and contains no personal names
 * (principle 9); we only display it here.
 */

export type MetaTone = 'normal' | 'question' | 'running' | 'error';

/** Where a click leads: an entry marked “to review” opens the review view. */
export type EntryTarget = 'entry' | 'review';

/** Shared reopening rule for history links and restored entry routes. */
export function entryTarget(entry: Entry, view?: EntryView | null): EntryTarget {
	if (entry.kind !== 'documents' || entry.status === 'processing' || entry.status === 'saved') {
		return 'entry';
	}
	return entry.status === 'review' || (entry.status === 'failed' && view?.active_file_id)
		? 'review'
		: 'entry';
}

function statusKey(entry: Entry): { key: MessageKey; tone: MetaTone } {
	switch (entry.status ?? 'processing') {
		case 'processing':
			return { key: 'historyList.status.processing', tone: 'running' };
		case 'review':
			return { key: 'historyList.status.review', tone: 'normal' };
		case 'copied':
			return { key: 'historyList.status.copied', tone: 'normal' };
		case 'failed':
			return { key: 'historyList.status.failed', tone: 'error' };
		case 'saved': {
			// Modified but not saved again.
			if (sinceTheSaveChanged(entry)) {
				return { key: 'historyList.status.changed', tone: 'question' };
			}
			// Files without a label get no label on save; they are ignored here.
			const files = (entry.files ?? []).filter((file) => file.label);
			// Use the strongest label only if every file has it —
			// a single irreversibly masked file does not change the entry overall.
			if (files.length > 0 && files.every((file) => file.label === 'anonymous')) {
				return { key: 'historyList.status.level3', tone: 'normal' };
			}
			if (entry.masking.method === 'irreversible') {
				return { key: 'historyList.status.irreversible', tone: 'normal' };
			}
			return { key: 'historyList.status.saved', tone: 'normal' };
		}
	}
}

/**
 * "3 files · saved", "text · copied" — and after restore
 * "3 files · 1 restore"
 */
export function historyMeta(entry: Entry, t: Translator): { text: string; tone: MetaTone } {
	const { key, tone } = statusKey(entry);
	const restored =
		(entry.restores ?? 0) > 0 &&
		(key === 'historyList.status.saved' || key === 'historyList.status.copied');
	const status = restored ? t('historyList.restorations', { n: entry.restores ?? 0 }) : t(key);
	if (entry.kind === 'text') return { text: `${t('historyList.text')} · ${status}`, tone };
	const n = entry.files?.length ?? 0;
	return { text: n > 0 ? `${t('historyList.files', { n })} · ${status}` : status, tone };
}

/** Is any entry still processing? If so, the history asks. */
export function runningStill(entries: readonly Entry[]): boolean {
	return entries.some((entry) => entry.status === 'processing');
}

/** How `FileRow` displays a file of the entry. */
export function fileStatus(file: Pick<EntryFile, 'status' | 'error_code'>): {
	status: 'waits' | 'running' | 'done' | 'error';
	text?: MessageKey;
} {
	switch (file.status ?? 'queued') {
		case 'queued':
			return { status: 'waits' };
		case 'processing':
			return { status: 'running' };
		case 'review':
			return { status: 'done', text: 'file.ready' };
		case 'failed':
			return { status: 'error', text: ablehnungsText(file.error_code) };
		case 'cancelled':
			return { status: 'error', text: 'file.cancelled' };
		case 'expired':
			return { status: 'error', text: 'file.expired' };
		case 'unavailable':
			return { status: 'error', text: 'file.unavailable' };
	}
}
