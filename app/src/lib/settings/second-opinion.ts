import type { SecondOpinionSettings } from '#lib/sidecar/api.gen.js';

/**
 * Second opinion settings: use the same defaults as the sidecar
 * (`SecondOpinionSettings`) until saved values are loaded.
 */
export type Policy = NonNullable<SecondOpinionSettings['policy']>;

export const SECOND_OPINION_PRESET: Required<SecondOpinionSettings> = {
	enabled: true,
	policy: 'protect',
	keep_threshold: 0.95
};
