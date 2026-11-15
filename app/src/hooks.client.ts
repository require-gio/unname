import type { HandleClientError } from '@sveltejs/kit/hooks';

/**
 * Log errors without exposing their content.
 *
 * Kit 3 forwards every error here, including render errors and `error(...)`.
 * The default handler prints the full error object and message to the console,
 * which may contain document text. This handler only logs the kind, error class
 * or status, and the route.
 *
 * The UI behavior is unchanged: Kit uses the error's status and message,
 * defaulting to 500 and "Internal Error" for unknown errors.
 */
export const handleError: HandleClientError = ({ kind, error, event }) => {
	const merkmal =
		kind === 'unknown'
			? error instanceof Error
				? error.name
				: typeof error
			: String(error.status);
	console.error(`Fehler (${kind}, ${merkmal}) in Route ${event.route.id ?? '–'}`);
};
