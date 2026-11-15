import { CATALOG, type Category, type CategoryId } from '#lib/masking/catalog.js';
import {
	toggleAll,
	asRequest,
	asPreset,
	initialState,
	fromPreset,
	istAll,
	toggleCategory,
	selectMethod,
	selectStyle,
	type MaskingState,
	type MaskingSpec,
	type Method,
	type Style,
	type Preset
} from '#lib/masking/rules.js';

/**
 * Masking as reactive state.
 *
 * This class is used for the home page (popover), the review bar, and the
 * settings (default). The rules live in `#lib/masking/rules.ts`;
 * here we only store and replace the state.
 *
 * “Anonymisieren” means `alles`: the word stem must not appear in any Svelte file
 * (`result-policy.spec.ts`), and components read these names.
 */
export class Masking {
	#status = $state<MaskingState>(initialState());
	readonly catalog: readonly Category[];
	/** The stored default whose replacement style is applied on method change. */
	readonly #preset: () => Preset | null | undefined;

	constructor(
		method: Method = 'reversible',
		catalog: readonly Category[] = CATALOG,
		preset: () => Preset | null | undefined = () => null
	) {
		this.catalog = catalog;
		this.#preset = preset;
		this.#status = initialState(method, catalog);
	}

	get method(): Method {
		return this.#status.method;
	}

	get style(): Style {
		return this.#status.style;
	}

	get categories(): ReadonlySet<CategoryId> {
		return this.#status.categories;
	}

	/** Derived, never persisted. */
	get all(): boolean {
		return istAll(this.#status, this.catalog);
	}

	selectMethod(method: Method): void {
		this.#status = selectMethod(this.#status, method, this.catalog, this.#preset());
	}

	selectStyle(style: Style): void {
		this.#status = selectStyle(this.#status, style);
	}

	toggleCategory(id: CategoryId): void {
		this.#status = toggleCategory(this.#status, id, this.catalog);
	}

	toggleAll(): void {
		this.#status = toggleAll(this.#status, this.catalog);
	}

	asRequest(): MaskingSpec {
		return asRequest(this.#status, this.catalog);
	}

	/** Apply the stored default masking (settings). */
	accept(preset: Preset | null | undefined): void {
		this.#status = fromPreset(preset, this.catalog);
	}

	asPreset(): ReturnType<typeof asPreset> {
		return asPreset(this.#status, this.catalog);
	}
}
