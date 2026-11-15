import { CATALOG, type Category, type CategoryId } from './catalog.js';

/**
 * Masking rules as pure functions.
 *
 * Home (popover), review (toolbar) and settings (default) share these rules via
 * `#lib/state/maskierung.svelte.ts`. Rules from the mockup:
 *
 * - Changing the method resets categories and replacement style to defaults.
 * - “Anonymize” on = irreversible + all categories; off = default selection.
 * - “Anonymize” is derived and never persisted.
 *
 * Locked categories (account, ID & access data, E3) are always included; no
 * function can remove them.
 */

export const METHODS = ['reversible', 'irreversible'] as const;
export type Method = (typeof METHODS)[number];

export const STILE: Record<Method, readonly Style[]> = {
	reversible: ['codes', 'surrogates'],
	irreversible: ['placeholder', 'mask', 'remove']
};
export type Style = 'codes' | 'surrogates' | 'placeholder' | 'mask' | 'remove';

/** Style → processing mode in sidecar (spec §7.1). */
export const MODE_FOR_STYLE = {
	codes: 'token_deterministic',
	surrogates: 'surrogate',
	placeholder: 'placeholder',
	mask: 'mask',
	remove: 'remove'
} as const satisfies Record<Style, string>;

export interface MaskingState {
	method: Method;
	style: Style;
	categories: ReadonlySet<CategoryId>;
}

/**
 * Stored default masking (`PUT /settings`, section `masking`).
 * The `anonymize` flag is derived and never persisted.
 */
export interface Preset {
	method?: Method;
	style?: Style;
	categories?: readonly string[];
}

/** Masking request sent to the sidecar (`MaskingSpec`). */
export interface MaskingSpec {
	method: Method;
	style: Style;
	categories: CategoryId[];
	anonymize: boolean;
}

export function standardStyle(method: Method): Style {
	return STILE[method][0];
}

export function standardCategories(catalog: readonly Category[] = CATALOG): Set<CategoryId> {
	return new Set(catalog.filter((k) => k.standard || k.locked).map((k) => k.id));
}

export function initialState(
	method: Method = 'reversible',
	catalog: readonly Category[] = CATALOG
): MaskingState {
	return { method, style: standardStyle(method), categories: standardCategories(catalog) };
}

/** All categories selected and irreversible – the condition for “anonymize”. */
export function istAll(status: MaskingState, catalog: readonly Category[] = CATALOG): boolean {
	return status.method === 'irreversible' && catalog.every((k) => status.categories.has(k.id));
}

/**
 * Switch method. Reset categories to the default selection; set the replacement
 * style to the stored default if it matches the new method (e.g., if “blackout”
 * is the default, switching to irreversible does not pick “[Name]”), otherwise
 * use the first style of the method.
 */
export function selectMethod(
	status: MaskingState,
	method: Method,
	catalog: readonly Category[] = CATALOG,
	preset?: Preset | null
): MaskingState {
	if (status.method === method) return status;
	const start = initialState(method, catalog);
	const style = preset?.method === method ? preset.style : undefined;
	return style && STILE[method].includes(style) ? { ...start, style } : start;
}

export function selectStyle(status: MaskingState, style: Style): MaskingState {
	if (!STILE[status.method].includes(style)) {
		throw new Error(`Stil ${style} passt nicht zu ${status.method}`);
	}
	return { ...status, style };
}

export function toggleCategory(
	status: MaskingState,
	id: CategoryId,
	catalog: readonly Category[] = CATALOG
): MaskingState {
	if (catalog.find((k) => k.id === id)?.locked) return status;
	const categories = new Set(status.categories);
	if (categories.has(id)) categories.delete(id);
	else categories.add(id);
	return { ...status, categories };
}

export function toggleAll(
	status: MaskingState,
	catalog: readonly Category[] = CATALOG
): MaskingState {
	if (istAll(status, catalog)) {
		return { ...status, categories: standardCategories(catalog) };
	}
	const style = status.method === 'irreversible' ? status.style : standardStyle('irreversible');
	return {
		method: 'irreversible',
		style,
		categories: new Set(catalog.map((k) => k.id))
	};
}

export function asRequest(
	status: MaskingState,
	catalog: readonly Category[] = CATALOG
): MaskingSpec {
	return {
		method: status.method,
		style: status.style,
		// Catalog order so identical requests look the same.
		categories: catalog.map((k) => k.id).filter((id) => status.categories.has(id)),
		anonymize: istAll(status, catalog)
	};
}

/**
 * State derived from a saved preset.
 *
 * Foreign values are not kept but replaced: a style that does not match the
 * method falls back to the default style; unknown categories are dropped;
 * locked categories are always included. If no categories are present, the
 * default selection is used.
 */
export function fromPreset(
	preset: Preset | null | undefined,
	catalog: readonly Category[] = CATALOG
): MaskingState {
	const method: Method = preset?.method === 'irreversible' ? 'irreversible' : 'reversible';
	const style =
		preset?.style && STILE[method].includes(preset.style) ? preset.style : standardStyle(method);
	if (!preset?.categories) return { method, style, categories: standardCategories(catalog) };
	const selected = new Set(preset.categories);
	return {
		method,
		style,
		categories: new Set(catalog.filter((k) => k.locked || selected.has(k.id)).map((k) => k.id))
	};
}

/** Preset for saving – the request without the derived `anonymize`. */
export function asPreset(
	status: MaskingState,
	catalog: readonly Category[] = CATALOG
): Required<Preset> & { categories: CategoryId[] } {
	const { method, style, categories } = asRequest(status, catalog);
	return { method, style, categories };
}

/** Do two presets describe the same masking? */
export function samePreset(
	a: Preset | null | undefined,
	b: Preset | null | undefined,
	catalog: readonly Category[] = CATALOG
): boolean {
	const links = asPreset(fromPreset(a, catalog), catalog);
	const right = asPreset(fromPreset(b, catalog), catalog);
	return (
		links.method === right.method &&
		links.style === right.style &&
		links.categories.join() === right.categories.join()
	);
}
