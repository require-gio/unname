import type { Locale } from '#lib/i18n/translate.js';

/**
 * Human-readable names of entity types in the UI language.
 *
 * Used for spoken codes (“Person: Original …”) and wherever a single type
 * appears instead of its category. Categories themselves are stored in the
 * dictionary (`kategorie.<id>`). `typen.spec.ts` checks that every catalog type
 * has a name in both languages.
 */
const TITLE: Record<Locale, Readonly<Record<string, string>>> = {
	de: {
		IBAN_CODE: 'IBAN',
		CREDIT_CARD: 'Kreditkartennummer',
		CRYPTO: 'Krypto-Adresse',
		BIC: 'BIC',
		DE_TAX_ID: 'Steuer-ID (DE)',
		DE_TAX_NUMBER: 'Steuernummer (DE)',
		DE_VAT_ID: 'USt-IdNr. (DE)',
		AT_UID: 'UID-Nummer (AT)',
		DE_SOCIAL_SECURITY: 'Sozialversicherungsnummer (DE)',
		AT_SVNR: 'Sozialversicherungsnummer (AT)',
		DE_PASSPORT: 'Reisepassnummer (DE)',
		DE_ID_CARD: 'Personalausweisnummer (DE)',
		DE_HEALTH_INSURANCE: 'Krankenversichertennummer (DE)',
		SECRET: 'Zugangsdaten',
		API_KEY: 'API-Schlüssel',
		ACCESS_TOKEN: 'Zugriffstoken',
		PASSWORD: 'Kennwort',
		PERSON: 'Person',
		EMAIL_ADDRESS: 'E-Mail-Adresse',
		PHONE_NUMBER: 'Telefonnummer',
		ADDRESS: 'Anschrift',
		STREET_ADDRESS: 'Straßenanschrift',
		CUSTOMER_NUMBER: 'Kundennummer',
		CASE_NUMBER: 'Aktenzeichen',
		DE_KFZ: 'Kfz-Kennzeichen (DE)',
		IP_ADDRESS: 'IP-Adresse',
		URL: 'Web-Adresse',
		ORGANIZATION: 'Organisation',
		LOCATION: 'Ort',
		DATE_TIME: 'Datum und Zeit',
		DE_PLZ: 'Postleitzahl (DE)',
		DE_HANDELSREGISTER: 'Handelsregisternummer (DE)',
		AT_FIRMENBUCH: 'Firmenbuchnummer (AT)',
		NRP: 'Nationalität, Religion, politische Haltung',
		JOB_TITLE: 'Funktionsbezeichnung',
		PROFESSION: 'Beruf',
		OTHER: 'Sonstige Angabe'
	},
	en: {
		IBAN_CODE: 'IBAN',
		CREDIT_CARD: 'Credit card number',
		CRYPTO: 'Crypto address',
		BIC: 'BIC',
		DE_TAX_ID: 'Tax ID (DE)',
		DE_TAX_NUMBER: 'Tax number (DE)',
		DE_VAT_ID: 'VAT ID (DE)',
		AT_UID: 'VAT ID (AT)',
		DE_SOCIAL_SECURITY: 'Social security number (DE)',
		AT_SVNR: 'Social security number (AT)',
		DE_PASSPORT: 'Passport number (DE)',
		DE_ID_CARD: 'ID card number (DE)',
		DE_HEALTH_INSURANCE: 'Health insurance number (DE)',
		SECRET: 'Credentials',
		API_KEY: 'API key',
		ACCESS_TOKEN: 'Access token',
		PASSWORD: 'Password',
		PERSON: 'Person',
		EMAIL_ADDRESS: 'Email address',
		PHONE_NUMBER: 'Phone number',
		ADDRESS: 'Address',
		STREET_ADDRESS: 'Street address',
		CUSTOMER_NUMBER: 'Customer number',
		CASE_NUMBER: 'Case number',
		DE_KFZ: 'Licence plate (DE)',
		IP_ADDRESS: 'IP address',
		URL: 'Web address',
		ORGANIZATION: 'Organisation',
		LOCATION: 'Place',
		DATE_TIME: 'Date and time',
		DE_PLZ: 'Postcode (DE)',
		DE_HANDELSREGISTER: 'Commercial register number (DE)',
		AT_FIRMENBUCH: 'Company register number (AT)',
		NRP: 'Nationality, religion, political views',
		JOB_TITLE: 'Job title',
		PROFESSION: 'Profession',
		OTHER: 'Other detail'
	}
};

/** Return the display name for a type; fall back to the raw identifier. */
export function typeTitle(type: string, language: Locale): string {
	return TITLE[language][type] ?? TITLE.de[type] ?? type;
}

/** List all named types for a language – used in completeness tests. */
export function namedTypes(language: Locale): string[] {
	return Object.keys(TITLE[language]);
}
