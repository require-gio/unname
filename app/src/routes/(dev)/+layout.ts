import { dev } from '$app/env';
import { error } from '@sveltejs/kit';

/**
 * Development-only pages: in production they return 404.
 * Reference images for the components page are generated against the dev server
 * (playwright.config.ts, project "components").
 */
export function load() {
	if (!dev) error(404, 'Nicht gefunden');
}
