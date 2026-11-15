import { expect, test, type Page } from '@playwright/test';
import { MODELS, MODELS_INSTALLED, board, harness, open } from './harness.js';

/**
 * Initial setup and settings ›
 * Models & storage.
 *
 * The shell is simulated (`huelle.ts`): it reports `setup_required`,
 * accepts `models_*` commands, and the test supplies its events
 * (`models://progress`, `sidecar://status`). We verify what the UI does
 * with those events and what it sends back to the shell.
 */

async function event(page: Page, name: string, payload: unknown): Promise<void> {
	await page.evaluate(
		([n, p]) => (window as unknown as { __EMIT__: (n: string, p: unknown) => void }).__EMIT__(n, p),
		[name, payload] as const
	);
}

function progress(
	component: string | null,
	state: string,
	received = 0,
	total = 0,
	error?: string
) {
	return {
		component,
		state,
		received,
		total,
		error: error ? { code: error, message: '' } : null
	};
}

const READY = {
	status: { state: 'ready', port: 51234, pid: 4242, version: '0.1.0' },
	restarts: 1,
	changed_at: '2026-10-24T09:00:00Z',
	can_upload: true,
	can_export: true,
	setup_required: false
};

test('Initial setup: selection, download with progress, "The necessary is here", start', async ({
	context,
	page
}) => {
	const { capture } = await harness(context, { setup: true });
	await open(page);

	// The switch leads to the setup view on first detection.
	await expect(page).toHaveURL(/\/setup$/);
	await expect(
		page.getByRole('heading', { name: 'Einmal einrichten – danach bleibt alles auf diesem Gerät.' })
	).toBeVisible();
	await expect(page.getByText('Gesamt 784 MB')).toBeVisible();
	// light and dark.
	await board(page, '0-setup');

	// Name detection is required; at least one language pack stays selected,
	// OCR can be deselected.
	await expect(page.getByRole('checkbox', { name: 'Namenserkennung' })).toBeDisabled();
	const ocr = page.getByRole('checkbox', { name: 'Texterkennung für Fotos und Scans' });
	await ocr.click();
	await expect(ocr).toHaveAttribute('aria-checked', 'false');
	const english = page.getByRole('checkbox', { name: 'Englisch' });
	await english.click();
	await expect(english).toHaveAttribute('aria-checked', 'false');
	const german = page.getByRole('checkbox', { name: 'Deutsch' });
	await german.click();
	await expect(german).toHaveAttribute('aria-checked', 'true');
	// Second opinion is optional and pre-selected.
	const secondOpinion = page.getByRole('checkbox', { name: 'Zweitmeinung' });
	await expect(secondOpinion).toHaveAttribute('aria-checked', 'true');
	await secondOpinion.click();
	await expect(secondOpinion).toHaveAttribute('aria-checked', 'false');

	const start = page.getByRole('button', { name: /Herunterladen und prüfen · 612 MB/ });
	await start.click();
	await expect
		.poll(() => capture.commands.find((b) => b.command === 'models_download')?.args)
		.toEqual({ components: ['ner', 'de'] });

	await expect(page.getByRole('heading', { name: 'Wird geladen und geprüft' })).toBeVisible();
	await event(page, 'models://progress', progress('ner', 'downloading', 298_693_053, 597_386_105));
	await expect(page.getByText('50 % von 597 MB').first()).toBeVisible();
	await expect(page.getByText('übersprungen')).toHaveCount(3);
	await expect(page.getByRole('button', { name: 'Loslegen' })).toBeDisabled();

	await event(page, 'models://progress', progress('ner', 'installed', 597_386_105, 597_386_105));
	await event(page, 'models://progress', progress('de', 'installed', 14_639_490, 14_639_490));
	await expect(page.getByText('2 von 2 fertig')).toBeVisible();
	await expect(page.getByText('Das Nötige ist da – Sie können schon loslegen.')).toBeVisible();

	// Only after the "Deposit" gate releases does the start button become active.
	await event(page, 'sidecar://status', READY);
	const getStarted = page.getByRole('button', { name: 'Loslegen' });
	await expect(getStarted).toBeEnabled();
	await getStarted.click();
	await expect(page.getByRole('heading', { name: /Persönliche Daten entfernen/ })).toBeVisible();
});

test('Initial setup: a tampered file is rejected and reported', async ({ context, page }) => {
	await harness(context, { setup: true });
	await open(page, '/setup');

	await page.getByRole('button', { name: /Herunterladen und prüfen/ }).click();
	await event(page, 'models://progress', progress('ner', 'downloading', 1000, 597_386_105));
	await event(
		page,
		'models://progress',
		progress('ner', 'failed', 0, 597_386_105, 'hash_mismatch')
	);
	await event(page, 'models://progress', progress(null, 'failed', 0, 0, 'hash_mismatch'));

	await expect(
		page
			.getByRole('alert')
			.filter({ hasText: 'wich von ihrer Prüfsumme ab und wurde verworfen' })
			.first()
	).toBeVisible();
	await expect(page.getByRole('button', { name: 'Loslegen' })).toHaveCount(0);
	await page.getByRole('button', { name: 'Zur Auswahl' }).click();
	await expect(page.getByRole('button', { name: /Herunterladen und prüfen/ })).toBeEnabled();
});

test('Initial setup: model package from file – rejected then accepted', async ({
	context,
	page
}) => {
	const { capture } = await harness(context, {
		setup: true,
		modelPackage: { error: 'hash_mismatch' }
	});
	await open(page, '/setup');

	const fromFile = page.getByRole('button', { name: /Modellpaket aus Datei installieren/ });
	await fromFile.click();
	await expect(page.getByText(/wich von ihrer Prüfsumme ab/).first()).toBeVisible();
	expect(capture.commands.find((b) => b.command === 'models_import_package')?.args).toEqual({
		title: 'Modellpaket auswählen',
		filter: 'Modellpaket (ZIP)'
	});

	await page.evaluate(
		(fresh) => ((window as unknown as Record<string, unknown>).__modelPackage__ = fresh),
		MODELS_INSTALLED
	);
	await fromFile.click();
	await expect(page.getByText('Modellpaket geprüft und installiert.')).toBeVisible();
	await expect(page.getByText('installiert').first()).toBeVisible();
});

test('No models: "Setup later" shows notice instead of drop, without re-routing', async ({
	context,
	page
}) => {
	await harness(context, { setup: true });
	await open(page);
	await expect(page).toHaveURL(/\/setup$/);

	await page.getByRole('link', { name: 'Später einrichten' }).click();
	await expect(page.getByRole('heading', { name: /Persönliche Daten entfernen/ })).toBeVisible();
	const notice = page.getByRole('status').filter({ hasText: 'Einrichtung steht aus' });
	await expect(notice).toBeVisible();
	await expect(
		page.getByText('Erst die Erkennungsmodelle einrichten – danach bleibt alles auf diesem Gerät.')
	).toBeVisible();
	await notice.getByRole('link', { name: 'Einrichten' }).click();
	await expect(page).toHaveURL(/\/setup$/);
});

test('Settings › Models & Storage: review, source, checksums, remove', async ({
	context,
	page
}) => {
	const { capture } = await harness(context, {
		models: MODELS_INSTALLED,
		sidecar: (method, path) =>
			path === '/models' && method === 'GET'
				? {
						components: [
							{
								id: 'ner',
								state: 'ready',
								source: 'download',
								size_bytes: 597_386_105,
								languages: []
							},
							{
								id: 'de',
								state: 'ready',
								source: 'download',
								size_bytes: 15_300_000,
								languages: ['de']
							},
							{
								id: 'en',
								state: 'ready',
								source: 'download',
								size_bytes: 13_400_000,
								languages: ['en']
							},
							{
								id: 'ocr',
								state: 'ready',
								source: 'download',
								size_bytes: 24_029_062,
								languages: ['deu', 'eng']
							}
						],
						total_bytes: 650_115_167,
						setup_required: false
					}
				: undefined
	});
	await open(page);
	await page.getByRole('button', { name: 'Einstellungen' }).click();
	const dialog = page.getByRole('dialog', { name: 'Einstellungen' });
	await dialog.getByRole('button', { name: 'Modelle & Speicher' }).click();

	await expect(dialog.getByText('auf diesem Gerät · 5 Bestandteile')).toBeVisible();
	await expect(dialog.getByText(/alle per Prüfsumme bestätigt · zuletzt geprüft/)).toBeVisible();
	await expect(dialog.getByText('benötigt')).toBeVisible();

	await dialog.getByRole('button', { name: 'Jetzt prüfen' }).click();
	await expect(page.getByText('Alle Modelle stimmen mit ihren Prüfsummen überein.')).toBeVisible();

	// Custom server: saved only after address provided.
	await dialog.getByLabel('Woher Modelle geladen werden').selectOption('custom');
	await dialog
		.getByLabel('Adresse des eigenen Servers')
		.fill('https://modelle.firma.example/unname');
	await dialog.getByRole('button', { name: 'Übernehmen' }).click();
	await expect
		.poll(() => capture.commands.find((b) => b.command === 'models_set_source')?.args)
		.toEqual({ source: 'custom', customUrl: 'https://modelle.firma.example/unname' });

	await dialog.getByRole('button', { name: 'Liste mit Prüfsummen anzeigen' }).click();
	await expect(dialog.getByText(`sha256 ${'ab'.repeat(32)}`).first()).toBeVisible();

	// Remove prompts for confirmation and mentions the consequences.
	const line = dialog
		.getByRole('listitem')
		.filter({ hasText: 'Texterkennung für Fotos und Scans' });
	await line.getByRole('button', { name: 'Entfernen' }).click();
	const question = page.getByRole('alertdialog');
	await expect(question).toContainText('Texterkennung für Fotos und Scans entfernen?');
	await question.getByRole('button', { name: 'Entfernen' }).click();
	await expect
		.poll(() => capture.commands.find((b) => b.command === 'models_remove')?.args)
		.toEqual({ component: 'ocr' });
	await expect(line.getByRole('button', { name: 'Laden' })).toBeVisible();
	expect(MODELS.components).toHaveLength(5);
});
