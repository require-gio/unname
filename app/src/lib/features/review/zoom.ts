/**
 * Zoom levels for the document. Classes are written out so Tailwind can detect them –
 * a runtime width or font size would be an inline style (Tailwind requires static class names).
 */
export const ZOOM_LEVELS = [70, 80, 90, 100, 110, 125, 150] as const;
export type ZoomLevel = (typeof ZOOM_LEVELS)[number];

export const ZOOM_CLASS: Record<ZoomLevel, string> = {
	70: '[zoom:0.7]',
	80: '[zoom:0.8]',
	90: '[zoom:0.9]',
	100: '',
	110: '[zoom:1.1]',
	125: '[zoom:1.25]',
	150: '[zoom:1.5]'
};

export function zoomStep(level: ZoomLevel, direction: 1 | -1): ZoomLevel {
	const index = ZOOM_LEVELS.indexOf(level) + direction;
	return ZOOM_LEVELS[Math.max(0, Math.min(ZOOM_LEVELS.length - 1, index))];
}
