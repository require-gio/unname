import { describe, expect, it } from 'vitest';
import { de } from '#lib/i18n/de.js';
import { ablehnungsText, missingOcr } from './rejection.js';

describe('Rejection reasons', () => {
	it('maps sidecar and shell codes to custom messages', () => {
		expect(ablehnungsText('unsupported_file_format')).toBe('rejection.format');
		expect(ablehnungsText('file_too_large')).toBe('rejection.tooLarge');
		expect(ablehnungsText('too_large')).toBe('rejection.tooLarge');
		expect(ablehnungsText('empty')).toBe('rejection.empty');
		expect(ablehnungsText('not_submitted')).toBe('rejection.notProvided');
		expect(ablehnungsText('storage_failed')).toBe('rejection.storage');
		expect(ablehnungsText('ocr_unavailable')).toBe('rejection.ocr');
	});

	it('detects when a scan failed due to missing OCR', () => {
		expect(missingOcr(['empty', null, 'ocr_unavailable'])).toBe(true);
		expect(missingOcr(['empty', undefined, 'extraction_failed'])).toBe(false);
		expect(missingOcr([])).toBe(false);
	});

	it('falls back to a generic message for unknown codes, never the raw code', () => {
		expect(ablehnungsText('neu_im_sidecar')).toBe('rejection.otherwise');
		expect(ablehnungsText(null)).toBe('rejection.otherwise');
		expect(de[ablehnungsText(undefined)]).not.toMatch(/sidecar/i);
	});
});
