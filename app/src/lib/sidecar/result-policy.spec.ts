import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

function svelteFiles(directory: string): string[] {
	return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
		const path = join(directory, entry.name);
		if (entry.isDirectory()) return svelteFiles(path);
		return entry.name.endsWith('.svelte') ? [path] : [];
	});
}

describe('protected result label (spec §7.3)', () => {
	it('does not appear as a literal in any Svelte file', () => {
		// The highest level may only be shown via the label from the sidecar;
		// a word in the markup would be a commitment without review.
		for (const file of svelteFiles(join(process.cwd(), 'src'))) {
			expect(readFileSync(file, 'utf8'), file).not.toMatch(/anonym/i);
		}
	});
});
