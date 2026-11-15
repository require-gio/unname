/**
 * Select appropriate page image for each view.
 *
 * The sidecar renders each page in a few levels (PDF: 1×, 1.5×, 2×, 3× at
 * 72 dpi; images: ½× and 1×). The smallest level that provides at least one
 * image pixel per device pixel on screen is chosen – larger levels waste
 * memory, smaller ones look blurry.
 */

/**
 * @param pxPerUnit CSS pixels per unit of the display area (rendered page width ÷ page width from the map); includes zoom and page width.
 * @param dpr `devicePixelRatio`
 * @param levels the `scales` of the page, ascending
 */
export function imageLevel(pxPerUnit: number, dpr: number, levels: readonly number[]): number {
	if (levels.length === 0) throw new Error('Die Seite hat keine Bildstufen.');
	const sorted = [...levels].sort((a, b) => a - b);
	const needed = Math.max(0, pxPerUnit) * Math.max(1, dpr);
	// Rounding errors in layout measurement must not force a higher level.
	return sorted.find((level) => level >= needed - 0.01) ?? sorted[sorted.length - 1];
}

/** Distance in display units that corresponds to `px` CSS pixels on screen. */
export function inUnits(px: number, pxPerUnit: number): number {
	return pxPerUnit > 0 ? px / pxPerUnit : px;
}

/**
 * Longest side of an image at 100 % in CSS pixels: equal to the long side of an
 * A4 sheet (842 pt) as PDF. Photos lack a sensible print size – one image pixel
 * per CSS pixel made a phone photo 4000 px wide.
 */
export const IMAGE_LONGEST_PAGE = 842;

/**
 * Display size of a page at 100 % in CSS pixels. PDF pages (pt) stay 1:1;
 * images (px) are scaled down to {@link IMAGE_LONGEST_PAGE}, smaller images
 * (screenshots) keep original size.
 */
export function displaySize(page: { width: number; height: number; unit: 'pt' | 'px' }): {
	width: number;
	height: number;
} {
	const faktor =
		page.unit === 'px' ? Math.min(1, IMAGE_LONGEST_PAGE / Math.max(page.width, page.height, 1)) : 1;
	return { width: page.width * faktor, height: page.height * faktor };
}
