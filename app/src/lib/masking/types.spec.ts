import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { LOCALES } from '#lib/i18n/translate.js';
import { CATALOG } from './catalog.js';
import { namedTypes, typeTitle } from './types.js';

/** Types defined in `ENTITY_MINIMUM_LEVEL` in the sidecar Python source. */
function pythonTypes(): string[] {
	const source = readFileSync(
		join(process.cwd(), '..', 'sidecar/src/presidio_sidecar/processing.py'),
		'utf8'
	);
	const start = source.indexOf('ENTITY_MINIMUM_LEVEL');
	expect(start, 'ENTITY_MINIMUM_LEVEL nicht gefunden').toBeGreaterThan(-1);
	const block = source.slice(start, source.indexOf('\n)\n', start));
	return [...block.matchAll(/"([A-Z0-9_]+)":\s*ProtectionLevel\./g)].map(([, type]) => type);
}

describe('entity type names', () => {
	it('catalog in UI matches exactly the sidecar types', () => {
		const catalog = CATALOG.flatMap((category) => category.types);
		expect([...catalog].sort()).toEqual(pythonTypes().sort());
	});

	it('each catalog type has a name in every app language', () => {
		const catalog = CATALOG.flatMap((category) => category.types);
		for (const language of LOCALES) {
			expect(namedTypes(language).sort()).toEqual([...catalog].sort());
		}
	});

	it('unknown types are returned unchanged', () => {
		expect(typeTitle('NEUER_TYP', 'en')).toBe('NEUER_TYP');
		expect(typeTitle('PERSON', 'en')).toBe('Person');
		expect(typeTitle('PHONE_NUMBER', 'de')).toBe('Telefonnummer');
	});
});
