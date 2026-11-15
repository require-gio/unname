import { describe, expect, it } from 'vitest';
import { translate } from '#lib/i18n/translate.js';
import type { StorageResponse } from '#lib/sidecar/api.gen.js';
import { entryNotice, fewSpace } from './disk-space.js';

const t = translate.bind(null, 'de');

function storage(part: Partial<StorageResponse> = {}): StorageResponse {
	return {
		total_bytes: 0,
		free_bytes: 10 * 1024 ** 3,
		low_space_bytes: 2 * 1024 ** 3,
		retention_days: 30,
		entries: [],
		...part
	};
}

describe('disk space of draft', () => {
	it('warns only when below threshold and never without a value', () => {
		expect(fewSpace(storage())).toBe(false);
		expect(fewSpace(storage({ free_bytes: 1024 ** 3 }))).toBe(true);
		expect(fewSpace(storage({ free_bytes: null }))).toBe(false);
	});

	it('reports size and retention of an entry', () => {
		const status = storage({
			entries: [
				{ entry_id: 'a', bytes: 2048, delete_at: '2026-12-14T09:00:00Z' },
				{ entry_id: 'b', bytes: 0, delete_at: null }
			]
		});
		expect(entryNotice(status, 'a', t)).toBe('2 KB gespeichert · wird am 14.12.2026 gelöscht');
		expect(entryNotice(status, 'b', t)).toBe('wird nicht automatisch gelöscht');
		expect(entryNotice(status, 'c', t)).toBeUndefined();
		expect(entryNotice(null, 'a', t)).toBeUndefined();
	});
});
