import { defineConfig } from 'vitest/config';
import tailwindcss from '@tailwindcss/vite';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';

const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// Tauri serves static files. A server adapter would yield a non-startable app (spec §9.6). `fallback` makes it an SPA because rendering occurs in the browser without SSR.
			adapter: adapter({ fallback: 'index.html' }),
			// Kit 3 would otherwise poll `_app/version.json` hourly to detect new deployments; a desktop app isn’t redeployed during a session. The focus/visibility check can’t be disabled. It reads the same file from the bundle (CSP `connect-src 'self'`), not from the network.
			version: { pollInterval: 0 }
		})
	],
	// Tauri requires a fixed port and must not fall back to another on error—otherwise the window shows a blank page.
	server: {
		port: 1420,
		strictPort: true,
		host: host || '127.0.0.1',
		hmr: host ? { protocol: 'ws', host, port: 1421 } : undefined,
		watch: { ignored: ['**/src-tauri/**'] }
	},
	envPrefix: ['VITE_', 'TAURI_ENV_*'],
	build: {
		// Windows 10/11 and macOS WebViews; see spec §4 and §5. On macOS the sidecar requires macOS 14 (torch-wheel macosx_14_0_arm64), which Safari 17 provides (`minimumSystemVersion` in tauri.macos.conf.json). Older targets appeared compatible but cannot transpile BigInt literals from dependencies.
		target: process.env.TAURI_ENV_PLATFORM === 'windows' ? 'chrome105' : 'safari17',
		minify: !process.env.TAURI_ENV_DEBUG,
		sourcemap: !!process.env.TAURI_ENV_DEBUG
	},
	test: {
		expect: { requireAssertions: true },
		projects: [
			{
				extends: './vite.config.ts',
				test: {
					name: 'server',
					environment: 'node',
					// Date and time strings are calculated in local time. Fixed to Central European Time so CI runners (UTC) and developer machines produce identical results.
					env: { TZ: 'Europe/Berlin' },
					include: ['src/**/*.{test,spec}.{js,ts}'],
					exclude: ['src/**/*.svelte.{test,spec}.{js,ts}']
				}
			}
		]
	}
});
