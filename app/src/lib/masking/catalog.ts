/**
 * Category catalog – temporary in frontend.
 *
 * Until the sidecar provides it via `GET /catalog` (the single source of truth),
 * this file mirrors the planned table so that `MaskingControl` and the components
 * page show real categories.
 *
 * Labels are stored in the dictionary (`kategorie.<id>`), not here.
 */

export const CATEGORY_IDS = [
	'finance_ids',
	'names',
	'addresses',
	'contact',
	'numbers',
	'web',
	'organizations',
	'locations',
	'dates',
	'other_indirect'
] as const;

export type CategoryId = (typeof CATEGORY_IDS)[number];

export interface Category {
	id: CategoryId;
	/** Audit/report level the category originates from. */
	level: 1 | 2 | 3;
	/** Included in the default selection. */
	standard: boolean;
	/** Always present and cannot be disabled; only “keep only here” per entry. */
	locked: boolean;
	/** “Circumstances”: indirect traits that reveal a person via context. */
	circumstances: boolean;
	types: readonly string[];
}

export const CATALOG: readonly Category[] = [
	{
		id: 'finance_ids',
		level: 1,
		standard: true,
		locked: true,
		circumstances: false,
		types: [
			'IBAN_CODE',
			'BIC',
			'CREDIT_CARD',
			'CRYPTO',
			'DE_TAX_ID',
			'DE_TAX_NUMBER',
			'DE_VAT_ID',
			'AT_UID',
			'DE_SOCIAL_SECURITY',
			'AT_SVNR',
			'DE_PASSPORT',
			'DE_ID_CARD',
			'DE_HEALTH_INSURANCE',
			'SECRET',
			'API_KEY',
			'ACCESS_TOKEN',
			'PASSWORD'
		]
	},
	{ id: 'names', level: 2, standard: true, locked: false, circumstances: false, types: ['PERSON'] },
	{
		id: 'addresses',
		level: 2,
		standard: true,
		locked: false,
		circumstances: false,
		types: ['ADDRESS', 'STREET_ADDRESS']
	},
	{
		id: 'contact',
		level: 2,
		standard: true,
		locked: false,
		circumstances: false,
		types: ['PHONE_NUMBER', 'EMAIL_ADDRESS', 'IP_ADDRESS']
	},
	{
		id: 'numbers',
		level: 2,
		standard: true,
		locked: false,
		circumstances: false,
		types: ['CUSTOMER_NUMBER', 'CASE_NUMBER', 'DE_KFZ']
	},
	{
		id: 'web',
		level: 3,
		standard: false,
		locked: false,
		circumstances: false,
		types: ['URL']
	},
	{
		id: 'organizations',
		level: 3,
		standard: false,
		locked: false,
		circumstances: true,
		types: ['ORGANIZATION', 'DE_HANDELSREGISTER', 'AT_FIRMENBUCH']
	},
	{
		id: 'locations',
		level: 3,
		standard: false,
		locked: false,
		circumstances: true,
		types: ['LOCATION', 'DE_PLZ']
	},
	{
		id: 'dates',
		level: 3,
		standard: false,
		locked: false,
		circumstances: true,
		types: ['DATE_TIME']
	},
	{
		id: 'other_indirect',
		level: 3,
		standard: false,
		locked: false,
		circumstances: true,
		types: ['NRP', 'JOB_TITLE', 'PROFESSION', 'OTHER']
	}
];

/** Category for an entity type; unknown types default to numbers. */
export function categoryForType(type: string, catalog = CATALOG): CategoryId {
	return catalog.find((category) => category.types.includes(type))?.id ?? 'numbers';
}
