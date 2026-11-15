import { describe, expect, it } from 'vitest';
import { translate } from '#lib/i18n/translate.js';
import { technicalInfo, ocr } from './labels.js';

const de = (key: Parameters<typeof translate>[1], values?: Parameters<typeof translate>[2]) =>
	translate('de', key, values);
const en = (key: Parameters<typeof translate>[1], values?: Parameters<typeof translate>[2]) =>
	translate('en', key, values);

describe('Technical info', () => {
	it('list version and port without thousand separators', () => {
		const info = technicalInfo(
			{ state: 'ready', port: 51234, transport: 'tcp', pid: 4242, version: '0.2.0' },
			de
		);
		expect(info).toContain('0.2.0');
		expect(info).toContain('51234');
		expect(info).toContain('4242');
	});

	it('list no port for Unix socket', () => {
		const info = technicalInfo(
			{ state: 'ready', port: null, transport: 'unix', pid: 4242, version: '0.2.0' },
			de
		);
		expect(info).toContain('privaten Socket');
		expect(info).not.toContain('Port {port}');
		expect(info).not.toContain('127.0.0.1');
	});

	it('preserve original failure reason', () => {
		const info = technicalInfo(
			{ state: 'unreachable', reason: 'Sidecar mit Code 2 beendet.', restarting: false },
			de
		);
		expect(info).toContain('Code 2');
		expect(info).toContain('kein weiterer automatischer Versuch');
	});

	it('exists in every app language', () => {
		const info = technicalInfo({ state: 'starting' }, en);
		expect(info).not.toBe(technicalInfo({ state: 'starting' }, de));
	});
});

describe('OCR', () => {
	const vision = {
		engine: 'apple-vision',
		state: 'ready',
		request: 'text',
		revision: 3,
		os_version: '27.2',
		os_build: '26B123'
	} as const;

	it('names Apple Vision with revision and OS version', () => {
		expect(ocr(vision, de)).toBe('Apple Vision · Revision 3 · macOS 27.2 (26B123)');
		expect(ocr(vision, en)).toBe('Apple Vision · revision 3 · macOS 27.2 (26B123)');
	});

	it('indicates when Vision is prepared once', () => {
		expect(ocr({ ...vision, state: 'warming' }, de)).toBe(
			'Apple Vision · Revision 3 · macOS 27.2 (26B123) · Texterkennung wird vorbereitet (einmalig)'
		);
		expect(ocr({ engine: 'apple-vision', state: 'warming' }, de)).toContain('vorbereitet');
	});

	it('names Tesseract with version, without version as before', () => {
		expect(ocr({ engine: 'tesseract', state: 'ready', version: '5.5.2' }, de)).toBe(
			'Tesseract 5.5.2 · deu, eng'
		);
		expect(ocr({ engine: 'tesseract', state: 'ready' }, de)).toBe('Tesseract · deu, eng');
		expect(ocr(undefined, de)).toBe('Tesseract · deu, eng');
	});

	it('reports missing OCR', () => {
		expect(ocr({ ...vision, state: 'missing' }, de)).toBe('nicht verfügbar');
	});
});
