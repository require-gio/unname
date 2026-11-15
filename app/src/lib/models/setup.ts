import type { MessageKey } from '#lib/i18n/translate.js';
import type { ModelPart, ModelEvent, ModelOverview, ModelState } from '#lib/sidecar/models.js';

/**
 * Initial setup and “Models & Storage” as pure functions.
 *
 * “The essentials” are name entity detection and one language – same rule as in the
 * shell’s setup gate (`setup_required`). The shell remains the source of truth;
 * this module only displays and selects.
 */

/** What the sidecar reports for each component (`/health` or `/models`). */
export type SidecarExisting = Readonly<
	Record<string, { state: 'ready' | 'missing'; source: string } | undefined>
>;

const LAUFEND: ReadonlySet<ModelState> = new Set([
	'queued',
	'downloading',
	'verifying',
	'installing'
]);

export function running(part: Pick<ModelPart, 'state'>): boolean {
	return LAUFEND.has(part.state);
}

/** A component that is required for detection – cannot be deselected. */
export function needed(part: ModelPart, all: readonly ModelPart[]): boolean {
	if (part.kind === 'ner') return true;
	// As long as there is only one language, it must stay selected (“one language”).
	return part.kind === 'language' && all.filter((item) => item.kind === 'language').length === 1;
}

/** Default selection: everything that is still missing (like board “0 · Initial setup”). */
export function startSelection(overview: ModelOverview): Set<string> {
	return new Set(
		overview.components.filter((part) => part.state !== 'installed').map((part) => part.id)
	);
}

export function toggle(
	selection: ReadonlySet<string>,
	part: ModelPart,
	all: readonly ModelPart[]
): Set<string> {
	const fresh = new Set(selection);
	if (needed(part, all)) {
		fresh.add(part.id);
		return fresh;
	}
	if (fresh.has(part.id)) {
		fresh.delete(part.id);
		// At least one language stays selected.
		if (
			part.kind === 'language' &&
			!all.some((item) => item.kind === 'language' && fresh.has(item.id))
		)
			fresh.add(part.id);
	} else {
		fresh.add(part.id);
	}
	return fresh;
}

/** What “Download and check” loads: selected items that are not yet installed. */
export function loadTo(overview: ModelOverview, selection: ReadonlySet<string>): string[] {
	return overview.components
		.filter(
			(part) =>
				(selection.has(part.id) || needed(part, overview.components)) && part.state !== 'installed'
		)
		.map((part) => part.id);
}

/** Remaining bytes to download; a partially completed download counts only the remainder. */
export function restSize(overview: ModelOverview, ids: readonly string[]): number {
	return overview.components
		.filter((part) => ids.includes(part.id))
		.reduce((sum, part) => sum + Math.max(0, part.download_size - part.received), 0);
}

export function installed(part: ModelPart, existing: SidecarExisting = {}): boolean {
	return part.state === 'installed' || existing[part.id]?.state === 'ready';
}

/** “The essentials are present”: name entity detection and one language. */
export function neededThere(overview: ModelOverview, existing: SidecarExisting = {}): boolean {
	const there = (kind: ModelPart['kind']) =>
		overview.components.some((part) => part.kind === kind && installed(part, existing));
	return there('ner') && there('language');
}

export interface Progress {
	done: number;
	total: number;
}

/** “2 of 3 done” for the components of a download operation. */
export function progress(overview: ModelOverview, ids: readonly string[]): Progress {
	const parts = overview.components.filter((part) => ids.includes(part.id));
	return {
		done: parts.filter((part) => part.state === 'installed').length,
		total: parts.length
	};
}

/** Integer percent of a component (0–100), rounded down. */
export function percent(part: Pick<ModelPart, 'received' | 'download_size'>): number {
	if (part.download_size <= 0) return 0;
	return Math.min(100, Math.floor((part.received / part.download_size) * 100));
}

/** Apply a shell event to the overview without reloading. */
export function applyEvent(overview: ModelOverview, event: ModelEvent): ModelOverview {
	if (event.component === null) {
		return event.state === 'finished' || event.state === 'failed' || event.state === 'cancelled'
			? { ...overview, busy: false }
			: overview;
	}
	const status = event.state as ModelState;
	return {
		...overview,
		busy: LAUFEND.has(status) ? true : overview.busy,
		components: overview.components.map((part) =>
			part.id === event.component
				? {
						...part,
						state: status,
						received: event.received,
						error: event.error ?? (status === 'failed' ? part.error : null)
					}
				: part
		)
	};
}

/** Name and description of a component in the dictionary. */
export function componentText(part: Pick<ModelPart, 'id' | 'kind'>): {
	name: MessageKey;
	text: MessageKey;
} {
	switch (part.kind) {
		case 'ner':
			return { name: 'models.ner', text: 'models.ner.text' };
		case 'ocr':
			return { name: 'models.ocr', text: 'models.ocr.text' };
		case 'classifier':
			return { name: 'models.classifier', text: 'models.classifier.text' };
		default:
			if (part.id === 'de') return { name: 'models.de', text: 'models.de.text' };
			if (part.id === 'en') return { name: 'models.en', text: 'models.en.text' };
			return { name: 'models.language', text: 'models.language.text' };
	}
}

const ERROR: Readonly<Record<string, MessageKey>> = {
	busy: 'models.error.busy',
	source_file_only: 'models.error.onlyFile',
	invalid_custom_url: 'models.error.server',
	host_not_allowed: 'models.error.host',
	network: 'models.error.network',
	http_status: 'models.error.source',
	size_mismatch: 'models.error.checksum',
	hash_mismatch: 'models.error.checksum',
	cancelled: 'models.error.cancelled',
	io: 'models.error.disk',
	invalid_package: 'models.error.package',
	package_incomplete: 'models.error.packageIncomplete',
	package_empty: 'models.error.packageEmpty'
};

export function errorKey(code: string | undefined): MessageKey {
	return (code && ERROR[code]) || 'models.error.general';
}

/** Host and path of a URL for “What exactly is being loaded” (no scheme). */
export function addressShort(url: string | null): string {
	if (!url) return '';
	try {
		const address = new URL(url);
		return `${address.host}${address.pathname}`;
	} catch {
		return url;
	}
}
