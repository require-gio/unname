import { isTauri } from '@tauri-apps/api/core';
import { goto } from '$app/navigation';
import { resolve } from '$app/paths';
import { createEntry, deleteEntry } from '#lib/sidecar/api-v2.js';
import type { Entry, RejectedFile } from '#lib/sidecar/api.gen.js';
import {
	listenDropzone,
	submitSelection,
	selectFiles,
	type Selection
} from '#lib/sidecar/inputs.js';
import { gateNotice } from '#lib/sidecar/gate-notice.js';
import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
import { fetchGeoeffnete } from '#lib/sidecar/system.js';
import type { SidecarConnection } from '#lib/sidecar/types.js';
import { t } from '#lib/state/i18n.svelte.js';
import { start } from '#lib/state/start.svelte.js';
import { history } from '#lib/state/history.svelte.js';
import { spaceReview } from '#lib/history/disk-space.js';

export interface DropzoneMessage {
	text: string;
	rejected: RejectedFile[];
}

/**
 * Drop files or a folder on the start page via dialog or by dragging onto the window.
 *
 * Process: create an entry with start-page masking, submit the selection through the shell,
 * then navigate to the entry. If no file is accepted, delete the empty entry so the history
 * stays clean.
 *
 * While processing is still starting, do not block; just note the pending selection.
 * The shell holds the selection briefly and will submit it once ready. This avoids a
 * double wait for users who drop files immediately after opening.
 */
export class Dropzone {
	/** Something is being dragged over the window. */
	zieht = $state(false);
	busy = $state(false);
	message = $state<DropzoneMessage | null>(null);
	/** Dropped while processing was still starting; will be released with the gate. */
	queued = $state<Selection | null>(null);

	/** Temporary block while processing is starting. */
	get starts(): boolean {
		if (sidecarGate.mayUpload) return false;
		const notice = gateNotice({
			snapshot: sidecarGate.snapshot,
			outside: sidecarGate.outside
		});
		return notice?.position === 'starts';
	}

	async select(folder: boolean): Promise<void> {
		if (this.busy || (!sidecarGate.mayUpload && !this.starts)) return;
		this.message = null;
		let selection: Selection | null;
		try {
			selection = await selectFiles(
				folder,
				t(folder ? 'dropzone.dialog.folder' : 'dropzone.dialog.files')
			);
		} catch {
			this.message = { text: t('dropzone.error'), rejected: [] };
			return;
		}
		if (selection) await this.accept(selection);
	}

	async accept(selection: Selection): Promise<void> {
		if (this.busy) return;
		const connection = sidecarGate.connection;
		if (!connection || !sidecarGate.mayUpload) {
			// Blocked: the drop area already shows the reason; a drop during the block
			// is ignored by the shell. Only the start can be waited for.
			if (this.starts && selection.names.length > 0) {
				this.message = null;
				this.queued = selection;
			}
			return;
		}
		this.message = null;
		if (selection.names.length === 0) {
			this.message = { text: t('dropzone.noFiles'), rejected: [] };
			return;
		}

		this.busy = true;
		void spaceReview(connection, t);
		let entry: Entry | null = null;
		try {
			// No saved default in the window yet (drop right after start):
			// the sidecar provides its default instead of inheriting the initial
			// reversible state.
			entry = await createEntry(connection, {
				kind: 'documents',
				masking: start.presetLoaded ? start.masking.asRequest() : null
			});
			const result = await submitSelection(entry.id, selection.id);
			if (result.accepted.length === 0) {
				await discard(connection, entry.id);
				this.message = {
					text: t('dropzone.allRejected', { n: result.rejected.length }),
					rejected: result.rejected
				};
				return;
			}
			void history.update();
			await goto(resolve('/(app)/entry/[id]', { id: entry.id }), {
				state: {
					inputs: {
						rejected: result.rejected,
						skipped: selection.skipped,
						shortened: selection.truncated
					}
				}
			});
		} catch {
			if (entry) await discard(connection, entry.id);
			this.message = { text: t('dropzone.error'), rejected: [] };
		} finally {
			this.busy = false;
		}
	}

	/**
	 * Submit the pending selection once the gate releases. If the start fails or
	 * resets, discard it with a message; otherwise a stale promise would remain.
	 *
	 * Reads the gate state; called from a `$effect`.
	 */
	nachholen(): void {
		const selection = this.queued;
		if (!selection) return;
		if (sidecarGate.mayUpload && sidecarGate.connection) {
			this.queued = null;
			void this.accept(selection);
		} else if (!this.starts) {
			this.queued = null;
			this.message = { text: t('dropzone.queued.expire'), rejected: [] };
		}
	}

	/**
	 * Complete whatever the menu bar or dock requested (`start.angefordert`).
	 * Reads the state; called from a `$effect`.
	 */
	completeRequest(): void {
		const request = start.angefordert;
		if (!request) return;
		start.angefordert = null;
		if (request === 'opened') void this.acceptGeoeffnete();
		else void this.select(request === 'folder');
	}

	/**
	 * Documents from dock or “Open with”: same path as a drop onto the window.
	 * On cold start they arrive before any listener; the start page therefore also
	 * fetches them once on open.
	 */
	async acceptGeoeffnete(): Promise<void> {
		let selection: Selection | null;
		try {
			selection = await fetchGeoeffnete();
		} catch {
			this.message = { text: t('dropzone.error'), rejected: [] };
			return;
		}
		if (!selection) return;
		start.kind = 'documents';
		await this.accept(selection);
	}

	/** Stop listening for drops over the window and clean up. */
	listen(): () => void {
		if (!isTauri()) return () => {};
		const abmelden = listenDropzone({
			ziehen: (active) => (this.zieht = active),
			abgelegt: (selection) => {
				// Dropping files means documents — also from text mode.
				start.kind = 'documents';
				void this.accept(selection);
			}
		});
		return () => void abmelden.then((ab) => ab());
	}
}

async function discard(connection: SidecarConnection, id: string): Promise<void> {
	try {
		await deleteEntry(connection, id);
	} catch {
		// If it remains, the history retention period will remove it.
	}
}
