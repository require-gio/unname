import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import type { Translator } from '#lib/i18n/translate.js';
import type { ProgressResponse } from './api.gen.js';
import type { Selection } from './inputs.js';

/**
 * The app in the system: menu bar, “Open with”, dock progress,
 * notifications and screen sharing. All runs through the shell
 * (`app/src-tauri/src/menu.rs`, `app_window.rs`, `inputs.rs`);
 * nothing runs outside Tauri.
 */

const MENU = 'menu://action';
const OPENED = 'inputs://opened';

/** Custom menu actions (`menu.rs`, `ACTIONS`). */
export type MenuAction = 'settings' | 'open_files' | 'open_folder' | 'help';

/** Menu bar texts in the app language (`MenuLabels`). */
export interface MenuTexts {
	about: string;
	settings: string;
	services: string;
	hide: string;
	hide_others: string;
	show_all: string;
	quit: string;
	file: string;
	open_files: string;
	open_folder: string;
	close_window: string;
	edit: string;
	undo: string;
	redo: string;
	cut: string;
	copy: string;
	paste: string;
	select_all: string;
	window: string;
	minimize: string;
	zoom: string;
	fullscreen: string;
	help: string;
	help_item: string;
}

/** Load menu texts from the dictionary. */
export function menuTexts(t: Translator): MenuTexts {
	return {
		about: t('menu.about'),
		settings: t('menu.settings'),
		services: t('menu.services'),
		hide: t('menu.hide'),
		hide_others: t('menu.hideOther'),
		show_all: t('menu.showAll'),
		quit: t('menu.finish'),
		file: t('menu.dropzone'),
		open_files: t('menu.selectFiles'),
		open_folder: t('menu.selectFolder'),
		close_window: t('menu.closeWindow'),
		edit: t('menu.edit'),
		undo: t('menu.revoke'),
		redo: t('menu.repeat'),
		cut: t('menu.cut'),
		copy: t('menu.copy'),
		paste: t('menu.insert'),
		select_all: t('menu.selectAll'),
		window: t('menu.window'),
		minimize: t('menu.inDock'),
		zoom: t('menu.zoom'),
		fullscreen: t('menu.fullscreen'),
		help: t('menu.help'),
		help_item: t('menu.showHelp')
	};
}

export async function setMenu(texts: MenuTexts): Promise<void> {
	if (!isTauri()) return;
	await invoke('set_app_menu', { labels: texts });
}

export async function listenMenu(callback: (action: MenuAction) => void): Promise<UnlistenFn> {
	if (!isTauri()) return () => {};
	return listen<{ action: MenuAction }>(MENU, (event) => callback(event.payload.action));
}

/** Documents arrive via dock or “Open with”; retrieve with `geoeffneteAbholen`. */
export async function listenOpened(callback: () => void): Promise<UnlistenFn> {
	if (!isTauri()) return () => {};
	return listen(OPENED, () => callback());
}

/** Native window close and Cmd/Ctrl+Q wait for the final draft writes. */
export async function guardEntryClose(
	flush: () => Promise<void>,
	onError: (error: unknown) => void
): Promise<UnlistenFn> {
	if (!isTauri()) return () => {};
	let flushing = false;
	const unlisten = await listen('draft://flush', async () => {
		if (flushing) return;
		flushing = true;
		let success = false;
		try {
			await flush();
			success = true;
		} catch (error) {
			onError(error);
		} finally {
			try {
				await invoke('finish_entry_flush', { success });
			} finally {
				flushing = false;
			}
		}
	});
	await invoke('set_entry_close_guard', { enabled: true });
	return () => {
		unlisten();
		void invoke('set_entry_close_guard', { enabled: false });
	};
}

/** Pending documents as a selection after a drop; `null` if none are waiting. */
export async function fetchGeoeffnete(): Promise<Selection | null> {
	if (!isTauri()) return null;
	return invoke<Selection | null>('take_opened_inputs');
}

/**
 * Dock share ratio 0…1: average over all files, finished (including failed) count as full.
 * `null` if nothing is running.
 */
export function dockShare(status: ProgressResponse | null): number | null {
	if (!status || status.files.length === 0) return null;
	const running = status.files.some(
		(file) => file.status === 'queued' || file.status === 'processing'
	);
	if (!running) return null;
	const sum = status.files.reduce(
		(sum, file) =>
			sum +
			(file.status === 'queued' || file.status === 'processing'
				? Math.min(Math.max(file.progress, 0), 100)
				: 100),
		0
	);
	return sum / (status.files.length * 100);
}

let lastShare: number | null = null;

/** Progress shown on dock icon; `null` removes the bar. Repeated values are ignored. */
export function dockProgress(share: number | null): void {
	if (!isTauri()) return;
	const gerundet = share === null ? null : Math.round(share * 100) / 100;
	if (gerundet === lastShare) return;
	lastShare = gerundet;
	void invoke('set_job_progress', { fraction: gerundet }).catch(() => {
		// Detection works the same without a bar.
	});
}

/** "`n files done" — the shell only shows it when the window is not in front. */
export function reportDone(title: string, text: string): void {
	if (!isTauri()) return;
	void invoke('notify_finished', { title: title, body: text }).catch(() => {
		// No notification is not an error in detection.
	});
}

/** Window settings, stored in the shell. */
export interface WindowSettings {
	/** Hide in screen shares and screenshots. */
	hide_from_capture: boolean;
}

export async function windowSettings(): Promise<WindowSettings | null> {
	if (!isTauri()) return null;
	return invoke<WindowSettings>('window_settings');
}

export function setWindowSettings(values: WindowSettings): Promise<WindowSettings> {
	return invoke<WindowSettings>('set_window_settings', { settings: values });
}
