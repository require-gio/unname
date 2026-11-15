import { invoke } from '@tauri-apps/api/core';
import type { Entry, SaveEntryRequest } from './api.gen.js';

/**
 * Save via the shell.
 *
 * The UI shows entry, final state and the target folder name in the app language –
 * never a path. The shell decides where to write (beside the originals, last used
 * or system dialog); it checks each file provided by the sidecar and publishes
 * them without collisions (`app/src-tauri/src/saving.rs`).
 */

/** Error reported by the shell on failed save. */
export interface StorageError {
	/**
	 * From sidecar: `irreversible_confirmation_required`, `files_expired`,
	 * `still_processing`, `pdf_check_failed`, `export_failed`, `nothing_to_save` …
	 * From the shell: `not_ready`, `target`, `publish`, `staging`, `save_failed`.
	 */
	code: string;
	message: string;
	file_id: string | null;
}

export interface SavedFile {
	file_id: string;
	path: string;
	name: string;
}

export interface StorageResult {
	entry: Entry;
	directory: string;
	files: SavedFile[];
	/** Files without masked locations; no copy is created. */
	unchanged: string[];
	committed_mappings: number;
	/** The target folder is synchronized (iCloud Drive, File Provider). */
	cloud: boolean;
}

export function istStorageError(value: unknown): value is StorageError {
	return (
		typeof value === 'object' &&
		value !== null &&
		typeof (value as { code?: unknown }).code === 'string'
	);
}

/**
 * "Save all n files". `null` means the folder dialog was cancelled.
 * Errors are returned as `StorageError`.
 */
export function saveEntry(
	entryId: string,
	request: SaveEntryRequest,
	options: {
		folderName: string;
		select: boolean;
		dialogTitle: string;
		tag: string;
		/** Folder of last save: only the initial location for the dialog. */
		lastFolder?: string | null;
	}
): Promise<StorageResult | null> {
	return invoke<StorageResult | null>('save_entry', {
		entryId: entryId,
		request: request,
		folderName: options.folderName,
		choose: options.select,
		dialogTitle: options.dialogTitle,
		tag: options.tag,
		lastDirectory: options.lastFolder ?? null
	});
}

export interface Reimported {
	entry: Entry | null;
	replaced: { file_id: string; new_id: string; changed: boolean }[];
	/** Originals that were unknown and not selected in the dialog. */
	missing: string[];
	failed: string[];
}

/** "Select original": reload legacy files and unreadable copies. */
export function reimportOriginals(
	entryId: string,
	files: { id: string; name: string }[],
	title: string
): Promise<Reimported> {
	return invoke<Reimported>('reload_entry_files', {
		entryId: entryId,
		files: files,
		title: title
	});
}

/** Save the review report to a location chosen in the dialog; `null` = cancelled. */
export function saveTextFile(title: string, name: string, content: string): Promise<string | null> {
	return invoke<string | null>('save_text_file', { title: title, name, content: content });
}

/** Open the target folder in the file manager. */
export function openFolder(path: string): Promise<void> {
	return invoke<void>('reveal_export', { path: path });
}
