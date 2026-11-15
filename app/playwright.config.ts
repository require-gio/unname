import { defineConfig, devices } from '@playwright/test';

/**
 * A single pinned Chromium renderer provides comparable reference across
 * Windows and macOS. The image comparison ignores minor antialiasing differences
 * but not shifted or missing UI areas.
 */
export default defineConfig({
	testDir: './tests/smoke',
	fullyParallel: false,
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? [['line'], ['html', { open: 'never' }]] : 'line',
	outputDir: 'test-results/smoke',
	snapshotPathTemplate: '{testDir}/references/{arg}{ext}',
	expect: {
		toHaveScreenshot: {
			// Pixelmatch already removes antialiasing. 2 % tolerance.
			// Captures differences in native font rasterisation.
			threshold: 0.25,
			maxDiffPixelRatio: 0.02,
			animations: 'disabled'
		}
	},
	use: {
		...devices['Desktop Chrome'],
		browserName: 'chromium',
		viewport: { width: 1280, height: 860 },
		deviceScaleFactor: 1,
		colorScheme: 'light',
		locale: 'de-AT',
		timezoneId: 'Europe/Vienna',
		// `reducedMotion` is not a dedicated test option; Playwright silently
		// ignores the key under `use`.
		contextOptions: { reducedMotion: 'reduce' },
		trace: 'retain-on-failure',
		screenshot: 'only-on-failure'
	},
	projects: [
		{
			// The shipped build as loaded by the shell.
			name: 'app',
			testIgnore: /components\.spec\.ts$/
		},
		{
			// The components page exists only in the development build;
			// its reference images are generated against the dev server.
			name: 'components',
			testMatch: /components\.spec\.ts$/,
			use: { baseURL: 'http://127.0.0.1:4174', viewport: { width: 1440, height: 900 } }
		}
	],
	webServer: [
		{
			command: 'npm run preview -- --host 127.0.0.1 --port 4173',
			url: 'http://127.0.0.1:4173',
			reuseExistingServer: !process.env.CI,
			timeout: 30_000
		},
		{
			command: 'npx vite dev --host 127.0.0.1 --port 4174 --strictPort',
			url: 'http://127.0.0.1:4174/components',
			reuseExistingServer: !process.env.CI,
			timeout: 60_000
		}
	]
});
