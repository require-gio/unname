/**
 * The three UI labels.
 *
 * The sidecar (`masking.py`, field `label` per file) assigns a saved result
 * a label using a single rule: the strongest level is only “anonymous”,
 * with no clear-text exception and no residual risk. The UI merely displays
 * the label; it does not derive it itself.
 */
export const LABELS = ['reversible', 'irreversible', 'anonymous'] as const;
export type Label = (typeof LABELS)[number];
