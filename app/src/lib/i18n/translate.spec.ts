import { describe, expect, it } from 'vitest';
import { de, type Message, type MessageKey } from './de.js';
import { en } from './en.js';
import { placeholder, translate } from './translate.js';

/**
 * Dictionary test. Checks that placeholders, plural forms and fallback to German work.
 */

describe('translate', () => {
	it('returns German text', () => {
		expect(translate('de', 'common.close')).toBe('Schließen');
	});

	it('inserts placeholders and formats numbers per locale', () => {
		expect(translate('de', 'code.original', { text: 'Hofer' })).toBe('Original: Hofer');
		expect(translate('de', 'historyList.files', { n: 1284 })).toBe('1.284 Dateien');
		expect(translate('en', 'historyList.files', { n: 1284 })).toBe('1,284 files');
	});

	it('selects plural form according to language rules', () => {
		expect(translate('de', 'historyList.files', { n: 1 })).toBe('1 Datei');
		expect(translate('de', 'historyList.files', { n: 3 })).toBe('3 Dateien');
		expect(translate('en', 'historyList.files', { n: 1 })).toBe('1 file');
	});

	it('has a custom form for zero', () => {
		expect(translate('de', 'file.questions', { n: 0 })).toBe('keine Fragen');
		expect(translate('de', 'file.questions', { n: 2 })).toBe('2 brauchen Ihren Blick');
		// Without a custom zero form, the "other" form is used.
		expect(translate('de', 'historyList.files', { n: 0 })).toBe('0 Dateien');
	});

	it('translates to English', () => {
		expect(translate('en', 'question.keep')).toBe('Keep');
		expect(translate('en', 'review.save', { n: 3 })).toBe('Save all 3 files');
	});

	it('leaves a missing value visible instead of swallowing it', () => {
		expect(translate('de', 'code.original', {})).toBe('Original: {text}');
	});
});

describe('Dictionaries', () => {
	const form = (entry: Message) => (typeof entry === 'string' ? 'text' : 'plural');

	it('English is complete', () => {
		const fehlend = Object.keys(de).filter((key) => !(key in en));
		expect(fehlend).toEqual([]);
		expect(Object.keys(en).filter((key) => !(key in de))).toEqual([]);
	});

	it('English is translated, not copied', () => {
		// Only proper nouns, formats and symbols may be identical.
		const same = (Object.keys(de) as MessageKey[]).filter((key) => {
			const german = de[key];
			const english = en[key];
			return (
				JSON.stringify(german) === JSON.stringify(english) && /[äöüß]/i.test(JSON.stringify(german))
			);
		});
		expect(same).toEqual([]);
	});

	it.each(Object.entries(en) as [MessageKey, Message][])(
		'en.%s hat dieselben Platzhalter und dieselbe Form wie de',
		(key, entry) => {
			expect(form(entry)).toBe(form(de[key]));
			if (typeof entry !== 'string' && typeof de[key] !== 'string') {
				expect(Object.keys(entry).sort()).toEqual(Object.keys(de[key]).sort());
			}
			expect(placeholder(entry)).toEqual(placeholder(de[key]));
		}
	);

	it('speaks to "Sie" (E17)', () => {
		const texts = Object.values(de).flatMap((entry) =>
			typeof entry === 'string' ? [entry] : Object.values(entry)
		);
		const duzen = texts.filter((text) => /\b(du|dein|deine|dich|dir)\b/i.test(text));
		expect(duzen).toEqual([]);
	});
});
