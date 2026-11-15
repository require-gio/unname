import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchHealth, request, SidecarError } from './client.js';
import type { SidecarConnection } from './types.js';

const CONNECTION: SidecarConnection = {
	base_url: 'unname-api://localhost',
	generation: 1
};

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

function answer(status: number, body?: unknown): Response {
	return new Response(body === undefined ? null : JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});
}

describe('request', () => {
	it('only contacts the shell schema and does not set a token', async () => {
		const fetchMock = vi.fn(async () => answer(200, { status: 'ok' }));
		vi.stubGlobal('fetch', fetchMock);

		await fetchHealth(CONNECTION);

		expect(fetchMock).toHaveBeenCalledWith('unname-api://localhost/health', {});
	});

	it('preserves custom headers and returns undefined for 204', async () => {
		const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
		vi.stubGlobal('fetch', fetchMock);

		const result = await request<void>(CONNECTION, '/entries/e-1', {
			method: 'DELETE',
			headers: { 'Content-Type': 'application/json' }
		});

		expect(result).toBeUndefined();
		expect(fetchMock).toHaveBeenCalledWith('unname-api://localhost/entries/e-1', {
			method: 'DELETE',
			headers: { 'Content-Type': 'application/json' }
		});
	});

	it('propagates the rejection reason, not just the status code', async () => {
		vi.spyOn(console, 'warn').mockImplementation(() => {});
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => answer(422, { detail: 'Die Datei ist leer.' }))
		);

		await expect(request(CONNECTION, '/entries')).rejects.toMatchObject({
			name: 'SidecarError',
			message: 'Die Datei ist leer.',
			status: 422
		});
	});

	it('extracts code and index from a structured rejection', async () => {
		vi.spyOn(console, 'warn').mockImplementation(() => {});
		vi.stubGlobal(
			'fetch',
			vi.fn(async () =>
				answer(422, { detail: { code: 'pattern_invalid', message: 'Kein Muster.', index: 2 } })
			)
		);

		const error = await request(CONNECTION, '/patterns').catch((e: unknown) => e);
		expect(error).toBeInstanceOf(SidecarError);
		expect(error).toMatchObject({ code: 'pattern_invalid', index: 2, message: 'Kein Muster.' });
	});

	it('uses the response location when body is not readable', async () => {
		vi.spyOn(console, 'warn').mockImplementation(() => {});
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => new Response('kaputt', { status: 503 }))
		);

		const error = (await request(CONNECTION, '/entries').catch((e: unknown) => e)) as Error;
		expect(error.message).not.toContain('/entries');
		expect(error).toMatchObject({ status: 503 });
	});

	it('converts a network error to SidecarError without technical details', async () => {
		vi.spyOn(console, 'warn').mockImplementation(() => {});
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => {
				throw new TypeError('Failed to fetch');
			})
		);

		const error = (await request(CONNECTION, '/health').catch((e: unknown) => e)) as Error;
		expect(error).toBeInstanceOf(SidecarError);
		expect(error.message).not.toContain('Failed to fetch');
	});

	it('passes through an intentionally aborted request unchanged', async () => {
		const abort = new AbortController();
		abort.abort();
		const cause = new DOMException('abgebrochen', 'AbortError');
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => {
				throw cause;
			})
		);

		await expect(request(CONNECTION, '/text/mask', { signal: abort.signal })).rejects.toBe(cause);
	});
});
