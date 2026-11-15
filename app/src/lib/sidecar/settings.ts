import { invoke } from '@tauri-apps/api/core';
import type { ListsResponse, VaultStatusResponse } from './api.gen.js';

/**
 * Settings files via the shell (`app/src-tauri/src/settings_io.rs`):
 * table backup, name lists, template files. The UI only provides titles and
 * filters for dialogs in the app language—never a path. `null` means cancelled.
 */

/** What the shell reports on error; `code` is translated for the UI. */
export interface SettingsError {
	/**
	 * From sidecar: `password_too_short`, `wrong_password`, `invalid_backup`,
	 * `list_format`, `list_empty`, `list_unsupported`, `list_encoding`,
	 * `too_large` … From the shell: `not_ready`, `unreadable`,
	 * `file_too_large`, `encoding`, `write`, `invalid`.
	 */
	code: string;
	message: string;
}

export function istSettingsError(value: unknown): value is SettingsError {
	return (
		typeof value === 'object' &&
		value !== null &&
		typeof (value as { code?: unknown }).code === 'string'
	);
}

export interface Dialog {
	title: string;
	/** Label for the file filter in the system dialog. */
	filter: string;
}

/** “Export backup”: encrypted with the password to a chosen location. */
export function saveBackup(password: string, title: string, name: string): Promise<string | null> {
	return invoke<string | null>('save_vault_backup', { password: password, title: title, name });
}

/** “Import”: replaces the device's key and table. */
export function importBackup(
	password: string,
	dialog: Dialog
): Promise<VaultStatusResponse | null> {
	return invoke<VaultStatusResponse | null>('import_vault_backup', {
		password: password,
		title: dialog.title,
		filter: dialog.filter
	});
}

export function addList(
	kind: 'persons' | 'organizations',
	dialog: Dialog
): Promise<ListsResponse | null> {
	return invoke<ListsResponse | null>('add_name_list', {
		kind: kind,
		title: dialog.title,
		filter: dialog.filter
	});
}

/** “Refresh now”: the shell re-reads the source or prompts for it. */
export function updateList(id: string, dialog: Dialog): Promise<ListsResponse | null> {
	return invoke<ListsResponse | null>('refresh_name_list', {
		id,
		title: dialog.title,
		filter: dialog.filter
	});
}

export function readTemplate(dialog: Dialog): Promise<{ name: string; content: string } | null> {
	return invoke<{ name: string; content: string } | null>('read_template_file', {
		title: dialog.title,
		filter: dialog.filter
	});
}
