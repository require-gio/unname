import { describe, expect, it } from 'vitest';
import type { FileProgress, ProgressResponse } from './api.gen.js';
import { dockShare } from './system.js';

function file(status: FileProgress['status'], progress: number): FileProgress {
	return {
		id: status + progress,
		name: 'x.pdf',
		status,
		phase: null,
		progress,
		counts: {},
		questions: null,
		error_code: null
	};
}

function status(files: FileProgress[]): ProgressResponse {
	return {
		entry_id: 'e',
		status: 'processing',
		files,
		totals: {},
		done: 0,
		total: files.length,
		ready: false
	};
}

describe('Dock progress', () => {
	it('shows nothing when idle', () => {
		expect(dockShare(null)).toBeNull();
		expect(dockShare(status([]))).toBeNull();
		expect(dockShare(status([file('review', 100), file('failed', 0)]))).toBeNull();
	});

	it('averages across files, completed count fully', () => {
		expect(dockShare(status([file('processing', 50), file('queued', 0)]))).toBe(0.25);
		expect(dockShare(status([file('review', 0), file('processing', 50)]))).toBe(0.75);
		expect(dockShare(status([file('failed', 3), file('queued', 0)]))).toBe(0.5);
	});

	it('clamps implausible values', () => {
		expect(dockShare(status([file('processing', 250)]))).toBe(1);
		expect(dockShare(status([file('processing', -5)]))).toBe(0);
	});
});
