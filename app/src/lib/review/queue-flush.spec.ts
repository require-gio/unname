import { describe, expect, it } from 'vitest';
import { ApplyQueue } from './queue.js';

describe('Flushing the review queue', () => {
	it('waits for the latest decision after an older Apply finishes', async () => {
		const requests: number[] = [];
		const completions: (() => void)[] = [];
		const queue = new ApplyQueue<number, void>(
			async (value) => {
				requests.push(value);
				await new Promise<void>((resolve) => completions.push(resolve));
			},
			{ result: () => {}, error: () => {} }
		);
		queue.places(1);
		queue.places(2);
		queue.places(3);
		let finished = false;
		const flush = queue.flush().then(() => {
			finished = true;
		});
		expect(requests).toEqual([1]);
		completions.shift()!();
		await Promise.resolve();
		await Promise.resolve();
		expect(requests).toEqual([1, 3]);
		expect(finished).toBe(false);
		completions.shift()!();
		await flush;
		expect(finished).toBe(true);
	});

	it('drains the queue when a preview fails after persisting the review', async () => {
		let fail!: (reason: Error) => void;
		const errors: unknown[] = [];
		const queue = new ApplyQueue<number, void>(
			() =>
				new Promise((_, reject) => {
					fail = reject;
				}),
			{ result: () => {}, error: (error) => errors.push(error) }
		);
		queue.places(1);
		const flush = queue.flush();
		fail(new Error('preview failed'));
		await flush;
		expect(errors).toHaveLength(1);
		expect(queue.running).toBe(false);
	});
});
