#!/usr/bin/env node
/**
 * Generates the TypeScript types for the Sidecar API v2 or checks they are up-to-date.
 *
 * Chain: Pydantic models (`sidecar/src/presidio_sidecar/api_models.py`)
 *   → JSON schema (`python -m presidio_sidecar.api_schema`)
 *   → `app/src/lib/sidecar/api.schema.json`
 *   → `json-schema-to-typescript`
 *   → `app/src/lib/sidecar/api.gen.ts`
 *
 * Both files are checked in and formatted with the app's Prettier config.
 * They are never edited by hand.
 *
 * Usage: node scripts/api-types.mjs [--check]
 *   --check  produces no output and exits with error if schema or types are stale
 *            (contract test in `scripts/check.mjs`)
 */

import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const ROOT = resolve(import.meta.dirname, '..');
const APP = resolve(ROOT, 'app');
const SCHEMA = resolve(APP, 'src/lib/sidecar/api.schema.json');
const TYPES = resolve(APP, 'src/lib/sidecar/api.gen.ts');
const check = process.argv.includes('--check');

const require = createRequire(resolve(APP, 'package.json'));
const load = (name) => import(pathToFileURL(require.resolve(name)).href);
const { compile } = await load('json-schema-to-typescript');
const prettierModul = await load('prettier');
const prettier = prettierModul.default ?? prettierModul;

const run_result = spawnSync(
	'uv',
	['run', '--project', 'sidecar', '--frozen', 'python', '-W', 'ignore', '-m', 'presidio_sidecar.api_schema'],
	{ cwd: ROOT, encoding: 'utf8' }
);
if (run_result.status !== 0) {
	process.stderr.write(run_result.stderr);
	console.error('Schema konnte nicht aus den Pydantic-Modellen erzeugt werden.');
	process.exit(1);
}
const schema = JSON.parse(run_result.stdout);

// Prettier loads the app's plugins relative to the working directory.
process.chdir(APP);

async function formatted(text, file) {
	const optionen = (await prettier.resolveConfig(file)) ?? {};
	return prettier.format(text, { ...optionen, filepath: file });
}

const generated_types = await compile(schema, 'UnnameApiV2', {
	bannerComment: [
		'/**',
		' * Typen der Sidecar-API v2 – ERZEUGT, nicht von Hand bearbeiten.',
		' *',
		' * Quelle: sidecar/src/presidio_sidecar/api_models.py',
		' * Neu erzeugen: node scripts/api-types.mjs',
		' */'
	].join('\n'),
	additionalProperties: false,
	unreachableDefinitions: true,
	format: false
});

const erwartet = new Map([
	[SCHEMA, await formatted(JSON.stringify(schema), SCHEMA)],
	[TYPES, await formatted(generated_types, TYPES)]
]);

let outdated = 0;
for (const [file, content] of erwartet) {
	const existing = existsSync(file) ? readFileSync(file, 'utf8') : null;
	if (existing === content) continue;
	if (check) {
		console.error(`veraltet: ${file.slice(ROOT.length + 1)}`);
		outdated += 1;
	} else {
		writeFileSync(file, content);
		console.log(`geschrieben: ${file.slice(ROOT.length + 1)}`);
	}
}

if (outdated) {
	console.error(
		'Die API-Typen passen nicht mehr zu den Pydantic-Modellen. Neu erzeugen mit: node scripts/api-types.mjs'
	);
	process.exit(1);
}
if (check) console.log('API-Typen sind aktuell.');
