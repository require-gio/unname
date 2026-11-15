import type { MessageKey } from '#lib/i18n/translate.js';

/**
 * Reason a file was rejected – for display on the desktop.
 *
 * The codes come from two sources: the sidecar (`RejectedFile.code` on submit,
 * `EntryFile.error_code` after reading) and the shell (`inputs.rs`: unreadable,
 * too large, not submitted, iCloud only). The sidecar's own message is never shown:
 * it is written to the log.
 */
const TEXTS: Record<string, MessageKey> = {
	format_not_allowed: 'rejection.format',
	unsupported_file_format: 'rejection.format',
	missing_file_extension: 'rejection.format',
	unsupported: 'rejection.format',
	file_too_large: 'rejection.tooLarge',
	too_large: 'rejection.tooLarge',
	empty: 'rejection.empty',
	unreadable: 'rejection.unreadable',
	invalid_file_size: 'rejection.unreadable',
	missing_filename: 'rejection.unreadable',
	extraction_failed: 'rejection.unreadable',
	ocr_unavailable: 'rejection.ocr',
	analysis_failed: 'rejection.analysis',
	timeout: 'rejection.analysis',
	not_submitted: 'rejection.notProvided',
	storage_failed: 'rejection.storage',
	cloud_not_downloaded: 'rejection.cloud'
};

/** Whether any code indicates OCR was unavailable. */
export function missingOcr(codes: Iterable<string | null | undefined>): boolean {
	for (const code of codes) if (code === 'ocr_unavailable') return true;
	return false;
}

export function ablehnungsText(code: string | null | undefined): MessageKey {
	return (code && TEXTS[code]) || 'rejection.otherwise';
}
