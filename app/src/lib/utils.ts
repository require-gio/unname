import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';
import { createTV } from 'tailwind-variants';

export type { VariantProps } from 'tailwind-variants';

/**
 * Custom scales from `styles/theme.css` so tailwind-merge classifies them
 * correctly. Without this, `text-label` is seen as a color and gets removed
 * in `cn('text-ink text-label')`.
 */
const TW_MERGE_CONFIG = {
	extend: {
		theme: {
			text: [
				'fine',
				'caption',
				'meta',
				'label',
				'ui',
				'body',
				'lead',
				'doc',
				'title',
				'heading',
				'display',
				'display-lg'
			],
			shadow: [
				'1',
				'raised',
				'float',
				'card',
				'menu',
				'pop',
				'page',
				'bar',
				'dialog',
				'coach',
				'peek'
			],
			spacing: ['control-sm', 'control-md', 'control-lg']
		}
	}
};

const twMerge = extendTailwindMerge(TW_MERGE_CONFIG);

export function cn(...inputs: ClassValue[]) {
	return twMerge(clsx(inputs));
}

/** `tv()` from tailwind-variants using the same scales as `cn()`. */
export const tv = createTV({ twMerge: true, twMergeConfig: TW_MERGE_CONFIG });
