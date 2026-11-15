import { describe, expect, it } from 'vitest';
import { fileSize } from './format.js';

describe('File size', () => {
	it('rounds KB to whole number and MB to one decimal with German comma', () => {
		expect(fileSize(512)).toBe('512 B');
		expect(fileSize(340 * 1024 + 300)).toBe('340 KB');
		expect(fileSize(1.25 * 1024 * 1024)).toBe('1,3 MB');
		expect(fileSize(50 * 1024 * 1024)).toBe('50 MB');
		expect(fileSize(2.5 * 1024 * 1024 * 1024)).toBe('2,5 GB');
	});
});
