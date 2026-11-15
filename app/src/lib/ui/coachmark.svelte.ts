import type { Attachment } from 'svelte/attachments';
import type { MessageKey, Values } from '#lib/i18n/translate.js';
import { help, type CoachMarkPage } from '#lib/state/help.svelte.js';

/**
 * Attach a numbered help note to an element.
 *
 * ```svelte
 * <button {@attach coachmark(1, 'hilfe.start.maskierung', { seite: 'right' })}>…</button>
 * ```
 *
 * While help is enabled, `CoachMarks` shows the note next to the element
 * and adds an `aria-describedby` reference so screen readers read it.
 */
export function coachmark(
	number: number,
	key: MessageKey,
	options: { page?: CoachMarkPage; values?: Values } = {}
): Attachment<HTMLElement> {
	return (target) => {
		const { id, abmelden } = help.register({
			number,
			key,
			values: options.values,
			target,
			page: options.page ?? 'bottom'
		});

		$effect(() => {
			if (!help.on) return;
			const notiz = help.notizId(id);
			const before = target.getAttribute('aria-describedby');
			target.setAttribute('aria-describedby', before ? `${before} ${notiz}` : notiz);
			return () => {
				if (before) target.setAttribute('aria-describedby', before);
				else target.removeAttribute('aria-describedby');
			};
		});

		return abmelden;
	};
}
