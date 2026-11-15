import { describe, expect, it } from 'vitest';
import { toggleCountry, packageState, toggleLanguage } from './languages.js';

describe('Document languages', () => {
	it('toggles languages in a fixed order, keeping the last one active', () => {
		expect(toggleLanguage(['de'], 'en')).toEqual(['de', 'en']);
		expect(toggleLanguage(['en'], 'de')).toEqual(['de', 'en']);
		expect(toggleLanguage(['de', 'en'], 'de')).toEqual(['en']);
		expect(toggleLanguage(['en'], 'en')).toBeNull();
	});

	it('toggles countries similarly', () => {
		expect(toggleCountry(['AT'], 'DE')).toEqual(['DE', 'AT']);
		expect(toggleCountry(['DE', 'AT'], 'DE')).toEqual(['AT']);
		expect(toggleCountry(['DE'], 'DE')).toBeNull();
	});

	it('derives the state of a package from the shell and sidecar', () => {
		const part = { state: 'missing' as const, received: 0, download_size: 1000 };
		expect(packageState({ ...part, state: 'downloading', received: 250 }, undefined)).toEqual({
			kind: 'running',
			percent: 25
		});
		expect(packageState({ ...part, state: 'installed' }, undefined)).toEqual({
			kind: 'installed'
		});
		expect(packageState(part, { state: 'ready', source: 'bundle' })).toEqual({
			kind: 'bundled',
			source: 'bundle'
		});
		expect(packageState(undefined, { state: 'ready', source: 'local' })).toEqual({
			kind: 'bundled',
			source: 'local'
		});
		expect(
			packageState({ ...part, state: 'partial' }, { state: 'missing', source: 'download' })
		).toEqual({ kind: 'missing', started: true });
		expect(packageState(undefined, undefined)).toEqual({ kind: 'missing', started: false });
	});
});
