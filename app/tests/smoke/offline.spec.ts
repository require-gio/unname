import { expect, test } from '@playwright/test';

const APP_ORIGIN = 'http://127.0.0.1:4173';

test('starts without external network and matches visual reference', async ({ context, page }) => {
	const externeVersuche: string[] = [];

	// The preview server only replaces the Tauri asset protocol in the test.
	// Anything beyond this loopback origin is blocked from the first start.
	await context.route('**/*', async (route) => {
		const url = new URL(route.request().url());
		if (url.origin === APP_ORIGIN || ['data:', 'blob:', 'about:'].includes(url.protocol)) {
			await route.continue();
			return;
		}
		externeVersuche.push(url.href);
		await route.abort('blockedbyclient');
	});

	await context.addInitScript(() => {
		localStorage.setItem('mode-watcher-mode', 'light');
	});
	await page.goto(APP_ORIGIN, { waitUntil: 'networkidle' });
	await page.evaluate(async () => document.fonts.ready);

	await expect(page).toHaveTitle('unname');
	await expect(
		page.getByRole('heading', { name: /Persönliche Daten entfernen – ohne dass etwas/ })
	).toBeVisible();
	// Without the shell the dropzone stays locked and reports the reason (Fail-Closed).
	await expect(page.getByRole('status').filter({ hasText: 'Außerhalb der App' })).toBeVisible();
	await expect(
		page.getByText('Nur in der App möglich – hier wird nichts verarbeitet.')
	).toBeVisible();
	await expect(page.getByRole('button', { name: 'Dateien auswählen' })).toHaveCount(0);
	// All four fonts include umlauts and ß – a Latin-Basic subset would miss them (spec §9.6).
	const loadedFonts = await page.evaluate(async () => {
		const probe = 'Größe Übersetzung Straße';
		const familien = [
			'"Instrument Sans Variable"',
			'"Instrument Serif"',
			'"Newsreader Variable"',
			'"JetBrains Mono Variable"'
		];
		const loaded = await Promise.all(
			familien.map((familie) => document.fonts.load(`16px ${familie}`, probe))
		);
		return Object.fromEntries(familien.map((familie, index) => [familie, loaded[index].length]));
	});
	for (const [familie, num] of Object.entries(loadedFonts)) {
		expect(num, `${familie} muss aus dem Bundle ladbar sein`).toBeGreaterThan(0);
	}

	const loadedResources = await page.evaluate(() =>
		performance
			.getEntriesByType('resource')
			.map((entry) => entry.name)
			.filter((name) => name.startsWith('http://') || name.startsWith('https://'))
	);
	for (const resource of loadedResources) {
		expect(new URL(resource).origin, `externe Ressource geladen: ${resource}`).toBe(APP_ORIGIN);
	}
	// After initial load the rendered view remains fully usable and stable even without any network connection.
	await context.setOffline(true);
	await page.waitForTimeout(100);
	await expect(externeVersuche, 'die App darf keinen externen Request versuchen').toEqual([]);
	await expect(page).toHaveScreenshot('start-view.png', {
		caret: 'hide',
		fullPage: false
	});
});
