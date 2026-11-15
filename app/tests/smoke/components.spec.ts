import { expect, test, type Page } from '@playwright/test';

/**
 * Components page: board "Components" and all variants,
 * light and dark, with reference images — plus interaction that does not show an image
 * (keyboard, focus, ARIA).
 *
 * Runs against the dev server; the released build does not include this page
 * (see developer pages spec).
 */

type Mode = 'light' | 'dark';

async function open(page: Page, mode: Mode) {
	await page.emulateMedia({ colorScheme: mode, reducedMotion: 'reduce' });
	await page.addInitScript(() => localStorage.setItem('mode-watcher-mode', 'system'));
	await page.goto('/components', { waitUntil: 'networkidle' });
	await page.evaluate(async () => document.fonts.ready);
	await expect(page.locator('html')).toHaveClass(mode === 'dark' ? /dark/ : /^(?!.*dark)/);
}

for (const [mode, name] of [
	['light', 'light'],
	['dark', 'dark']
] as const) {
	test.describe(`Components ${name}`, () => {
		test('Board matches reference', async ({ page }) => {
			await open(page, mode);
			await expect(page.getByTestId('board-components')).toHaveScreenshot(
				`components-board-${name}.png`,
				{ caret: 'hide' }
			);
		});

		test('all variants match reference', async ({ page }) => {
			await open(page, mode);
			await expect(page.getByTestId('variants')).toHaveScreenshot(
				`components-variants-${name}.png`,
				{ caret: 'hide' }
			);
		});
	});
}

test.describe('operation', () => {
	test.beforeEach(async ({ page }) => {
		await open(page, 'light');
	});

	test('segmented changes with arrow keys', async ({ page }) => {
		const group = page.getByRole('radiogroup', { name: 'Was soll geschützt werden?' });
		await group.getByRole('radio', { name: 'Dokumente' }).focus();
		await page.keyboard.press('ArrowRight');
		await expect(group.getByRole('radio', { name: 'Text' })).toBeChecked();
		await expect(group.getByRole('radio', { name: 'Dokumente' })).not.toBeChecked();
	});

	test('MaskingControl follows spec rules', async ({ page }) => {
		const menu = page.getByTestId('masking-review');
		const all = menu.getByRole('switch', { name: 'Anonymisieren' });
		const places = menu.getByRole('button', { name: /^\+?\s*Orte/ });

		await expect(all).not.toBeChecked();
		await expect(places).toHaveAttribute('aria-pressed', 'false');

		// Turn on: irreversible and all categories.
		await all.click();
		await expect(all).toBeChecked();
		await expect(menu.getByRole('radio', { name: /Irreversibel/ })).toBeChecked();
		await expect(places).toHaveAttribute('aria-pressed', 'true');

		// Deselecting a category also toggles its derived switch.
		await places.click();
		await expect(all).not.toBeChecked();

		// Switching method resets to default.
		await places.click();
		await menu.getByRole('radio', { name: /Reversibel/ }).click();
		await expect(all).not.toBeChecked();
		await expect(places).toHaveAttribute('aria-pressed', 'false');

		// Locked categories stay on.
		await expect(menu.getByRole('button', { name: /Konto-, Ausweis/ })).toBeDisabled();
	});

	test('Popover opens, closes with Escape and returns focus', async ({ page }) => {
		const trigger = page.getByRole('button', { name: 'Maskierung anpassen' });
		await trigger.click();
		await expect(page.getByRole('dialog', { name: 'Maskierung anpassen' })).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(page.getByRole('dialog', { name: 'Maskierung anpassen' })).toBeHidden();
		await expect(trigger).toBeFocused();
	});

	test('Code menu can be operated with the keyboard', async ({ page }) => {
		await page.getByRole('button', { name: 'Code-Menü' }).click();
		const menu = page.getByRole('menu');
		await expect(menu.getByRole('menuitem', { name: 'Nur hier behalten' })).toBeVisible();
		await page.keyboard.press('ArrowDown');
		await page.keyboard.press('Escape');
		await expect(menu).toBeHidden();
	});

	test('ConfirmDialog lists the consequences and starts on "Cancel"', async ({ page }) => {
		await page.getByRole('button', { name: 'Verlauf leeren …' }).click();
		const dialog = page.getByRole('alertdialog', { name: 'Verlauf leeren?' });
		await expect(dialog).toBeVisible();
		await expect(dialog.getByText('Die Übersetzungstabelle bleibt')).toBeVisible();
		await expect(dialog.getByRole('button', { name: 'Abbrechen' })).toBeFocused();
		await page.keyboard.press('Escape');
		await expect(dialog).toBeHidden();
	});

	test('Help shows numbered notices and links them to the target', async ({ page }) => {
		const target = page.getByText('Ziel einer Hilfe-Marke');
		await expect(page.getByRole('note')).toHaveCount(4); // the static examples

		await page.getByRole('button', { name: 'Hilfe anzeigen' }).first().click();
		await expect(page.getByRole('button', { name: 'Hilfe anzeigen' }).first()).toHaveAttribute(
			'aria-pressed',
			'true'
		);
		await expect(page.getByRole('note')).toHaveCount(5);
		const notizId = await target.getAttribute('aria-describedby');
		expect(notizId).toMatch(/^coachmark-\d+$/);
		await expect(page.locator(`#${notizId}`)).toContainText('wiederherstellbar bleiben');

		await page.keyboard.press('Escape');
		await expect(page.getByRole('note')).toHaveCount(4);
		await expect(target).not.toHaveAttribute('aria-describedby');
	});

	test('SegmentText displays segment text exactly, without extra spaces', async ({ page }) => {
		// The window content is what gets saved.
		await expect(page.getByTestId('addendum-text')).toHaveText(
			'Das Holz stammt wie vereinbart von den <<PERSON_77c0a2f1>>. Ein Muster der ' +
				'Arbeitsplatte in Eiche Natur liegt bei, wie von Ihrer Tochter Lena gewünscht.',
			{ useInnerText: false }
		);
		const roh = await page.getByTestId('addendum-text').evaluate((p) => p.textContent?.trim());
		expect(roh).toBe(
			'Das Holz stammt wie vereinbart von den <<PERSON_77c0a2f1>>. Ein Muster der ' +
				'Arbeitsplatte in Eiche Natur liegt bei, wie von Ihrer Tochter Lena gewünscht.'
		);
	});

	test('Codes show the original on focus and announce it', async ({ page }) => {
		const code = page.getByRole('button', { name: 'Person: Sabine Hofer' }).first();
		await code.focus();
		await expect(page.getByText('Original: Sabine Hofer').first()).toBeVisible();
		await code.click();
		await expect(code).toHaveAttribute('aria-expanded', 'true');
	});
});
