import { toast } from 'svelte-sonner';
import { errorSentence } from '#lib/settings/dialog.js';
import { SidecarError } from '#lib/sidecar/client.js';
import { istSettingsError } from '#lib/sidecar/settings.js';
import { sidecarGate } from '#lib/sidecar/gate.svelte.js';
import type { SidecarConnection } from '#lib/sidecar/types.js';
import { settings } from '#lib/state/settings.svelte.js';
import { t } from '#lib/state/i18n.svelte.js';

/**
 * Common handling for settings areas: “Changes apply immediately” —
 * otherwise show why they can’t be saved. Errors come as codes from
 * Sidecar or the shell and are turned into sentences (`errorSentence`).
 */

/** The connection – or a notice that nothing can be saved right now. */
export function gateConnection(): SidecarConnection | null {
	const connection = sidecarGate.connection;
	if (!connection) toast.error(t('settings.error.noConnection'));
	return connection;
}

/** Change a part of the settings; returns `false` if not saved. */
export async function change(part: Parameters<typeof settings.change>[1]): Promise<boolean> {
	const connection = gateConnection();
	if (!connection) return false;
	try {
		await settings.change(connection, part);
		return true;
	} catch {
		toast.error(t('settings.error.notSaved'));
		return false;
	}
}

/** Error code from Sidecar (`detail.code`) or the shell (`SettingsFailure`). */
export function errorCode(error: unknown): string | undefined {
	if (error instanceof SidecarError) return error.code;
	if (istSettingsError(error)) return error.code;
	return undefined;
}

/** Show the error as a sentence; unknown means “currently not possible”. */
export function showError(error: unknown): void {
	toast.error(errorSentence(errorCode(error), t));
}
