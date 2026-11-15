import { describe, expect, it } from 'vitest';
import type { ModelPart, ModelOverview } from '#lib/sidecar/models.js';
import {
	addressShort,
	applyEvent,
	errorKey,
	progress,
	needed,
	neededThere,
	percent,
	restSize,
	startSelection,
	toggle,
	loadTo
} from './setup.js';

function part(
	id: string,
	kind: ModelPart['kind'],
	size: number,
	next: Partial<ModelPart> = {}
): ModelPart {
	return {
		id,
		kind,
		origin: '',
		license: 'MIT',
		download_size: size,
		installed_size: size,
		state: 'missing',
		received: 0,
		installed_at: null,
		checked_at: null,
		error: null,
		files: [],
		...next
	};
}

function overview(parts: ModelPart[]): ModelOverview {
	return {
		revision: 't',
		sources: ['github', 'huggingface'],
		choice: { source: 'huggingface' },
		busy: false,
		components: parts
	};
}

const EMPTY = overview([
	part('ner', 'ner', 600),
	part('de', 'language', 570),
	part('ocr', 'ocr', 24)
]);

describe('Initial setup', () => {
	it('Name detection and the only language are required, OCR not needed', () => {
		const [ner, de, ocr] = EMPTY.components;
		expect(needed(ner, EMPTY.components)).toBe(true);
		expect(needed(de, EMPTY.components)).toBe(true);
		expect(needed(ocr, EMPTY.components)).toBe(false);
	});

	it('pre-selects missing items and does not deselect required ones', () => {
		const selection = startSelection(EMPTY);
		expect([...selection]).toEqual(['ner', 'de', 'ocr']);
		const withoutOcr = toggle(selection, EMPTY.components[2], EMPTY.components);
		expect([...withoutOcr]).toEqual(['ner', 'de']);
		expect([...toggle(withoutOcr, EMPTY.components[0], EMPTY.components)]).toEqual(['ner', 'de']);
		expect(restSize(EMPTY, loadTo(EMPTY, withoutOcr))).toBe(1170);
	});

	it('with two languages at least one remains selected', () => {
		const two = overview([
			part('ner', 'ner', 1),
			part('de', 'language', 1),
			part('en', 'language', 1)
		]);
		const [, de, en] = two.components;
		expect(needed(de, two.components)).toBe(false);
		const onlyEn = toggle(new Set(['ner', 'de', 'en']), de, two.components);
		expect([...onlyEn]).toEqual(['ner', 'en']);
		expect([...toggle(onlyEn, en, two.components)]).toEqual(['ner', 'en']);
	});

	it('loads only missing and counts partial downloads with the rest', () => {
		const partly = overview([
			part('ner', 'ner', 600, { state: 'installed', received: 600 }),
			part('de', 'language', 570, { state: 'partial', received: 170 }),
			part('ocr', 'ocr', 24)
		]);
		const ids = loadTo(partly, new Set(['ner', 'de', 'ocr']));
		expect(ids).toEqual(['de', 'ocr']);
		expect(restSize(partly, ids)).toBe(424);
	});

	it('“The necessary is present” follows the gate rule – also for bundled items', () => {
		expect(neededThere(EMPTY)).toBe(false);
		const onlyNer = overview([
			part('ner', 'ner', 1, { state: 'installed' }),
			part('de', 'language', 1),
			part('ocr', 'ocr', 1)
		]);
		expect(neededThere(onlyNer)).toBe(false);
		expect(neededThere(onlyNer, { de: { state: 'ready', source: 'bundle' } })).toBe(true);
	});

	it('propagates progress, completion and errors from events', () => {
		let status = applyEvent(EMPTY, {
			component: 'ner',
			state: 'downloading',
			received: 150,
			total: 600,
			error: null
		});
		expect(status.busy).toBe(true);
		expect(percent(status.components[0])).toBe(25);
		status = applyEvent(status, {
			component: 'ner',
			state: 'installed',
			received: 600,
			total: 600,
			error: null
		});
		expect(progress(status, ['ner', 'de'])).toEqual({ done: 1, total: 2 });
		status = applyEvent(status, {
			component: 'de',
			state: 'failed',
			received: 0,
			total: 570,
			error: { code: 'hash_mismatch', message: '' }
		});
		expect(status.components[1].error?.code).toBe('hash_mismatch');
		status = applyEvent(status, {
			component: null,
			state: 'failed',
			received: 0,
			total: 0,
			error: { code: 'hash_mismatch', message: '' }
		});
		expect(status.busy).toBe(false);
	});

	it('translates error codes and shortens addresses', () => {
		expect(errorKey('hash_mismatch')).toBe('models.error.checksum');
		expect(errorKey('unbekannt')).toBe('models.error.general');
		expect(errorKey(undefined)).toBe('models.error.general');
		expect(
			addressShort('https://huggingface.co/unname/modelle/resolve/v1/ner/model.int8.onnx')
		).toBe('huggingface.co/unname/modelle/resolve/v1/ner/model.int8.onnx');
		expect(addressShort(null)).toBe('');
	});
});
