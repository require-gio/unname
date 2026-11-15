import { describe, expect, it } from 'vitest';
import { CATALOG, categoryForType } from './catalog.js';
import {
	toggleAll,
	asRequest,
	asPreset,
	initialState,
	fromPreset,
	samePreset,
	istAll,
	toggleCategory,
	selectMethod,
	standardCategories,
	selectStyle
} from './rules.js';

/**
 * Masking rules derived from the UI mockup. They apply to the home page,
 * review and settings uniformly, so they live here instead of a component.
 */

describe('Standard', () => {
	it('matches the previous level 2 and includes the locked categories', () => {
		expect([...standardCategories()].sort()).toEqual(
			['addresses', 'contact', 'finance_ids', 'names', 'numbers'].sort()
		);
		expect(initialState().style).toBe('codes');
		expect(initialState('irreversible').style).toBe('placeholder');
	});
});

describe('Method switch', () => {
	it('resets categories and style to defaults', () => {
		let status = toggleCategory(initialState(), 'locations');
		status = selectStyle(status, 'surrogates');
		status = selectMethod(status, 'irreversible');

		expect(status.method).toBe('irreversible');
		expect(status.style).toBe('placeholder');
		expect(status.categories.has('locations')).toBe(false);
	});

	it('keeps the saved default style when it matches the method', () => {
		const preset = { method: 'irreversible', style: 'mask' } as const;
		const status = selectMethod(initialState(), 'irreversible', CATALOG, preset);
		expect(status.style).toBe('mask');
		expect(status.categories.has('locations')).toBe(false);

		// The default is irreversible; for reversible we fall back to the first style.
		expect(selectMethod(status, 'reversible', CATALOG, preset).style).toBe('codes');
	});

	it('leaves state unchanged when the same method is selected', () => {
		const status = toggleCategory(initialState(), 'dates');
		expect(selectMethod(status, 'reversible')).toBe(status);
	});

	it('rejects a style that does not fit the method', () => {
		expect(() => selectStyle(initialState(), 'placeholder')).toThrow();
	});
});

describe('Anonymize', () => {
	it('switches to irreversible and selects all categories', () => {
		const status = toggleAll(initialState('reversible'));

		expect(status.method).toBe('irreversible');
		expect(status.style).toBe('placeholder');
		expect(status.categories.size).toBe(CATALOG.length);
		expect(istAll(status)).toBe(true);
	});

	it('restores the default selection when turned off', () => {
		const status = toggleAll(toggleAll(initialState()));

		expect(istAll(status)).toBe(false);
		expect(status.method).toBe('irreversible');
		expect([...status.categories].sort()).toEqual([...standardCategories()].sort());
	});

	it('is derived: manually toggling all yields the same result', () => {
		let status = initialState('irreversible');
		for (const category of CATALOG) {
			if (!status.categories.has(category.id)) {
				status = toggleCategory(status, category.id);
			}
		}
		expect(istAll(status)).toBe(true);
		expect(asRequest(status).anonymize).toBe(true);
	});

	it('never applies to reversible masking, even with all categories', () => {
		let status = initialState('reversible');
		for (const category of CATALOG) {
			if (!status.categories.has(category.id)) {
				status = toggleCategory(status, category.id);
			}
		}
		expect(istAll(status)).toBe(false);
	});

	it('disappears as soon as a category is deselected', () => {
		const status = toggleCategory(toggleAll(initialState()), 'dates');
		expect(istAll(status)).toBe(false);
	});
});

describe('Locked categories', () => {
	it('cannot be deselected', () => {
		const status = toggleCategory(initialState(), 'finance_ids');
		expect(status.categories.has('finance_ids')).toBe(true);
	});
});

describe('Masking request', () => {
	it('lists categories in catalog order', () => {
		const status = toggleCategory(initialState(), 'dates');
		expect(asRequest(status)).toEqual({
			method: 'reversible',
			style: 'codes',
			categories: ['finance_ids', 'names', 'addresses', 'contact', 'numbers', 'dates'],
			anonymize: false
		});
	});
});

describe('Catalog', () => {
	it('assigns exactly one category to each type', () => {
		const all = CATALOG.flatMap((category) => category.types);
		expect(new Set(all).size).toBe(all.length);
	});

	it('returns the category for a given type', () => {
		expect(categoryForType('PERSON')).toBe('names');
		expect(categoryForType('IBAN_CODE')).toBe('finance_ids');
		expect(categoryForType('UNBEKANNT')).toBe('numbers');
	});
});

describe('Saved preset', () => {
	it('keeps method, style and categories and always includes locked ones', () => {
		const status = fromPreset({
			method: 'irreversible',
			style: 'mask',
			categories: ['names', 'locations']
		});

		expect(status.method).toBe('irreversible');
		expect(status.style).toBe('mask');
		expect([...status.categories].sort()).toEqual(['finance_ids', 'locations', 'names']);
	});

	it('replaces an unsuitable style and unknown categories instead of keeping them', () => {
		const status = fromPreset({ method: 'reversible', style: 'remove', categories: ['erfunden'] });

		expect(status.style).toBe('codes');
		expect([...status.categories]).toEqual(['finance_ids']);
	});

	it('falls back to the default when no preset is provided', () => {
		expect(asPreset(fromPreset(null))).toEqual(asPreset(initialState()));
		expect(fromPreset({ method: 'irreversible' }).categories).toEqual(standardCategories());
	});

	it('does not store the derived "anonymize" property', () => {
		const preset = asPreset(toggleAll(initialState()));

		expect(preset).not.toHaveProperty('anonymize');
		expect(preset.categories).toHaveLength(CATALOG.length);
		expect(istAll(fromPreset(preset))).toBe(true);
	});

	it('compares presets by meaning, not by order', () => {
		expect(
			samePreset(
				{ method: 'reversible', style: 'codes', categories: ['names', 'finance_ids'] },
				{ method: 'reversible', style: 'codes', categories: ['names'] }
			)
		).toBe(true);
		expect(samePreset({ method: 'reversible' }, { method: 'irreversible' })).toBe(false);
	});
});
