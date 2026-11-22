import { expect, test, type Page } from '@playwright/test';
import {
	APP_ORIGIN,
	MODELS,
	WITHOUT_CONTENT,
	SETTINGS,
	board,
	entry,
	harness,
	open
} from './harness.js';

/**
 * Settings dialog (board “Einstellungen”) with selectable document languages.
 *
 * The shell and sidecar are simulated (`huelle.ts`). This test verifies the UI
 * behavior and the requests sent to sidecar and shell.
 */

const VAULT = {
	state: 'ready',
	key_fingerprint: 'abc123def456',
	warning: 'x',
	created_at: '2026-08-02T08:00:00+00:00',
	mapping_count: 418,
	entry_count: 12,
	mapping_persisted: true
};

const LISTEN = {
	lists: [
		{
			id: 'l1',
			kind: 'persons',
			source_name: 'kunden.csv',
			count: 1284,
			imported_at: new Date().toISOString(),
			updated_at: new Date().toISOString(),
			stale: false
		},
		{
			id: 'l2',
			kind: 'organizations',
			source_name: 'lieferanten.csv',
			count: 312,
			imported_at: '2026-10-15T08:00:00Z',
			updated_at: '2026-10-15T08:00:00Z',
			stale: true
		}
	],
	configured: null
};

const TEMPLATES = {
	templates: [
		{
			id: 'beispiel-versicherung',
			title: 'Versicherung',
			example: true,
			terms: 0,
			patterns: 2,
			masking: null
		}
	],
	active: null
};

const PATTERN = [
	{
		id: 'pol',
		name: 'Polizzennummer',
		regex: 'POL-\\d{6,8}',
		entity_type: 'CUSTOMER_NUMBER',
		enabled: true
	}
];

function dialog(page: Page) {
	return page.getByRole('dialog', { name: 'Einstellungen' });
}

test('General: language and appearance apply immediately, "restore" closes', async ({
	context,
	page
}) => {
	const { capture } = await harness(context);
	await open(page);

	await page.getByRole('button', { name: 'Einstellungen' }).click();
	const settings = dialog(page);
	await expect(settings).toBeVisible();
	await expect(settings.getByRole('heading', { name: 'Allgemein', level: 2 })).toBeVisible();
	await expect(settings.getByText('Änderungen gelten sofort.')).toBeVisible();
	// The address stays; the dialog is attached to history.
	await expect(page).toHaveURL(`${APP_ORIGIN}/`);

	await settings.getByRole('radio', { name: /Dunkel/ }).click();
	await expect(page.locator('html')).toHaveClass(/dark/);
	await settings.getByRole('radio', { name: /English/ }).click();
	await expect(page.locator('html')).toHaveAttribute('lang', 'en');
	await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Masking', exact: true })).toBeVisible();
	// English UI is fully functional, including the home page behind the dialog.
	await expect(page.getByRole('button', { name: 'Adjust masking' })).toBeVisible();
	const puts = capture.requests.filter((a) => a.path === '/settings' && a.method === 'PUT');
	expect(puts.map((a) => a.body)).toEqual([
		{ general: { theme: 'dark' } },
		{ general: { language: 'en' } }
	]);

	await page.goBack();
	await expect(page.getByRole('dialog')).toHaveCount(0);
	await expect(page).toHaveURL(`${APP_ORIGIN}/`);

	// Escape also closes.
	await page.getByRole('button', { name: 'Settings' }).click();
	await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('Masking: default with example sentence, also from home popover', async ({
	context,
	page
}) => {
	const { capture } = await harness(context);
	await open(page);

	await page.getByRole('button', { name: 'Maskierung anpassen' }).click();
	await page.getByRole('button', { name: 'Mehr Einstellungen' }).click();
	const settings = dialog(page);
	await expect(settings.getByRole('heading', { name: 'Maskierung', level: 2 })).toBeVisible();
	// Appears in the card example and with the replacement style “Codes” (always visible).
	await expect(settings.getByText('<<PERSON_ba903855>>', { exact: true })).toHaveCount(2);
	await expect(settings.getByRole('radio', { name: /Schwärzen/ })).toHaveCount(0);

	await settings.getByRole('radio', { name: /Irreversibel/ }).click();
	// In the example sentence and with the replacement style “Placeholder”.
	await expect(settings.getByText('[Name]', { exact: true })).toHaveCount(2);
	await expect
		.poll(() =>
			capture.requests
				.filter((a) => a.path === '/settings' && a.method === 'PUT')
				.map((a) => (a.body as { masking?: { method: string; style: string } }).masking)
		)
		.toContainEqual(expect.objectContaining({ method: 'irreversible', style: 'placeholder' }));

	// Replacement style without expanding: redaction becomes default.
	await settings.getByRole('radio', { name: /Schwärzen/ }).click();
	await expect
		.poll(() =>
			capture.requests
				.filter((a) => a.path === '/settings' && a.method === 'PUT')
				.map((a) => (a.body as { masking?: { method: string; style: string } }).masking)
		)
		.toContainEqual(expect.objectContaining({ method: 'irreversible', style: 'mask' }));
	await settings.getByRole('radio', { name: /^Platzhalter/ }).click();

	await settings.getByRole('button', { name: /Orte/ }).click();
	await expect(settings.getByText('[Ort]', { exact: true })).toBeVisible();
});

test('Document languages: per paragraph, languages, load language pack, countries, placeholders', async ({
	context,
	page
}) => {
	const { capture } = await harness(context, {
		models: {
			...MODELS,
			components: MODELS.components.map((part) =>
				part.id === 'en' ? part : { ...part, state: 'installed' }
			)
		},
		sidecar: (method, path) =>
			path === '/models' && method === 'GET'
				? {
						components: [
							{ id: 'ner', state: 'ready', source: 'download', size_bytes: 1, languages: [] },
							{ id: 'de', state: 'ready', source: 'download', size_bytes: 1, languages: ['de'] },
							{ id: 'en', state: 'missing', source: 'download', size_bytes: null, languages: [] },
							{ id: 'ocr', state: 'missing', source: 'download', size_bytes: null, languages: [] }
						],
						total_bytes: 2,
						setup_required: false
					}
				: undefined
	});
	const puts = () => capture.requests.filter((a) => a.method === 'PUT').map((a) => a.body);
	await open(page);
	await page.getByRole('button', { name: 'Einstellungen' }).click();
	const settings = dialog(page);
	await settings.getByRole('button', { name: 'Dokumentsprachen', exact: true }).click();

	await settings
		.getByRole('switch', { name: 'Sprache jedes Absatzes automatisch erkennen' })
		.click();
	await expect.poll(puts).toContainEqual({ document_languages: { per_paragraph: false } });

	// English is missing on this device: load via the shell.
	await expect(settings.getByText('installiert')).toHaveCount(1);
	await expect(settings.getByText(/Paket fehlt/)).toBeVisible();
	await settings.getByRole('button', { name: 'Sprachpaket laden …' }).click();
	await expect
		.poll(() => capture.commands.find((b) => b.command === 'models_download')?.args)
		.toEqual({ components: ['en'] });

	// Turn off English; the last language stays enabled.
	await settings.getByRole('switch', { name: 'Dokumente auf English prüfen' }).click();
	await expect.poll(puts).toContainEqual({ document_languages: { languages: ['de'] } });
	await expect(
		settings.getByRole('switch', { name: 'Dokumente auf Deutsch prüfen' })
	).toBeDisabled();

	// Countries: disable Austria, keep Germany as last.
	await expect(settings.getByText('Schweiz · folgt')).toBeVisible();
	await settings.getByRole('button', { name: 'Österreich' }).click();
	await expect.poll(puts).toContainEqual({ document_languages: { countries: ['DE'] } });

	await settings
		.getByRole('switch', {
			name: 'Platzhalter und erfundene Namen in der Sprache des Dokuments'
		})
		.click();
	await expect
		.poll(puts)
		.toContainEqual({ document_languages: { placeholders_in_document_language: false } });
	expect(
		puts().some((body) => {
			const part = (body as { document_languages?: Record<string, unknown[]> }).document_languages;
			return part?.languages?.length === 0 || part?.countries?.length === 0;
		})
	).toBe(false);
});

test('Second opinion: on/off, policy, load model, reviewer workflow', async ({ context, page }) => {
	const { capture } = await harness(context, {
		models: {
			...MODELS,
			components: MODELS.components.map((part) =>
				part.id === 'classifier' ? part : { ...part, state: 'installed' }
			)
		}
	});
	const puts = () => capture.requests.filter((a) => a.method === 'PUT').map((a) => a.body);
	await open(page);
	await page.getByRole('button', { name: 'Einstellungen' }).click();
	const settings = dialog(page);
	await settings.getByRole('button', { name: 'Zweitmeinung (KI)' }).click();

	const toggleSwitch = settings.getByRole('switch', {
		name: 'Zweitmeinung bei unsicheren Stellen'
	});
	await expect(toggleSwitch).toBeChecked();
	await toggleSwitch.click();
	await expect.poll(puts).toContainEqual({ second_opinion: { enabled: false } });

	// What the AI is allowed to decide itself.
	const policy = settings.getByRole('radiogroup', {
		name: 'Wie viel die KI selbst entscheidet'
	});
	await expect(policy.getByRole('radio', { name: /^Nur maskieren/ })).toBeChecked();
	await policy.getByRole('radio', { name: /Maskieren und stehen lassen/ }).click();
	await expect.poll(puts).toContainEqual({ second_opinion: { policy: 'protect_and_keep' } });
	await policy.getByRole('radio', { name: /Nichts – nur Vorschläge/ }).click();
	await expect.poll(puts).toContainEqual({ second_opinion: { policy: 'suggest' } });

	// Model missing: load via the shell.
	await expect(settings.getByText(/Nicht installiert/)).toBeVisible();
	await settings.getByRole('button', { name: 'Modell laden …' }).click();
	await expect
		.poll(() => capture.commands.find((b) => b.command === 'models_download')?.args)
		.toEqual({ components: ['classifier'] });

	await expect(settings.getByRole('heading', { name: 'So arbeitet der Prüfer' })).toBeVisible();
	await expect(settings).toContainText('Es schreibt keinen Text und sendet nichts');
});

test('History: occupied space of saved originals (draft)', async ({ context, page }) => {
	await harness(context, {
		entries: () => [entry({ status: 'saved' }), entry({ id: 'e-2', title: 'Rechnung' })]
	});
	await open(page);
	await page.getByRole('button', { name: 'Einstellungen' }).click();
	const settings = dialog(page);
	await settings.getByRole('button', { name: 'Verlauf' }).click();

	await expect(settings.getByTestId('storage-used')).toHaveText('2,4 MB für 2 Einträge');
	await expect(settings).toContainText('50 GB frei auf dem Laufwerk');
	await expect(settings).toContainText('Die Frist löscht auch die gespeicherten Originale');
});

test('History: retention and clearing with consequences', async ({ context, page }) => {
	let geleert = false;
	const { capture } = await harness(context, {
		entries: () =>
			geleert ? [] : [entry({ status: 'saved' }), entry({ id: 'e-2', title: 'Rechnung' })],
		sidecar: (method, path) => {
			if (path === '/entries' && method === 'DELETE') {
				geleert = true;
				return WITHOUT_CONTENT;
			}
			return undefined;
		}
	});
	await open(page);
	await page.getByRole('button', { name: 'Einstellungen' }).click();
	const settings = dialog(page);
	await settings.getByRole('button', { name: 'Verlauf' }).click();

	await settings
		.getByLabel('Einträge im Verlauf automatisch löschen')
		.selectOption({ label: 'nie' });
	await expect
		.poll(() => capture.requests.filter((a) => a.method === 'PUT').map((a) => a.body))
		.toContainEqual({ history: { retention_days: null } });

	await settings.getByRole('button', { name: 'Verlauf leeren …' }).click();
	const followUp = page.getByRole('alertdialog', { name: 'Verlauf leeren?' });
	await expect(followUp).toContainText(
		'2 Einträge mit ihren Dateilisten und den gespeicherten Kopien der Originale werden gelöscht.'
	);
	await expect(followUp).toContainText('Die Übersetzungstabelle bleibt');
	await followUp.getByRole('button', { name: 'Verlauf leeren' }).click();

	await expect(page.getByText('Verlauf geleert.')).toBeVisible();
	const erase = capture.requests.find((a) => a.method === 'DELETE');
	expect(erase?.path).toBe('/entries');
	await expect(page.getByRole('region', { name: 'Zuletzt erstellt' })).toContainText(
		'Noch nichts im Verlauf.'
	);
});

test('Translation table: metrics, backup with password, clearing', async ({ context, page }) => {
	const { capture } = await harness(context, {
		settings: { save_vault_backup: '/Users/test/unname-tabelle.unname-sicherung' },
		sidecar: (method, path) => {
			if (path === '/vault/status') return VAULT;
			if (path === '/vault/clear') return { ...VAULT, mapping_count: 0, entry_count: 0 };
			return undefined;
		}
	});
	await open(page);
	await page.getByRole('button', { name: 'Einstellungen' }).click();
	const settings = dialog(page);
	await settings.getByRole('button', { name: 'Übersetzungstabelle' }).click();

	await expect(settings.getByText('Zuordnungen aus 12 Einträgen')).toBeVisible();
	await expect(settings.getByText(/Schlüssel vom 2\. August 2026/)).toBeVisible();

	await settings.getByRole('button', { name: 'Sicherung exportieren' }).click();
	const backup = page.getByRole('dialog', { name: 'Sicherung exportieren' });
	const save = backup.getByRole('button', { name: 'Sicherung speichern …' });
	await backup.getByLabel('Passwort', { exact: true }).fill('kurz');
	await expect(backup.getByText('Das Passwort braucht mindestens 10 Zeichen.')).toBeVisible();
	await expect(save).toBeDisabled();
	await backup.getByLabel('Passwort', { exact: true }).fill('ein langes Passwort');
	await backup.getByLabel('Passwort wiederholen').fill('ein langes Passwort');
	await save.click();
	await expect(page.getByText('Sicherung gespeichert.')).toBeVisible();
	const command = capture.commands.find((b) => b.command === 'save_vault_backup');
	expect(command?.args).toMatchObject({ password: 'ein langes Passwort' });
	expect(String(command?.args.name)).toMatch(
		/^unname-tabelle-\d{4}-\d{2}-\d{2}\.unname-sicherung$/
	);

	await settings.getByRole('button', { name: 'Tabelle leeren …' }).click();
	const followUp = page.getByRole('alertdialog', { name: 'Tabelle leeren?' });
	await expect(followUp).toContainText('418 Zuordnungen gehen verloren.');
	await expect(followUp.getByRole('button', { name: 'Abbrechen' })).toBeFocused();
	await followUp.getByRole('button', { name: 'Tabelle leeren' }).click();
	await expect(page.getByText('Übersetzungstabelle geleert.')).toBeVisible();
	expect(capture.requests.find((a) => a.path === '/vault/clear')?.body).toEqual({
		confirm: true
	});
});

test('Names & Terms: lists, terms, patterns with try, template', async ({ context, page }) => {
	let pattern = [...PATTERN];
	let terms: unknown[] = [];
	const { capture } = await harness(context, {
		sidecar: (method, path, body) => {
			if (path === '/lists') return LISTEN;
			if (path === '/templates') return TEMPLATES;
			if (path === '/terms' && method === 'GET') return { terms: terms };
			if (path === '/terms' && method === 'PUT') {
				terms = (body as { terms: { text: string; entity_type: string }[] }).terms.map(
					(term, index) => ({ id: `t${index}`, ...term })
				);
				return { terms: terms };
			}
			if (path === '/patterns' && method === 'GET') return { patterns: pattern };
			if (path === '/patterns/test') {
				const text = (body as { text: string }).text;
				const start = text.indexOf('POL-');
				return {
					offset_encoding: 'utf-16',
					hits:
						start < 0
							? []
							: [
									{
										start,
										end: start + 11,
										entity_type: 'CUSTOMER_NUMBER',
										source: 'pattern',
										label: '[Kundennummer]'
									}
								],
					count: start < 0 ? 0 : 1
				};
			}
			if (path === '/templates/beispiel-versicherung/apply') {
				pattern = [...PATTERN, { ...PATTERN[0], id: 'sch', name: 'Schadennummer' }];
				return {
					templates: { ...TEMPLATES, active: 'beispiel-versicherung' },
					terms: terms,
					patterns: pattern,
					settings: { ...SETTINGS, names_terms: { template: 'beispiel-versicherung' } }
				};
			}
			return undefined;
		}
	});
	// The pattern "Alles" is rejected by the sidecar (code and index).
	await context.route('http://127.0.0.1:51234/patterns', async (route) => {
		if (route.request().method() !== 'PUT') return route.fallback();
		capture.requests.push({
			method: 'PUT',
			path: '/patterns',
			search: '',
			body: route.request().postDataJSON()
		});
		return route.fulfill({
			status: 422,
			contentType: 'application/json',
			headers: { 'Access-Control-Allow-Origin': '*' },
			body: JSON.stringify({
				detail: { code: 'pattern_broad', message: 'x', index: 1 }
			})
		});
	});
	await open(page);
	await page.getByRole('button', { name: 'Einstellungen' }).click();
	const settings = dialog(page);
	// A list older than one week: indicator on "Names & Terms".
	await expect(
		settings.getByRole('img', { name: 'Eine Liste ist älter als eine Woche' })
	).toBeVisible();
	await settings.getByRole('button', { name: /Namen & Begriffe/ }).click();

	await expect(settings.getByText('Personen · kunden.csv')).toBeVisible();
	await expect(settings.getByText('1.284 Namen', { exact: false })).toBeVisible();
	await expect(settings.getByText('bitte aktualisieren')).toBeVisible();

	await settings.getByPlaceholder('Begriff hinzufügen …').fill('Projekt Falke');
	await settings.getByRole('button', { name: 'Hinzufügen', exact: true }).click();
	await expect(settings.getByText('Projekt Falke', { exact: true })).toBeVisible();
	expect(capture.requests.find((a) => a.path === '/terms' && a.method === 'PUT')?.body).toEqual({
		terms: [{ text: 'Projekt Falke', entity_type: 'OTHER' }]
	});

	await expect(settings.getByText('wird zu [Kundennummer]')).toBeVisible();
	await expect(settings.getByText('1 Treffer – so würde der Satz maskiert.')).toBeVisible();

	await settings.getByRole('button', { name: 'Muster hinzufügen' }).click();
	await settings.getByLabel('Name', { exact: true }).fill('Alles');
	await settings.getByLabel('Muster (regulärer Ausdruck)').fill('\\w+');
	await settings.getByRole('button', { name: 'Muster hinzufügen' }).click();
	await expect(
		settings.getByText('Das Muster trifft fast jeden Text. Bitte enger fassen.')
	).toBeVisible();
	await settings.getByRole('button', { name: 'Abbrechen' }).click();

	await settings.getByLabel('Vorlage').selectOption({ label: 'Versicherung (Beispiel)' });
	const followUp = page.getByRole('alertdialog', { name: 'Vorlage wählen?' });
	await expect(followUp).toContainText('1 eigener Begriff wird ersetzt.');
	await followUp.getByRole('button', { name: 'Vorlage wählen' }).click();
	await expect(settings.getByText('Schadennummer')).toBeVisible();
	expect(capture.requests.some((a) => a.path === '/templates/beispiel-versicherung/apply')).toBe(
		true
	);
});

test('Technical: empty diagnostic report, developer mode', async ({ context, page }) => {
	await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: APP_ORIGIN });
	const { capture } = await harness(context);
	await open(page);
	await page.getByRole('button', { name: 'Einstellungen' }).click();
	const settings = dialog(page);
	await settings.getByRole('button', { name: 'Technik' }).click();

	await expect(settings.getByText('bereit · nur auf diesem Gerät')).toBeVisible();
	await settings.getByRole('button', { name: 'Diagnosebericht kopieren' }).click();
	await expect(page.getByText(/Diagnosebericht kopiert/)).toBeVisible();
	const report = await page.evaluate(() => navigator.clipboard.readText());
	expect(report).toContain('unname · Diagnosebericht');
	expect(report).toContain('Neustarts: 0');

	await settings.getByRole('button', { name: 'Entwicklermodus' }).click();
	await expect
		.poll(() => capture.requests.filter((a) => a.method === 'PUT').map((a) => a.body))
		.toContainEqual({ technical: { developer_mode: true } });
});

test('Board "settings": each section accessible and matches reference, light and dark', async ({
	context,
	page
}) => {
	test.setTimeout(120_000);
	await harness(context, {
		sidecar: (method, path) => {
			if (path === '/vault/status') return VAULT;
			if (path === '/lists') return LISTEN;
			if (path === '/templates') return TEMPLATES;
			if (path === '/terms')
				return { terms: [{ id: 't0', text: 'Projekt Adler', entity_type: 'OTHER' }] };
			if (path === '/patterns') return { patterns: PATTERN };
			return undefined;
		}
	});
	await open(page);
	await page.getByRole('button', { name: 'Einstellungen' }).click();
	const settings = dialog(page);

	for (const [section, name] of [
		['Allgemein', 'general'],
		['Maskierung', 'masking'],
		['Dokumentsprachen', 'languages'],
		['Zweitmeinung (KI)', 'secondOpinion'],
		['Namen & Begriffe', 'names'],
		['Verlauf', 'history'],
		['Übersetzungstabelle', 'table'],
		['Modelle & Speicher', 'models'],
		['Technik', 'technical']
	] as const) {
		// A section may show a notice ("A list is older than one week").
		const start = new RegExp(`^${section.replace(/[()]/g, '\\$&')}`);
		await settings.getByRole('button', { name: start }).first().click();
		await expect(settings.getByRole('heading', { name: section, level: 2 })).toBeVisible();
		// "Stand today, 09:19" is attached to the run timestamp.
		await board(page, `settings-${name}`, {
			mask: [settings.getByText(/Stand (heute|gestern|vor)/)]
		});
	}
});
