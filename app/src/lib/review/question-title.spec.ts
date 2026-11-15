import { describe, expect, it } from 'vitest';
import { translate, type MessageKey, type Values } from '#lib/i18n/translate.js';
import { questionTitle } from './question-title.js';

const de = (key: MessageKey, values?: Values) => translate('de', key, values);
const en = (key: MessageKey, values?: Values) => translate('en', key, values);

describe('frageTitel', () => {
	it('does not call phone numbers "Name" (regression delivery note)', () => {
		expect(questionTitle(de, 'uncertain', 'PHONE_NUMBER', 'contact')).toBe(
			'Als Telefonnummer erkannt – aber unsicher'
		);
		expect(questionTitle(en, 'uncertain', 'PHONE_NUMBER', 'contact')).toBe(
			'Detected as a phone number – but uncertain'
		);
	});

	it('follows the category otherwise', () => {
		expect(questionTitle(de, 'uncertain', 'DATE_TIME', 'dates')).toBe(
			'Als Datum erkannt – aber unsicher'
		);
		expect(questionTitle(de, 'uncertain', 'DE_TAX_NUMBER', 'finance_ids')).toBe(
			'Als Konto- oder Ausweisnummer erkannt – aber unsicher'
		);
		expect(questionTitle(de, 'suspicion', 'LOCATION', 'locations')).toBe(
			'Sieht aus wie ein Ort – wurde aber nicht erkannt'
		);
	});

	it('keeps original wording for names', () => {
		expect(questionTitle(de, 'suspicion', 'PERSON', 'names')).toBe(
			'Sieht aus wie ein Name – wurde aber nicht erkannt'
		);
		expect(questionTitle(de, 'uncertain')).toBe('Als Name erkannt – aber unsicher');
	});

	it('falls back to a neutral label for unknown category', () => {
		expect(questionTitle(de, 'uncertain', 'CUSTOM_THING', null)).toBe(
			'Als persönliches Merkmal erkannt – aber unsicher'
		);
	});
});
