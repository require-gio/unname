import { invoke } from '@tauri-apps/api/core';

/**
 * Load models via the shell.
 *
 * The UI only selects and displays. Connection targets, installed files,
 * and file authenticity are decided by the shell using the signed app manifest.
 */

export const MODELS_EVENT = 'models://progress';

/** `huggingface`, `github`, `custom` (own server) or `file` (package file only). */
export type ModelSource = 'huggingface' | 'github' | 'custom' | 'file';

export interface SourceChoice {
	source: ModelSource;
	custom_url?: string | null;
}

export interface ModelError {
	/**
	 * `busy`, `unknown_component`, `source_file_only`, `invalid_custom_url`,
	 * `host_not_allowed`, `network`, `http_status`, `size_mismatch`,
	 * `hash_mismatch`, `cancelled`, `io`, `invalid_package`,
	 * `package_incomplete`, `package_empty`, `not_installed`.
	 */
	code: string;
	message: string;
}

/** Current or final state of a component. */
export type ModelState =
	| 'installed'
	| 'missing'
	| 'partial'
	| 'queued'
	| 'downloading'
	| 'verifying'
	| 'installing'
	| 'failed'
	| 'cancelled';

export interface ModelFile {
	path: string;
	size: number;
	sha256: string;
	url: string | null;
}

export interface ModelPart {
	id: string;
	/** `ner` (named entity recognition), `language` (language), `ocr` (OCR). */
	kind: 'ner' | 'language' | 'ocr' | 'classifier';
	origin: string;
	license: string;
	download_size: number;
	installed_size: number;
	state: ModelState;
	received: number;
	installed_at: string | null;
	checked_at: string | null;
	error: ModelError | null;
	files: ModelFile[];
}

export interface ModelOverview {
	revision: string;
	sources: string[];
	choice: SourceChoice;
	busy: boolean;
	components: ModelPart[];
}

export interface ModelEvent {
	/** `null` for the whole task (`finished`, `failed`, `cancelled`). */
	component: string | null;
	state: ModelState | 'finished';
	received: number;
	total: number;
	error: ModelError | null;
}

export interface ReviewReport {
	checked_at: string;
	/** Components removed by the shell. */
	damaged: string[];
	overview: ModelOverview;
}

export function istModelError(value: unknown): value is ModelError {
	return (
		typeof value === 'object' &&
		value !== null &&
		typeof (value as { code?: unknown }).code === 'string'
	);
}

export function loadModels(): Promise<ModelOverview> {
	return invoke<ModelOverview>('models_overview');
}

export function setSource(choice: SourceChoice): Promise<ModelOverview> {
	return invoke<ModelOverview>('models_set_source', {
		source: choice.source,
		customUrl: choice.custom_url ?? null
	});
}

/** Starts in background; progress is emitted via `MODELS_EVENT`. */
export function herunterladen(components: readonly string[]): Promise<void> {
	return invoke<void>('models_download', { components: [...components] });
}

export function cancel(): Promise<void> {
	return invoke<void>('models_cancel');
}

/** "Install model package from file …"; `null` means cancelled. */
export function installPackage(dialog: {
	title: string;
	filter: string;
}): Promise<ModelOverview | null> {
	return invoke<ModelOverview | null>('models_import_package', {
		title: dialog.title,
		filter: dialog.filter
	});
}

export function review(): Promise<ReviewReport> {
	return invoke<ReviewReport>('models_verify');
}

export function remove(component: string): Promise<ModelOverview> {
	return invoke<ModelOverview>('models_remove', { component: component });
}
