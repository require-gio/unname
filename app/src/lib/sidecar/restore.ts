import { invoke } from '@tauri-apps/api/core';
import type { RestoreFileResponse } from './api.gen.js';

/**
 * Restore files via the shell.
 *
 * Like saving, the UI does not know a path: the KI file is identified from
 * a dialog or dropzone (`eingang.ts`), the shell reads it and passes it to the
 * sidecar. The shell writes the new file with real names to the location the
 * user selects in the save dialog (`app/src-tauri/src/restoring.rs`).
 */

export interface FileRestoration extends RestoreFileResponse {
	/** Count of additional files in the selection that were not processed. */
	skipped: number;
}

/** Restore the first file of a selection. Errors are returned as `{ code, message }`. */
export function restoreFile(
	selection: string,
	entryId: string | null,
	aliases: boolean
): Promise<FileRestoration> {
	return invoke<FileRestoration>('restore_file', {
		selection: selection,
		entryId: entryId,
		surrogates: aliases
	});
}

/**
 * “Save as …”: path of the new file, `null` = dialog cancelled.
 *
 * If the target is in iCloud Drive or another cloud service, the shell does not
 * write directly but returns `cloud_target` and remembers the target. With
 * `cloudConfirmed` it writes there without prompting again.
 */
export function saveRestoredFile(
	id: string,
	title: string,
	cloudConfirmed = false
): Promise<string | null> {
	return invoke<string | null>('save_restored_file', {
		id,
		title: title,
		confirmCloud: cloudConfirmed
	});
}

/** “Open” – only a file that the shell has just saved itself. */
export function openSavedFile(path: string): Promise<void> {
	return invoke<void>('open_restored_file', { path: path });
}
