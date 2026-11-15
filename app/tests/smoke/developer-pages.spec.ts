import { expect, test } from '@playwright/test';

/**
 * Routes under `src/routes/(dev)` exist only in development builds.
 * The production build returns its error page for these routes.
 */

const APP_ORIGIN = 'http://127.0.0.1:4173';

test('components page is not reachable in the production build', async ({ page }) => {
	await page.goto(`${APP_ORIGIN}/components`, { waitUntil: 'networkidle' });
	await expect(page.getByText('404')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Bausteine' })).toHaveCount(0);
});
