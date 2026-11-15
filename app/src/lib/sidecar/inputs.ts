import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import type { Entry, EntryFile, RejectedFile } from './api.gen.js';

/**
 * File input via the shell.
 *
 * The UI never sees a path. The dialog and drop on the window provide an ID
 * and file names; the ID is submitted, the shell reads the files and passes
 * them to the sidecar (`app/src-tauri/src/inputs.rs`).
 */

const ZIEHEN = 'inputs://drag';
const ABGELEGT = 'inputs://dropped';

export interface Selection {
	id: string;
	names: string[];
	/** Skipped in folder: other formats, shortcuts. */
	skipped: number;
	/** More files than an entry can hold; excess omitted. */
	truncated: boolean;
}

/** Combined response from the sidecar for all sub-requests. */
export interface SubmitResult {
	entry: Entry | null;
	accepted: EntryFile[];
	rejected: RejectedFile[];
}

/** System dialog for files or a folder. `null` means cancelled. */
export function selectFiles(folder: boolean, title: string): Promise<Selection | null> {
	return invoke<Selection | null>('pick_inputs', { folder: folder, title: title });
}

export function submitSelection(entryId: string, selection: string): Promise<SubmitResult> {
	return invoke<SubmitResult>('submit_inputs', { entryId: entryId, selection: selection });
}

/** Text from the clipboard; `null` if none. */
export function readClipboard(): Promise<string | null> {
	return invoke<string | null>('read_clipboard_text');
}

/**
 * What is copied determines where the text may go:
 * `restored` contains clear text and stays on macOS on this device,
 * hidden from clipboard managers and short-lived; `masked` is for an AI program,
 * possibly on another device; `plain` contains no content.
 */
export type Kopierart = 'restored' | 'masked' | 'plain';

export interface Copied {
	/** Only on this device, not via Universal Clipboard. */
	local_only: boolean;
	/** After this many seconds the text is cleared. */
	clears_after_s: number | null;
}

/** Write text to the clipboard via the shell; outside Tauri uses the browser. */
export async function writeClipboard(text: string, kind: Kopierart): Promise<Copied> {
	if (!isTauri()) {
		await navigator.clipboard.writeText(text);
		return { local_only: false, clears_after_s: null };
	}
	return invoke<Copied>('copy_text', { text, kind: kind });
}

/**
 * Reports drag over the window and completed drops. Returns the unlisten function.
 */
export async function listenDropzone(callbacks: {
	ziehen: (active: boolean) => void;
	abgelegt: (selection: Selection) => void;
}): Promise<UnlistenFn> {
	const abmelden = await Promise.all([
		listen<{ active: boolean }>(ZIEHEN, (event) => callbacks.ziehen(event.payload.active)),
		listen<Selection>(ABGELEGT, (event) => callbacks.abgelegt(event.payload))
	]);
	return () => abmelden.forEach((ab) => ab());
}
