import type { HealthReport, SidecarConnection } from './types.js';

/**
 * Calls to the sidecar.
 *
 * The address is provided by the shell via the `unname-api` scheme. The shell
 * forwards every request to the sidecar and injects the token; the UI never
 * sees the token or port. If the connection is missing, there is no address
 * and the sidecar cannot be reached (spec §2).
 *
 * The Content Security Policy only allows `connect-src` to `'self'` and this
 * scheme—network requests fail in the WebView before any code check.
 */

export class SidecarError extends Error {
	constructor(
		message: string,
		readonly status?: number,
		/** `detail.code` of the rejection if the service provides one. */
		readonly code?: string,
		/** `detail.index`: which entry in a list was rejected. */
		readonly index?: number
	) {
		super(message);
		this.name = 'SidecarError';
	}
}

export async function request<T>(
	connection: SidecarConnection,
	path: string,
	init: RequestInit = {}
): Promise<T> {
	const response = await answer(connection, path, init);
	if (response.status === 204) return undefined as T;
	return (await response.json()) as T;
}

/**
 * Like `request` but returns the body as a `Blob`—used for page images.
 * They are processed with `createImageBitmap` onto a canvas, never via a
 * `blob:` URL; the CSP remains unchanged.
 */
export async function requestBlob(
	connection: SidecarConnection,
	path: string,
	init: RequestInit = {}
): Promise<Blob> {
	return (await answer(connection, path, init)).blob();
}

async function answer(
	connection: SidecarConnection,
	path: string,
	init: RequestInit
): Promise<Response> {
	let response: Response;
	try {
		response = await fetch(`${connection.base_url}${path}`, init);
	} catch (cause) {
		// An intentionally aborted call (text mode: new input) is not a failure
		// and should not be logged or shown to the user.
		if (init.signal?.aborted) throw cause;
		// The cause belongs in the log, not in the user-facing message.
		// „TypeError: Failed to fetch" does not answer a question a user at
		// the desk might have.
		console.warn(`Aufruf an ${path} fehlgeschlagen:`, cause);
		throw new SidecarError('Die lokale Verarbeitung antwortet gerade nicht.');
	}

	if (!response.ok) {
		const reason = await errorReason(response, path);
		throw new SidecarError(reason.text, response.status, reason.code, reason.index);
	}
	return response;
}

/**
 * Full rejection reason, not just the status code.
 *
 * The service may include a reason in the `detail` field—as plain text or as
 * `{code, message}`. Previously only the status code was shown, e.g.
 * “responded with 503 on /entries”, while the actual explanation
 * ("the name list could not be read") was in the body and lost.
 * If no explanation is present, the fallback text describes the situation,
 * not the endpoint: the path is useful for debugging and is logged.
 */
async function errorReason(
	response: Response,
	path: string
): Promise<{ text: string; code?: string; index?: number }> {
	const replacement =
		response.status >= 500
			? 'Die lokale Verarbeitung konnte die Anfrage nicht abschliessen.'
			: 'Die Anfrage wurde abgelehnt.';
	console.warn(`Aufruf an ${path} beantwortet mit ${response.status}.`);
	try {
		const body: unknown = await response.json();
		const detail = (body as { detail?: unknown })?.detail;
		if (typeof detail === 'string' && detail.trim()) return { text: detail };
		const { message, code, index } = (detail ?? {}) as {
			message?: unknown;
			code?: unknown;
			index?: unknown;
		};
		return {
			text: typeof message === 'string' && message.trim() ? message : replacement,
			code: typeof code === 'string' ? code : undefined,
			index: typeof index === 'number' ? index : undefined
		};
	} catch {
		// A response without a readable body is not a second error.
		return { text: replacement };
	}
}

export function fetchHealth(connection: SidecarConnection): Promise<HealthReport> {
	return request<HealthReport>(connection, '/health');
}
