import { fileSize } from '#lib/format.js';
import type { Translator } from '#lib/i18n/translate.js';
import type { Segment } from '#lib/ui/segments.js';
import type {
	Entry,
	RestoreCounts,
	RestoreFileResponse,
	RestoreOrigin,
	RestoredSpan
} from '#lib/sidecar/api.gen.js';

/**
 * Board “5 · AI answer restore”: shows the sidecar’s response in the window.
 * The sidecar performs the restore and counting; this code only creates segments and sentences.
 */

/**
 * Produce segments from the output, marking restored parts (indigo) and others (green),
 * unknown codes (amber). Offsets are UTF-16 as used by `String.prototype.slice`.
 */
export function outputSegments(text: string, spans: readonly RestoredSpan[]): Segment[] {
	const segments: Segment[] = [];
	let cursor = 0;
	for (const span of [...spans].sort((a, b) => a.start - b.start)) {
		if (span.start < cursor || span.end > text.length) continue;
		if (span.start > cursor) segments.push({ kind: 'plain', text: text.slice(cursor, span.start) });
		const kind =
			span.status === 'unknown'
				? 'unknown-code'
				: span.origin === 'here'
					? 'restored'
					: 'restored-other';
		segments.push({ kind, text: text.slice(span.start, span.end), type: span.entity_type });
		cursor = span.end;
	}
	if (cursor < text.length) segments.push({ kind: 'plain', text: text.slice(cursor) });
	return segments;
}

export interface Origin {
	/** “hier” is shown in indigo, everything else in green – like underline in the text. */
	kind: 'here' | 'other';
	text: string;
}

export interface Count {
	/** Total replaced places (“6 codes replaced”). */
	replaced: number;
	origin: Origin[];
	/** Places that look like a code but are not in the table. */
	unknown: number;
	/** Recognized in altered form (parentheses, spelling …). */
	changed: number;
	/** No code found in the answer. */
	noCode: boolean;
}

/** Show a few other entries with titles; group the rest. */
const WITH_TITLE = 2;

/** “5 from here · 1 from “Lease contract (Scan)” · 2 from earlier entries”. */
export function count(
	counts: RestoreCounts,
	origins: readonly RestoreOrigin[],
	t: Translator
): Count {
	const origin: Origin[] = [];
	if (counts.here > 0) origin.push({ kind: 'here', text: t('restore.here', { n: counts.here }) });
	for (const item of origins.slice(0, WITH_TITLE)) {
		origin.push({
			kind: 'other',
			text: t('restore.from', { n: item.count, title: item.title })
		});
	}
	const weitere = origins.slice(WITH_TITLE).reduce((sum, origin) => sum + origin.count, 0);
	if (weitere > 0) origin.push({ kind: 'other', text: t('restore.further', { n: weitere }) });
	if (counts.earlier > 0) {
		origin.push({ kind: 'other', text: t('restore.earlier', { n: counts.earlier }) });
	}
	return {
		replaced: counts.restored,
		origin,
		unknown: counts.unknown,
		changed: counts.tolerant,
		noCode: counts.restored === 0 && counts.unknown === 0
	};
}

/** “by AI · 24 KB · 8 codes found”. */
export function fileMeta(file: RestoreFileResponse, t: Translator): string {
	return [
		t('restore.file.byAi'),
		fileSize(file.source.size_bytes),
		t('restore.file.found', { n: file.counts.restored + file.counts.unknown })
	].join(' · ');
}

/** Also restore invented names if the entry used them. */
export function withAliases(entry: Pick<Entry, 'masking'> | null): boolean {
	return entry?.masking.style === 'surrogates';
}

/** Error codes from shell and sidecar as a sentence; unknowns are generic. */
const FILE_ERROR: Record<string, Parameters<Translator>[0]> = {
	unsupported_format: 'restore.file.error.format',
	unsupported_content: 'restore.file.error.content',
	invalid_file: 'restore.file.error.read',
	encoding: 'restore.file.error.encoding',
	empty: 'restore.file.error.empty',
	unreadable: 'restore.file.error.read',
	too_large: 'restore.file.error.large',
	file_too_large: 'restore.file.error.large',
	expired: 'restore.file.error.expired',
	same_as_source: 'restore.file.error.same',
	not_ready: 'restore.file.error.ready',
	cloud_not_downloaded: 'restore.file.error.cloud'
};

export function fileError(code: string | undefined, t: Translator): string {
	return t(FILE_ERROR[code ?? ''] ?? 'restore.file.error.otherwise');
}

/** Message from the shell on failure (`RestoreFailure`). */
export function errorCode(value: unknown): string | undefined {
	if (typeof value !== 'object' || value === null) return undefined;
	const code = (value as { code?: unknown }).code;
	return typeof code === 'string' ? code : undefined;
}
