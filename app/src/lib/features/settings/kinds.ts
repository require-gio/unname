import type { MessageKey } from '#lib/i18n/translate.js';
import type { KindOfInfo } from '#lib/state/names.svelte.js';

/**
 * Types of term or pattern – same categories as “type of entry”
 * used for masking, plus the reference number. Determines category
 * and placeholder in the sidecar.
 */
export const KINDS: readonly KindOfInfo[] = [
	'PERSON',
	'ORGANIZATION',
	'LOCATION',
	'CUSTOMER_NUMBER',
	'CASE_NUMBER',
	'OTHER'
];

export const KIND_TEXT: Record<KindOfInfo, MessageKey> = {
	PERSON: 'settings.kind.PERSON',
	ORGANIZATION: 'settings.kind.ORGANIZATION',
	LOCATION: 'settings.kind.LOCATION',
	CUSTOMER_NUMBER: 'settings.kind.CUSTOMER_NUMBER',
	CASE_NUMBER: 'settings.kind.CASE_NUMBER',
	OTHER: 'settings.kind.OTHER'
};

/** Placeholders as defined in the sidecar catalog (`catalog.PLACEHOLDERS`). */
export const PLACEHOLDER: Record<KindOfInfo, MessageKey> = {
	PERSON: 'settings.placeholder.PERSON',
	ORGANIZATION: 'settings.placeholder.ORGANIZATION',
	LOCATION: 'settings.placeholder.LOCATION',
	CUSTOMER_NUMBER: 'settings.placeholder.CUSTOMER_NUMBER',
	CASE_NUMBER: 'settings.placeholder.CASE_NUMBER',
	OTHER: 'settings.placeholder.OTHER'
};
