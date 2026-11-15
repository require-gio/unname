import type { MessageKey, Translator } from '#lib/i18n/translate.js';

/**
 * Header for an open question: what the placeholder represents.
 *
 * Uncertainty applies not only to names but also to numbers, dates, and places —
 * “Recognized as name” above a phone number would be wrong. Phone and e-mail
 * are described more precisely than their shared category “contact”.
 */
const CATEGORIES = new Set([
	'names',
	'contact',
	'addresses',
	'numbers',
	'web',
	'finance_ids',
	'organizations',
	'locations',
	'dates',
	'other_indirect'
]);

export function questionLabel(type?: string, category?: string | null): string {
	if (type === 'PHONE_NUMBER') return 'phone';
	if (type === 'EMAIL_ADDRESS') return 'email';
	// When no info is given (Board “components”): the most common case, a name.
	if (type === undefined && !category) return 'names';
	return category && CATEGORIES.has(category) ? category : 'other_indirect';
}

export function questionTitle(
	t: Translator,
	kind: 'suspicion' | 'uncertain',
	type?: string,
	category?: string | null
): string {
	const was = questionLabel(type, category);
	return kind === 'suspicion'
		? t('question.suspicion', { was: t(`question.asA.${was}` as MessageKey) })
		: t('question.uncertain', { was: t(`question.as.${was}` as MessageKey) });
}
