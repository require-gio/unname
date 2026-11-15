import type { MessageKey, Translator } from '#lib/i18n/translate.js';
import type { CategoryId } from '#lib/masking/catalog.js';
import type { Segment } from '#lib/ui/segments.js';
import type { Decision } from './state.js';

/**
 * Context menu for a position in the document (spec §3.4, board “3 · review”).
 *
 * Clicking a code opens a shared menu attached to the code (spec §2.8). Options depend on
 * the state of the position:
 *
 * | state                         | title                | entries                                 |
 * |-------------------------------|----------------------|------------------------------------------|
 * | masked                        | “Name · 6 characters”| Keep only here · Keep all names          |
 * | question, decided “mask”      | “Masked by you”      | Keep anyway                              |
 * | keep only here                | “Keep only here”     | Mask again                               |
 * | question, decided “keep”      | “Kept by you”        | Mask anyway                              |
 * | category from                 | “Places stay”        | Mask all places                          |
 * | manually added               | “Added by you”       | Do not mask further                      |
 *
 * Locked categories (spec §3) never offer “Keep all …” – only the single position can be kept.
 */

export type MenuAction =
	'onlyHere' | 'undo' | 'mask' | 'keep' | 'categoryFrom' | 'categoryOn' | 'removeHand';

export interface MenuModel {
	title: string;
	entries: { label: string; action: MenuAction }[];
}

export interface MenuContext {
	/** The position is a decided question. */
	question: boolean;
	decision?: Decision;
	/** The category is locked (account, ID & access data). */
	locked: boolean;
	/** Number of positions of this category in the file – used for the title. */
	places: number;
	/** Manual entry corrects the bounds of a finding. */
	correction: boolean;
}

const one = (category: string) => `category.${category as CategoryId}.one` as const;
const plural = (category: string) => `category.${category as CategoryId}.plural` as const;

export function codeMenu(segment: Segment, context: MenuContext, t: Translator): MenuModel | null {
	const category = segment.category ?? 'numbers';
	const ai = context.decision?.by === 'ai-suggestion';
	const entry = (label: MessageKey, action: MenuAction, values?: Record<string, string>) => ({
		label: t(label, values),
		action
	});

	switch (segment.kind) {
		case 'code':
		case 'placeholder':
		case 'masked-original': {
			if (segment.manual) {
				return context.correction
					? {
							title: t('menu.adjusted'),
							entries: [entry('menu.undoAdjustment', 'removeHand')]
						}
					: {
							title: t('menu.addedLater'),
							entries: [entry('menu.notMask', 'removeHand')]
						};
			}
			if (context.question && context.decision?.action === 'accept') {
				return {
					title: t(ai ? 'menu.byAiMasked' : 'menu.byYouMasked'),
					entries: [entry('question.anywayKeep', 'keep')]
				};
			}
			const entries = [entry('code.onlyHereKeep', 'onlyHere')];
			if (!context.locked) {
				entries.push(entry('code.allKeep', 'categoryFrom', { category: t(plural(category)) }));
			}
			return {
				title: `${t(one(category))} · ${t('code.places', { n: context.places })}`,
				entries
			};
		}
		case 'kept-here':
		case 'ai-kept':
			if (context.question || ai) {
				return {
					title: t(ai ? 'menu.byAiKeep' : 'menu.byYouKeep'),
					entries: [entry('question.maskAnyway', 'mask')]
				};
			}
			return {
				title: t('code.onlyHereKeep'),
				entries: [entry('code.maskAgain', 'undo')]
			};
		case 'kept-category':
			return {
				title: t('menu.stayAsIs', { category: t(`category.${category as CategoryId}`) }),
				entries: [entry('code.maskAll', 'categoryOn', { category: t(plural(category)) })]
			};
		default:
			return null;
	}
}
