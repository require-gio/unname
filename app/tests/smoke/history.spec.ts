import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { FILE, entry, harness, open } from './harness.js';

/**
 * Sidebar and history view: navigation with counters, three most recent entries,
 * full view with search, sorting and "Show more", back navigation from an entry,
 * rename, and loading/error states.
 */

const ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';

/** Reference number `i`: six unique characters from the alphabet. */
function ref(i: number): string {
	return `R${ALPHABET[Math.floor(i / 32) % 32]}${ALPHABET[i % 32]}Q7X`;
}

/** `n` similar entries, newest first; every third is saved. */
function many(n: number, part: (i: number) => Record<string, unknown> = () => ({})) {
	return Array.from({ length: n }, (_, i) => {
		const time = new Date(Date.now() - (i + 1) * 3600_000).toISOString();
		const saved = i % 3 === 2;
		return entry({
			id: `e-${String(i).padStart(3, '0')}`,
			display_ref: ref(i),
			title: 'Dokument',
			status: saved ? 'saved' : 'review',
			created_at: time,
			updated_at: time,
			draft_revision: 1,
			saved_revision: saved ? 1 : null,
			files: [{ ...FILE, id: `j-${i}`, status: 'review', ocr_used: i % 2 === 0, pages: 4 }],
			...part(i)
		});
	});
}

const sidebar = (page: Page) => page.getByRole('complementary', { name: 'Verlauf' });
const navigation = (page: Page) => sidebar(page).getByRole('navigation', { name: 'Einträge' });
const last = (page: Page) => sidebar(page).getByRole('region', { name: 'Zuletzt erstellt' });
const table = (page: Page) => page.getByRole('table');

test('20 identical titles: three in sidebar, all 20 in full view', async ({ context, page }) => {
	const list = many(20);
	const { capture } = await harness(context, { entries: () => list });
	await open(page, '/');

	await expect(navigation(page).getByRole('link', { name: 'Offen, 14 Einträge' })).toBeVisible();
	await expect(
		navigation(page).getByRole('link', { name: 'Abgeschlossen, 6 Einträge' })
	).toBeVisible();
	await expect(
		navigation(page).getByRole('link', { name: 'Alle Einträge, 20 Einträge' })
	).toHaveAttribute('href', '/history?view=all');
	await expect(last(page).getByRole('link')).toHaveCount(3);
	// Reference distinguishes identical titles.
	await expect(last(page).getByRole('link').first()).toContainText(`#${ref(0)}`);
	await expect(last(page).getByRole('link').first()).toContainText('PDF-Scan · 4 Seiten');

	await navigation(page)
		.getByRole('link', { name: /^Alle Einträge/ })
		.click();
	await expect(page).toHaveURL(/\/history\?view=all$/);
	await expect(page.getByRole('heading', { level: 1, name: 'Alle Einträge' })).toBeVisible();
	await expect(table(page).getByRole('row')).toHaveCount(21);
	await expect(navigation(page).getByRole('link', { name: /^Alle Einträge/ })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(navigation(page).getByRole('link', { name: /^Offen/ })).not.toHaveAttribute(
		'aria-current'
	);

	// Entries with same title have distinct names.
	await expect(table(page).getByRole('link', { name: `Dokument #${ref(5)}` })).toBeVisible();
	await expect(
		table(page).getByRole('button', { name: `Umbenennen: Dokument #${ref(5)}` })
	).toBeVisible();

	// Open and Closed share entries without overlap.
	await navigation(page)
		.getByRole('link', { name: /^Offen/ })
		.click();
	await expect(page).toHaveURL(/view=open/);
	await expect(table(page).getByRole('row')).toHaveCount(15);
	await expect(table(page)).not.toContainText('Gespeichert');
	await navigation(page)
		.getByRole('link', { name: /^Abgeschlossen/ })
		.click();
	await expect(table(page).getByRole('row')).toHaveCount(7);
	await expect(table(page)).not.toContainText('Zu prüfen');

	// Search and filter do not load any documents.
	await page.getByRole('searchbox', { name: /suchen/ }).fill('scan');
	const allowed = new Set(['/entries', '/storage', '/settings', '/health']);
	expect(capture.requests.filter((a) => !allowed.has(a.path))).toEqual([]);
});

test('over 50 entries: search also works beyond the first page, counters remain', async ({
	context,
	page
}) => {
	const list = many(60);
	await harness(context, { entries: () => list });
	await open(page, '/history?view=all');

	await expect(table(page).getByRole('row')).toHaveCount(51);
	await expect(page.getByText('60 Einträge', { exact: true })).toBeVisible();
	await page.getByRole('button', { name: 'Weitere anzeigen' }).click();
	await expect(table(page).getByRole('row')).toHaveCount(61);
	await expect(page.getByRole('button', { name: 'Weitere anzeigen' })).toHaveCount(0);
	await expect(
		navigation(page).getByRole('link', { name: 'Alle Einträge, 60 Einträge' })
	).toBeVisible();

	// The reference of entry 58 – with “#” and lowercase.
	const search = page.getByRole('searchbox', { name: /suchen/ });
	await search.fill(`#${ref(57).toLowerCase()}`);
	await expect(table(page).getByRole('row')).toHaveCount(2);
	await expect(page.getByText('1 von 60 Einträgen')).toBeVisible();
	await expect(table(page)).toContainText('Referenz stimmt genau');
	await expect(
		navigation(page).getByRole('link', { name: 'Alle Einträge, 60 Einträge' })
	).toBeVisible();

	// The search stops when changing the section.
	await navigation(page)
		.getByRole('link', { name: /^Offen/ })
		.click();
	await expect(search).toHaveValue(`#${ref(57).toLowerCase()}`);

	await page.getByRole('button', { name: 'Suche löschen' }).first().click();
	await expect(search).toHaveValue('');
	await expect(search).toBeFocused();

	await search.fill('kein-treffer-xyz');
	await expect(page.getByText('Keine passenden Einträge.')).toBeVisible();
});

test('open from history and return: search, section, sorting and scope stay', async ({
	context,
	page
}) => {
	// 60 of 90 entries: the target entry appears after "Show more".
	const list = many(90);
	await harness(context, {
		entries: () => list,
		sidecar: (method, path) => {
			const id = /^\/entries\/([^/]+)$/.exec(path)?.[1];
			return method === 'GET' && id ? list.find((item) => item.id === id) : undefined;
		}
	});
	await open(page, '/history?view=open');

	await page.getByRole('combobox', { name: 'Sortierung' }).selectOption('oldest');
	await expect(page).toHaveURL(/view=open&sort=oldest/);
	await page.getByRole('searchbox', { name: /suchen/ }).fill('dokument');
	await page.getByRole('button', { name: 'Weitere anzeigen' }).click();
	await expect(table(page).getByRole('row')).toHaveCount(61);

	// An entry "to review" opens the review view directly.
	const target = table(page).getByRole('link', { name: `Dokument #${ref(0)}` });
	await expect(target).toHaveAttribute('href', '/entry/e-000/review');
	await target.scrollIntoViewIfNeeded();
	const before = await page.locator('main').evaluate((main) => main.scrollTop);
	expect(before).toBeGreaterThan(0);
	await target.click();
	await expect(page).toHaveURL(/\/entry\/e-000\/review/);

	await page.getByRole('link', { name: 'Zurück zum Verlauf' }).click();
	await expect(page).toHaveURL(/\/history\?view=open&sort=oldest$/);
	await expect(page.getByRole('searchbox', { name: /suchen/ })).toHaveValue('dokument');
	await expect(table(page).getByRole('row')).toHaveCount(61);
	await expect
		.poll(() => page.locator('main').evaluate((main) => main.scrollTop))
		.toBeGreaterThan(before - 40);

	// Opened via the sidebar: no way back to a previous list.
	await last(page).getByRole('link').first().click();
	await expect(page).toHaveURL(/\/entry\/e-000/);
	await expect(page.getByRole('link', { name: 'Zurück zum Verlauf' })).toHaveCount(0);
});

test('rename: success, empty title and sidecar error', async ({ context, page }) => {
	let list = many(3);
	let fail = true;
	const { capture } = await harness(context, {
		entries: () => list,
		sidecar: (method, path, body) => {
			if (method !== 'PATCH' || path !== '/entries/e-001') return undefined;
			if (fail) {
				// No custom response: harness returns 500.
				fail = false;
				return undefined;
			}
			const title = (body as { title: string }).title;
			list = list.map((item) =>
				item.id === 'e-001' ? { ...item, title: title, title_custom: true } : item
			);
			return list.find((item) => item.id === 'e-001');
		}
	});
	await open(page, '/history?view=all');

	const trigger = table(page).getByRole('button', { name: `Umbenennen: Dokument #${ref(1)}` });
	await trigger.click();
	const dialog = page.getByRole('dialog', { name: 'Eintrag umbenennen' });
	await expect(dialog).toContainText(`#${ref(1)}`);
	const field = dialog.getByRole('textbox', { name: 'Titel' });
	await expect(field).toHaveValue('Dokument');
	// Opens on the title, so typing replaces it and Enter saves.
	await expect(field).toBeFocused();

	// Escape closes without request, focus returns to the trigger.
	await page.keyboard.press('Escape');
	await expect(dialog).toBeHidden();
	await expect(trigger).toBeFocused();
	await trigger.click();
	await expect(field).toHaveValue('Dokument');

	await field.fill('   ');
	await dialog.getByRole('button', { name: 'Umbenennen' }).click();
	await expect(dialog.getByText('Bitte geben Sie einen Titel ein.').first()).toBeVisible();
	await field.fill('x'.repeat(201));
	await page.keyboard.press('Enter');
	await expect(
		dialog.getByText('Der Titel darf höchstens 200 Zeichen haben.').first()
	).toBeVisible();
	expect(capture.requests.filter((a) => a.method === 'PATCH')).toEqual([]);

	// Sidecar returns nonsense: text stays, another attempt works.
	await field.fill('  Projekt Nord  ');
	await page.keyboard.press('Enter');
	await expect(dialog.getByText(/Umbenennen hat nicht geklappt/).first()).toBeVisible();
	await expect(field).toHaveValue('  Projekt Nord  ');

	await dialog.getByRole('button', { name: 'Umbenennen' }).click();
	await expect(dialog).toBeHidden();
	const patches = capture.requests.filter((a) => a.method === 'PATCH');
	expect(patches.at(-1)?.body).toEqual({ title: 'Projekt Nord' });
	await expect(table(page).getByRole('link', { name: `Projekt Nord #${ref(1)}` })).toBeVisible();
	await expect(last(page)).toContainText('Projekt Nord');
	// Order stays: rename does not move the row.
	await expect(table(page).getByRole('row').nth(2)).toContainText('Projekt Nord');
	await expect(
		table(page).getByRole('button', { name: `Umbenennen: Projekt Nord #${ref(1)}` })
	).toBeFocused();

	// If the row no longer matches the search, focus moves to the search box.
	await page.getByRole('searchbox', { name: /suchen/ }).fill('projekt');
	await table(page)
		.getByRole('button', { name: /^Umbenennen: Projekt Nord/ })
		.click();
	await expect(field).toBeFocused();
	await field.fill('Angebot Süd');
	await page.keyboard.press('Enter');
	await expect(dialog).toBeHidden();
	await expect(page.getByRole('searchbox', { name: /suchen/ })).toBeFocused();
	await expect(page.getByText('Keine passenden Einträge.')).toBeVisible();
});

test('Error on initial load, stale state and expired entry', async ({ context, page }) => {
	// A running entry: the shell polls every 2 s.
	const list = many(2, (i) => (i === 0 ? { status: 'processing' } : {}));
	let fail = true;
	await harness(context, { entries: () => list });
	const cors = { 'Access-Control-Allow-Origin': '*' };
	await page.route('**/entries', (route) =>
		fail && route.request().method() === 'GET'
			? route.fulfill({ status: 500, headers: cors, body: '{}' })
			: route.fallback()
	);
	await page.route('**/entries/weg', (route) =>
		route.fulfill({ status: 404, headers: cors, contentType: 'application/json', body: '{}' })
	);
	await open(page, '/history?view=all');

	await expect(page.getByRole('alert')).toContainText('Der Verlauf konnte nicht geladen werden.');
	// No false zero while nothing is loaded.
	await expect(navigation(page).getByRole('link', { name: 'Offen', exact: true })).toBeVisible();
	fail = false;
	await page.getByRole('button', { name: 'Erneut versuchen' }).click();
	await expect(table(page).getByRole('row')).toHaveCount(3);

	// A later error leaves the list unchanged and reports it.
	fail = true;
	await expect(page.getByText(/ließ sich gerade nicht aktualisieren/)).toBeVisible({
		timeout: 6000
	});
	await expect(table(page).getByRole('row')).toHaveCount(3);
	await expect(page.getByRole('searchbox', { name: /suchen/ })).toBeVisible();

	// Between list and click expired: existing notice with a way back.
	fail = false;
	await open(page, '/entry/weg');
	await expect(page.getByText(/Diesen Eintrag gibt es nicht mehr/)).toBeVisible();
	await expect(page.getByRole('link', { name: 'Zurück zum Verlauf' })).toHaveAttribute(
		'href',
		'/history?view=all'
	);
});

test('empty history and empty area', async ({ context, page }) => {
	let list: unknown[] = [];
	await harness(context, { entries: () => list });
	await open(page, '/history?view=open');
	await expect(page.getByText(/Noch nichts im Verlauf\. Abgelegte Dokumente/)).toBeVisible();
	await expect(page.getByRole('main').getByRole('link', { name: 'Neu' })).toBeVisible();

	list = many(2).map((item) => ({ ...item, status: 'review' }));
	await open(page, '/history?view=completed');
	await expect(page.getByText('Noch nichts abgeschlossen.')).toBeVisible();

	await open(page, '/history?view=unbekannt');
	await expect(page.getByRole('heading', { level: 1, name: 'Alle Einträge' })).toBeVisible();
});

test('collapsed sidebar meets all three goals; axe light and dark', async ({ context, page }) => {
	const list = many(5);
	await harness(context, { entries: () => list });
	await open(page, '/history?view=open');

	for (const schema of ['light', 'dark'] as const) {
		await page.emulateMedia({ colorScheme: schema });
		const { violations } = await new AxeBuilder({ page })
			.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
			.analyze();
		expect(
			violations.map((v) => v.id),
			`axe ${schema}`
		).toEqual([]);
	}
	await page.emulateMedia({ colorScheme: 'light' });

	await page.getByRole('button', { name: 'Seitenleiste einklappen' }).click();
	const bar = navigation(page);
	await expect(bar.getByRole('link')).toHaveCount(3);
	await expect(bar.getByRole('link', { name: 'Offen, 4 Einträge' })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await bar.getByRole('link', { name: /^Abgeschlossen/ }).click();
	await expect(page).toHaveURL(/view=completed/);
	await expect(page.getByRole('button', { name: 'Seitenleiste ausklappen' })).toBeVisible();
	await page.getByRole('button', { name: 'Seitenleiste ausklappen' }).click();

	// Narrow window, slim content: everything reachable, no horizontal overflow.
	await page.setViewportSize({ width: 640, height: 430 });
	await expect(page.getByRole('button', { name: 'Einstellungen' })).toBeVisible();
	const overflow = await page
		.locator('main')
		.evaluate((main) => main.scrollWidth - main.clientWidth);
	expect(overflow).toBeLessThanOrEqual(1);
	await expect(
		table(page)
			.getByRole('button', { name: /^Umbenennen/ })
			.first()
	).toBeVisible();
});
