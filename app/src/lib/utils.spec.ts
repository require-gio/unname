import { describe, expect, it } from 'vitest';
import { cn, tv } from './utils.js';

/**
 * tailwind-merge only knows its scales via the config in `utils.ts`.
 * Without it, colors or shadows may disappear silently.
 */
describe('cn and tv with token scales', () => {
	it('keeps font size and text color separate', () => {
		expect(cn('text-ink text-label')).toBe('text-ink text-label');
		expect(cn('text-label text-body')).toBe('text-body');
	});

	it('recognizes its own shadows and control heights', () => {
		expect(cn('shadow-1 shadow-pop')).toBe('shadow-pop');
		expect(cn('h-10 h-control-lg')).toBe('h-control-lg');
	});

	it('applies in tv() as well', () => {
		const button = tv({ base: 'text-ink text-body', variants: { small: { true: 'text-label' } } });
		expect(button({ small: true })).toBe('text-ink text-label');
	});
});
