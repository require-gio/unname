import AxeBuilder from '@axe-core/playwright';
import { expect, type BrowserContext, type Locator, type Page, type Route } from '@playwright/test';

/**
 * Simulated shell and sidecar for smoke tests of the new UI.
 *
 * `__TAURI_INTERNALS__` answers shell commands (gate, file picker,
 * submit, clipboard) and emits events via `__EMIT__`; HTTP calls to the sidecar
 * are intercepted by `context.route`. The test verifies what the UI does with
 * those calls and what it sends back to the sidecar.
 */

export const APP_ORIGIN = 'http://127.0.0.1:4173';
export const SIDECAR_ORIGIN = 'http://127.0.0.1:51234';

export const STANDARD = {
	method: 'reversible',
	style: 'codes',
	categories: ['finance_ids', 'names', 'addresses', 'contact', 'numbers']
};
export const JOB = { ...STANDARD, anonymize: false };

export const SETTINGS = {
	schema_version: 3,
	general: { language: 'de', theme: 'system' },
	masking: STANDARD,
	document_languages: {
		languages: ['de', 'en'],
		per_paragraph: true,
		countries: ['DE', 'AT'],
		placeholders_in_document_language: true
	},
	second_opinion: { enabled: true, policy: 'protect', keep_threshold: 0.95 },
	names_terms: { template: null },
	history: { retention_days: 30 },
	vault: { unused_retention_months: 12 },
	technical: { developer_mode: false }
};

const NOW = new Date().toISOString();

export function entry(part: Record<string, unknown>) {
	return {
		id: 'e-1',
		title: 'Angebot',
		title_custom: false,
		kind: 'documents',
		status: 'processing',
		created_at: NOW,
		updated_at: NOW,
		masking: JOB,
		files: [],
		review: {},
		...part
	};
}

/** `GET /storage` returns a list of entries: ~1.2 MiB each, 30-day retention. */
export function storage(
	list: readonly { id: string; updated_at: string }[],
	part: Record<string, unknown> = {}
) {
	const size = Math.round(1.2 * 1024 * 1024);
	return {
		total_bytes: size * list.length,
		free_bytes: 50 * 1024 ** 3,
		low_space_bytes: 2 * 1024 ** 3,
		retention_days: 30,
		entries: list.map((item) => ({
			entry_id: item.id,
			bytes: size,
			delete_at: new Date(Date.parse(item.updated_at) + 30 * 86_400_000).toISOString()
		})),
		...part
	};
}

export const FILE = {
	id: 'j-1',
	name: 'Angebot_Hofer.pdf',
	format: 'pdf',
	size_bytes: 340 * 1024,
	pages: 2,
	ocr_used: false,
	status: 'processing',
	error_code: null,
	counts: {},
	label: null,
	output_path: null
};

function model(
	id: string,
	kind: string,
	size: number,
	next: Record<string, unknown> = {}
): Record<string, unknown> {
	return {
		id,
		kind,
		origin: `${id}-source`,
		license: 'Apache-2.0',
		download_size: size,
		installed_size: size,
		state: 'missing',
		received: 0,
		installed_at: null,
		checked_at: null,
		error: null,
		files: [
			{
				path: `${id}.daten`,
				size: size,
				sha256: 'ab'.repeat(32),
				url: `https://huggingface.co/unname/modelle/resolve/v1/${id}/${id}.daten`
			}
		],
		...next
	};
}

/** Shell overview before initial setup (sizes as in manifest). */
export const MODELS = {
	revision: '2026-10-24',
	sources: ['github', 'huggingface'],
	choice: { source: 'huggingface', custom_url: null },
	busy: false,
	components: [
		model('ner', 'ner', 597_386_105),
		model('de', 'language', 14_639_490, { installed_size: 15_300_000 }),
		model('en', 'language', 12_806_118, { installed_size: 13_400_000 }),
		model('ocr', 'ocr', 24_029_062),
		model('classifier', 'classifier', 135_327_342, { license: 'MIT' })
	]
};

/** Same overview after setup. */
export const MODELS_INSTALLED = {
	...MODELS,
	components: MODELS.components.map((part) => ({
		...part,
		state: 'installed',
		received: part.download_size,
		installed_at: '2026-10-24T09:00:00Z',
		checked_at: '2026-10-24T09:12:00Z'
	}))
};

export interface Capture {
	commands: { command: string; args: Record<string, unknown> }[];
	/** `suche`: the query string including `?` (e.g., the image level of a page image). */
	requests: { method: string; path: string; search: string; body: unknown }[];
}

/** Response without body (204), e.g., for `DELETE`. */
export const WITHOUT_CONTENT = Symbol('204');

/** Response with raw body instead of JSON – e.g., a page image. */
export class Roh {
	constructor(
		readonly body: Buffer,
		readonly type: string
	) {}
}

export interface Szenario {
	ready?: boolean;
	entries?: () => unknown[];
	/** Custom response; `undefined` means default or 500. */
	sidecar?: (method: string, path: string, body: unknown, search: string) => unknown;
	/** Response from the shell for `submit_inputs`. */
	submit?: unknown;
	/** Response from the shell for `save_entry`; omitted means cancelled. */
	save?: unknown;
	/** Response from the shell for `restore_file`. */
	restore?: unknown;
	/**
	 * Responses from the shell for settings commands:
	 * `save_vault_backup`, `import_vault_backup`, `add_name_list`,
	 * `refresh_name_list`, `read_template_file`. Omitted means cancelled.
	 */
	settings?: Record<string, unknown>;
	/**
	 * Phase I: initial setup pending (`setup_required`), dropping is locked.
	 * `models` is the shell's overview (`models_overview`),
	 * `model_package` is the response to `models_import_package` – an object where
	 * `error` causes the command to fail with that code.
	 */
	setup?: boolean;
	models?: unknown;
	modelPackage?: unknown;
	/**
	 * Phase L (E15): first launch – the home page shows the notices automatically.
	 * If omitted, treat as already shown so notices do not interfere with later flow.
	 */
	firstStart?: boolean;
}

export async function harness(
	context: BrowserContext,
	szenario: Szenario = {}
): Promise<{ capture: Capture; entries: unknown[] }> {
	const capture: Capture = { commands: [], requests: [] };
	const entries: unknown[] = [];
	const views = new Map<string, unknown>();

	await context.exposeBinding('__command__', (_source, command: string, args: string) => {
		capture.commands.push({ command, args: JSON.parse(args) });
	});

	await context.addInitScript(
		({ ready: initial, setup }) => {
			let ready = initial;
			const callbacks = new Map<number, (payload: unknown) => void>();
			const listener = new Map<string, number[]>();
			let next = 1;
			const runtime = window as unknown as Record<string, unknown>;
			Object.defineProperty(globalThis, 'isTauri', { value: true, configurable: true });

			const snapshot = () => ({
				status: ready
					? { state: 'ready', port: 51234, pid: 4242, version: '0.1.0' }
					: { state: 'starting' },
				restarts: 0,
				changed_at: '2026-10-23T09:00:00Z',
				can_upload: ready && !setup,
				can_export: ready,
				setup_required: ready ? setup : null
			});
			const models = () => runtime.__models__ as Record<string, unknown>;

			runtime.__TAURI_INTERNALS__ = {
				transformCallback(callback: (payload: unknown) => void) {
					const id = next++;
					callbacks.set(id, callback);
					return id;
				},
				unregisterCallback(id: number) {
					callbacks.delete(id);
				},
				async invoke(command: string, args: Record<string, unknown> = {}) {
					const report = (runtime.__command__ as (b: string, a: string) => Promise<void>) ?? null;
					if (report && !command.startsWith('plugin:event')) {
						await report(command, JSON.stringify(args));
					}
					switch (command) {
						case 'plugin:event|listen': {
							const name = String(args.event);
							listener.set(name, [...(listener.get(name) ?? []), Number(args.handler)]);
							return Number(args.handler);
						}
						case 'plugin:event|unlisten':
							return null;
						case 'sidecar_status':
							return snapshot();
						case 'sidecar_connection':
							return ready ? { base_url: 'http://127.0.0.1:51234', token: 'token' } : null;
						case 'diagnostics_report':
							return { entries: [], count: 0 };
						case 'pick_inputs':
							return {
								id: 'auswahl-1',
								names: ['Angebot_Hofer.pdf', 'Notiz.exe'],
								skipped: 0,
								truncated: false
							};
						case 'submit_inputs':
							return (runtime.__submit__ as () => unknown)();
						case 'save_entry':
							return (runtime.__save__ as () => unknown)();
						case 'reload_entry_files':
							return { entry: null, replaced: [], missing: [], failed: [] };
						case 'save_text_file':
							return '/Users/test/Prüfbericht.txt';
						case 'restore_file':
							return (runtime.__restore__ as () => unknown)();
						case 'save_restored_file':
							return '/Users/test/Downloads/Zahlungserinnerung_Entwurf_mit_Namen.docx';
						case 'open_restored_file':
							return null;
						case 'reveal_export':
							return null;
						case 'save_vault_backup':
						case 'import_vault_backup':
						case 'add_name_list':
						case 'refresh_name_list':
						case 'read_template_file':
							return (runtime.__settings__ as Record<string, unknown>)[command] ?? null;
						case 'models_overview':
							return models();
						case 'models_set_source':
							runtime.__models__ = {
								...models(),
								choice: { source: args.source, custom_url: args.customUrl ?? null }
							};
							return models();
						case 'models_download':
						case 'models_cancel':
							return null;
						case 'models_import_package': {
							const answer = runtime.__modelPackage__ as { error?: string } | null;
							if (answer?.error) throw { code: answer.error, message: '' };
							if (answer) runtime.__models__ = answer;
							return answer;
						}
						case 'models_verify':
							return { checked_at: '2026-10-24T09:12:00Z', damaged: [], overview: models() };
						case 'models_remove': {
							const overview = models();
							runtime.__models__ = {
								...overview,
								components: (overview.components as { id: string }[]).map((part) =>
									part.id === args.component ? { ...part, state: 'missing' } : part
								)
							};
							return models();
						}
						case 'read_clipboard_text':
							return 'Hallo Frau Hofer,\n\nviele Grüße\nJosef Brandstetter';
						default:
							throw new Error(`Unerwartet: ${command}`);
					}
				}
			};

			// `unlisten` of the Tauri API calls the event plugin directly.
			runtime.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };

			runtime.__EMIT__ = (name: string, payload: unknown) => {
				for (const id of listener.get(name) ?? []) {
					callbacks.get(id)?.({ event: name, id: 1, payload });
				}
			};

			// Startup complete: shell reports new state as in production.
			runtime.__FREIGEBEN__ = () => {
				ready = true;
				(runtime.__EMIT__ as (name: string, payload: unknown) => void)(
					'sidecar://status',
					snapshot()
				);
			};
		},
		{ ready: szenario.ready ?? true, setup: szenario.setup ?? false }
	);

	await context.addInitScript(
		({ firstStart }) => {
			if (!firstStart && localStorage.getItem('unname.help.shown') === null) {
				localStorage.setItem('unname.help.shown', '1');
			}
		},
		{ firstStart: szenario.firstStart ?? false }
	);

	await context.addInitScript(
		({ overview, bundle }) => {
			const runtime = window as unknown as Record<string, unknown>;
			runtime.__models__ = overview;
			runtime.__modelPackage__ = bundle;
		},
		{ overview: szenario.models ?? MODELS, bundle: szenario.modelPackage ?? null }
	);

	await context.addInitScript(
		({ saved }) => {
			(window as unknown as Record<string, unknown>).__save__ = () => saved;
		},
		{ saved: szenario.save ?? null }
	);

	await context.addInitScript(
		({ answers }) => {
			(window as unknown as Record<string, unknown>).__settings__ = answers;
		},
		{ answers: szenario.settings ?? {} }
	);

	await context.addInitScript(
		({ file }) => {
			(window as unknown as Record<string, unknown>).__restore__ = () => file;
		},
		{ file: szenario.restore ?? null }
	);

	await context.addInitScript(
		({ answer }) => {
			(window as unknown as Record<string, unknown>).__submit__ = () => answer;
		},
		{
			answer: szenario.submit ?? {
				entry: { ...entry({}), files: [FILE] },
				accepted: [FILE],
				rejected: [{ name: 'Notiz.exe', code: 'unsupported_file_format', message: 'x' }]
			}
		}
	);

	const json = (route: Route, body: unknown, status = 200) =>
		route.fulfill({
			status,
			contentType: 'application/json',
			headers: { 'Access-Control-Allow-Origin': '*' },
			body: JSON.stringify(body)
		});

	await context.route(`${SIDECAR_ORIGIN}/**`, async (route) => {
		const request = route.request();
		const { pathname: path, search: search } = new URL(request.url());
		const method = request.method();
		const body = request.postData() ? request.postDataJSON() : null;
		capture.requests.push({ method, path, search, body });
		const own = szenario.sidecar?.(method, path, body, search);
		if (own === WITHOUT_CONTENT) {
			return route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*' } });
		}
		if (own instanceof Roh) {
			return route.fulfill({
				status: 200,
				contentType: own.type,
				headers: { 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' },
				body: own.body
			});
		}
		if (own !== undefined) return json(route, own);
		if (/^\/entries\/[^/]+\/view$/.test(path)) {
			if (method === 'PUT') views.set(path, body);
			return json(route, views.get(path) ?? null);
		}
		if (path === '/health') return json(route, { status: 'ok' });
		if (path === '/settings' && method === 'GET') return json(route, SETTINGS);
		if (path === '/settings' && method === 'PUT') {
			return json(route, { ...SETTINGS, ...(body as object) });
		}
		if (path === '/entries' && method === 'GET') {
			const list = szenario.entries?.() ?? entries;
			return json(route, { entries: list, count: list.length });
		}
		// Current state: occupied space per entry and "redetect" feature –
		// without a custom scenario each detection matches the settings.
		if (path === '/storage' && method === 'GET') {
			const list = (szenario.entries?.() ?? entries) as { id: string; updated_at: string }[];
			return json(route, storage(list));
		}
		const redetect = /^\/entries\/([^/]+)\/redetect$/.exec(path);
		if (redetect && method === 'GET') return json(route, { entry_id: redetect[1], files: [] });
		return json(route, { detail: path }, 500);
	});

	return { capture, entries };
}

export async function open(page: Page, path = '/'): Promise<void> {
	await page.goto(`${APP_ORIGIN}${path}`, { waitUntil: 'networkidle' });
}

/**
 * Verify board rendering in both light and dark mode using axe (WCAG 2.1 AA).
 *
 * axe runs offline: `@axe-core/playwright` injects the rule set from
 * `node_modules` into the page, no network requests. The harness follows the
 * system theme (`theme: system`), so switching the color scheme is sufficient.
 * The `mask` option hides time stamps from visual diff; axe still evaluates the
 * masked areas.
 */
export async function board(
	page: Page,
	name: string,
	options: { mask?: Locator[]; fromClose?: string[] } = {}
): Promise<void> {
	for (const [schema, extension] of [
		['light', 'light'],
		['dark', 'dark']
	] as const) {
		await page.emulateMedia({ colorScheme: schema });
		if (schema === 'dark') await expect(page.locator('html')).toHaveClass(/\bdark\b/);
		else await expect(page.locator('html')).not.toHaveClass(/\bdark\b/);
		await page.evaluate(async () => document.fonts.ready);

		let review = new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']);
		for (const selektor of options.fromClose ?? []) review = review.exclude(selektor);
		const { violations } = await review.analyze();
		expect(
			violations.map((verstoss) => ({
				rule: verstoss.id,
				impact: verstoss.impact,
				places: verstoss.nodes.map((node) => node.target.join(' ')).slice(0, 5)
			})),
			`axe: ${name} (${extension})`
		).toEqual([]);

		await expect(page).toHaveScreenshot(`board-${name}-${extension}.png`, {
			caret: 'hide',
			mask: options.mask
		});
	}
	await page.emulateMedia({ colorScheme: 'light' });
}
