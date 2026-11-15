import type { Translator } from '#lib/i18n/translate.js';
import type { OcrStatus, SidecarStatus } from './types.js';

/**
 * Technical info about the running service – only for the “Technology” section
 * and the diagnostic report. Otherwise the UI talks about the work, not its
 * implementation (`gate-hinweis.ts`).
 */
export function technicalInfo(status: SidecarStatus, t: Translator): string {
	switch (status.state) {
		case 'ready':
			if (status.transport === 'unix' || status.port === null) {
				return t('settings.technical.state.ready.socket', {
					version: status.version,
					pid: String(status.pid)
				});
			}
			return t('settings.technical.state.ready', {
				version: status.version,
				pid: String(status.pid),
				port: String(status.port)
			});
		case 'unreachable':
			return t(
				status.restarting ? 'settings.technical.state.restart' : 'settings.technical.state.failure',
				{
					reason: status.reason
				}
			);
		case 'starting':
			return t('settings.technical.state.starting');
		case 'stopped':
			return t('settings.technical.state.stopped');
	}
}

/**
 * OCR details for Settings › Technology, e.g. “Apple Vision · Revision 3 · macOS 27.2 (26B123)”
 * or “Tesseract 5.5.2 · deu, eng”.
 */
export function ocr(ocr: OcrStatus | undefined, t: Translator): string {
	if (!ocr) return t('settings.technical.ocr.value');
	if (ocr.state === 'missing') return t('settings.technical.ocr.missing');
	if (ocr.engine === 'tesseract') {
		return ocr.version
			? t('settings.technical.ocr.tesseract', { version: ocr.version })
			: t('settings.technical.ocr.value');
	}
	const value = ocr.os_version
		? t('settings.technical.ocr.vision', {
				revision: String(ocr.revision ?? 3),
				os: ocr.os_version,
				build: ocr.os_build ?? '–'
			})
		: t('settings.technical.ocr.vision.short');
	return ocr.state === 'warming' ? `${value} · ${t('models.ocr.preparation')}` : value;
}
