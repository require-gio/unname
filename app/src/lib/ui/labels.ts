import type { MessageKey } from '#lib/i18n/translate.js';
import type { Label } from '#lib/sidecar/result-policy.js';
import type { IconName } from './icons.js';

/**
 * How the three result labels look. Which label applies is decided by the
 * sidecar alone (`#lib/sidecar/result-policy.ts`).
 */
export const LABEL_DISPLAY: Record<
	Label,
	{
		text: MessageKey;
		explanation: MessageKey;
		technicalTerm: MessageKey;
		icon: IconName;
		klass: string;
	}
> = {
	reversible: {
		text: 'label.reversible',
		explanation: 'label.reversible.explanation',
		technicalTerm: 'label.reversible.technicalTerm',
		icon: 'restore',
		klass: 'bg-tint-3 text-tint-ink'
	},
	irreversible: {
		text: 'label.irreversible',
		explanation: 'label.irreversible.explanation',
		technicalTerm: 'label.irreversible.technicalTerm',
		icon: 'lock',
		klass: 'bg-chip-strong text-ink'
	},
	anonymous: {
		text: 'label.level3',
		explanation: 'label.level3.explanation',
		technicalTerm: 'label.level3.technicalTerm',
		icon: 'lockFull',
		klass: 'bg-ink text-on-ink'
	}
};
