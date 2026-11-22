#!/usr/bin/env node
/**
 * Build an installable desktop distribution for the target platform.
 *
 * The default is the real release path: signing (and notarization on macOS) is required.
 * An unsigned build must be marked explicitly with `--unsigned` for CI/development.
 *
 * `--adhoc` (macOS only) signs the app, sidecar and download service ad-hoc
 * (`codesign -s -`) with real entitlements and Hardened Runtime, but without
 * Developer ID and without notarization. The sandbox is therefore active;
 * Gatekeeper only allows “Open Anyway”. `--unsigned` carries no entitlements,
 * so no sandbox.
 *
 * Usage:
 *   node scripts/build-distribution.mjs [--target TRIPLE] [--unsigned | --adhoc]
 *                                       [--sidecar-ready]
 *
 * On macOS the download service is added (spec §4, `build-download-xpc.mjs`).
 * It is the only package component with its own network rights; the sidecar
 * and its helpers have none.
 */

import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { chmodSync, copyFileSync, existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { basename, resolve } from 'node:path';

const ROOT = resolve(import.meta.dirname, '..');
const APP = resolve(ROOT, 'app');
const NOTARIZATION_VARIABLES = [
	'APPLE_ID',
	'APPLE_PASSWORD',
	'APPLE_TEAM_ID',
	'APPLE_API_ISSUER',
	'APPLE_API_KEY',
	'APPLE_API_KEY_PATH'
];

const args = process.argv.slice(2);
const unsigned = takeFlag('--unsigned');
const adhoc = takeFlag('--adhoc');
const sidecarReady = takeFlag('--sidecar-ready');
const target = takeValue('--target') ?? targetForHost();
const mac = target.includes('apple-darwin');
/** Release with real identity: Developer ID or Authenticode. */
const release = !unsigned && !adhoc;

if (args.length > 0) {
	fail(`Unbekannte Argumente: ${args.join(' ')}`);
}
if (unsigned && adhoc) fail('--unsigned und --adhoc schließen sich aus.');
if (adhoc && !mac) fail('--adhoc gibt es nur für macOS.');

assertNativeTarget(target);
if (release) assertReleaseCredentials(target);

if (!sidecarReady) {
	const sidecarArgs = [resolve(ROOT, 'scripts', 'build-sidecar.mjs'), '--target', target];
	if (release && mac) sidecarArgs.push('--require-signing');
	if (adhoc) sidecarArgs.push('--codesign-identity', '-');
	run(process.execPath, sidecarArgs, ROOT);
}

const sidecarPath = resolve(
	APP,
	'src-tauri',
	'binaries',
	`presidio-sidecar-${target}${target.includes('windows') ? '.exe' : ''}`
);
if (!existsSync(sidecarPath)) {
	fail(`Das Tauri-Sidecar fehlt nach dem Build: ${sidecarPath}`);
}
verifyArtifactManifest(sidecarPath, target, !unsigned && mac);
if (!unsigned) prepareNativeSidecar(sidecarPath, target);
if (mac) {
	stageMacSidecar(sidecarPath, !unsigned);
	const xpcArgs = [resolve(ROOT, 'scripts', 'build-download-xpc.mjs'), '--target', target];
	// Without --identity the script signs ad-hoc with the same entitlements.
	if (release) xpcArgs.push('--identity', process.env.APPLE_SIGNING_IDENTITY);
	run(process.execPath, xpcArgs, ROOT);
}

// Node refuses to spawn npm.cmd without a shell on Windows (CVE-2024-27980:
// spawnSync EINVAL), so the Tauri CLI runs through Node itself, on every
// platform. Tauri still runs beforeBuildCommand through its own shell.
const tauriCli = resolve(APP, 'node_modules', '@tauri-apps', 'cli', 'tauri.js');
if (!existsSync(tauriCli)) fail(`Tauri-CLI fehlt (${tauriCli}); vorher npm ci in app/ ausführen.`);
const tauriArgs = [
	tauriCli,
	'build',
	'--ci',
	'--target',
	target,
	'--config',
	mac ? 'src-tauri/tauri.bundle.macos.conf.json' : 'src-tauri/tauri.bundle.conf.json'
];

let tauriEnvironment = process.env;
if (unsigned) {
	tauriArgs.push('--no-sign');
} else if (adhoc) {
	// Tauri signs ad-hoc with "-" using Entitlements.plist and Hardened Runtime
	// from tauri.macos.conf.json. Without credentials it skips notarization;
	// those variables are therefore removed.
	tauriEnvironment = { ...process.env, APPLE_SIGNING_IDENTITY: '-' };
	for (const name of NOTARIZATION_VARIABLES) delete tauriEnvironment[name];
} else if (target.includes('windows')) {
	// The basic Windows build intentionally lacks signing config so PR-smokes
	// need no certificates. This release entry enables the fail-closed
	// Authenticode command.
	tauriArgs.push('--config', 'src-tauri/tauri.release.windows.conf.json');
}

run(process.execPath, tauriArgs, APP, tauriEnvironment);
if (adhoc) verifyAdhocApp(target);

console.log(
	unsigned
		? `\n  ✓ Unsignierter Distributions-Smoke für ${target} gebaut.\n`
		: adhoc
			? `\n  ✓ Ad-hoc signierte Distribution für ${target} gebaut (Sandbox aktiv, nicht notarisiert).\n`
			: `\n  ✓ Signierte Distribution für ${target} gebaut.\n`
);

function takeFlag(name) {
	const index = args.indexOf(name);
	if (index === -1) return false;
	args.splice(index, 1);
	return true;
}

function takeValue(name) {
	const index = args.indexOf(name);
	if (index === -1) return undefined;
	const value = args[index + 1];
	if (!value || value.startsWith('--')) fail(`${name} braucht einen Wert.`);
	args.splice(index, 2);
	return value;
}

function targetForHost() {
	if (process.platform === 'win32' && process.arch === 'x64') {
		return 'x86_64-pc-windows-msvc';
	}
	if (process.platform === 'darwin' && process.arch === 'arm64') {
		return 'aarch64-apple-darwin';
	}
	if (process.platform === 'darwin' && process.arch === 'x64') {
		return 'x86_64-apple-darwin';
	}
	fail(`Kein Phase-6-Ziel für ${process.platform}/${process.arch}.`);
}

function assertNativeTarget(value) {
	const expected = targetForHost();
	if (value !== expected) {
		fail(
			`PyInstaller baut hostnativ. ${value} kann auf ${process.platform}/${process.arch} ` +
				`nicht reproduzierbar erzeugt werden (erwartet: ${expected}).`
		);
	}
}

function assertReleaseCredentials(value) {
	if (value.includes('windows')) {
		requireEnvironment('WINDOWS_CERTIFICATE_SHA1');
		requireEnvironment('WINDOWS_TIMESTAMP_URL');
		return;
	}

	requireEnvironment('APPLE_SIGNING_IDENTITY');
	const appleIdCredentials = allPresent('APPLE_ID', 'APPLE_PASSWORD', 'APPLE_TEAM_ID');
	const apiKeyCredentials = allPresent('APPLE_API_ISSUER', 'APPLE_API_KEY', 'APPLE_API_KEY_PATH');
	if (!appleIdCredentials && !apiKeyCredentials) {
		fail(
			'Für die Notarisierung fehlt entweder APPLE_ID + APPLE_PASSWORD + APPLE_TEAM_ID ' +
				'oder APPLE_API_ISSUER + APPLE_API_KEY + APPLE_API_KEY_PATH.'
		);
	}
}

function prepareNativeSidecar(path, value) {
	if (value.includes('windows')) {
		run(
			'powershell.exe',
			[
				'-NoLogo',
				'-NoProfile',
				'-NonInteractive',
				'-ExecutionPolicy',
				'Bypass',
				'-File',
				resolve(ROOT, 'scripts', 'sign-windows.ps1'),
				path
			],
			ROOT
		);
		refreshArtifactManifest(path, value, true);
		return;
	}

	// Sign all collected Mach-O files and the outer one-file binary together with
	// SidecarEntitlements.plist. Re-signing here would overwrite those helper entitlements.
	run('codesign', ['--verify', '--deep', '--strict', '--verbose=2', path], ROOT);
	verifyMacHelperSignature(path);
}

function stageMacSidecar(path, requireReleaseSignature) {
	const staged = resolve(APP, 'src-tauri', 'binaries', 'presidio-sidecar');
	copyFileSync(path, staged);
	chmodSync(staged, 0o755);
	if (sha256(staged) !== sha256(path)) {
		fail('Das für den macOS-App-Bundle bereitgestellte Sidecar weicht vom Build-Artefakt ab.');
	}
	if (requireReleaseSignature) {
		run('codesign', ['--verify', '--deep', '--strict', '--verbose=2', staged], ROOT);
		verifyMacHelperSignature(staged);
	}
}

function verifyMacHelperSignature(path) {
	const result = spawnSync('codesign', ['--display', '--entitlements', ':-', '--verbose=4', path], {
		cwd: ROOT,
		encoding: 'utf8',
		env: process.env,
		shell: false
	});
	if (result.error || result.status !== 0) {
		fail(`Sidecar-Entitlements konnten nicht gelesen werden: ${result.error?.message ?? result.stderr}`);
	}
	const output = `${result.stdout ?? ''}\n${result.stderr ?? ''}`;
	for (const entitlement of ['com.apple.security.app-sandbox', 'com.apple.security.inherit']) {
		const escaped = entitlement.replaceAll('.', '\\.');
		const enabled = new RegExp(`<key>${escaped}</key>\\s*<true\\s*/>`).test(output);
		if (!enabled) fail(`Das macOS-Sidecar trägt ${entitlement} nicht.`);
	}
	if (!/flags=.*\bruntime\b/i.test(output)) {
		fail('Das macOS-Sidecar trägt keine Hardened-Runtime-Signatur.');
	}
	// The sidecar inherits the app sandbox but does not request any network rights.
	for (const entitlement of ['com.apple.security.network.client', 'com.apple.security.network.server']) {
		const escaped = entitlement.replaceAll('.', '\\.');
		if (new RegExp(`<key>${escaped}</key>\\s*<true\\s*/>`).test(output)) {
			fail(`Das macOS-Sidecar trägt ${entitlement}.`);
		}
	}
}

/**
 * Review the ad-hoc package: valid signature through helpers, sandbox and
 * network.client, no network.server, signed ad-hoc rather than with an identity.
 */
function verifyAdhocApp(value) {
	const folder = resolve(APP, 'src-tauri', 'target', value, 'release', 'bundle', 'macos');
	const bundles = existsSync(folder) ? readdirSync(folder).filter((name) => name.endsWith('.app')) : [];
	if (bundles.length !== 1) fail(`Genau ein .app-Bundle erwartet in ${folder}, gefunden: ${bundles.length}`);
	const app = resolve(folder, bundles[0]);
	run('codesign', ['--verify', '--deep', '--strict', '--verbose=2', app], ROOT);
	const result = spawnSync('codesign', ['--display', '--entitlements', ':-', '--verbose=4', app], {
		cwd: ROOT,
		encoding: 'utf8',
		shell: false
	});
	if (result.error || result.status !== 0) fail(`App-Signatur unlesbar: ${result.stderr}`);
	const output = `${result.stdout ?? ''}\n${result.stderr ?? ''}`;
	const enabled = (key) => new RegExp(`<key>${key.replaceAll('.', '\\.')}</key>\\s*<true\\s*/>`).test(output);
	for (const key of ['com.apple.security.app-sandbox', 'com.apple.security.network.client']) {
		if (!enabled(key)) fail(`Die App trägt ${key} nicht.`);
	}
	if (enabled('com.apple.security.network.server')) fail('Die App trägt network.server (Phase Z3).');
	if (!/Signature=adhoc/.test(output)) fail('Die App ist nicht ad hoc signiert.');
	if (!/flags=.*\bruntime\b/i.test(output)) fail('Die App trägt keine Hardened Runtime.');
	console.log(`\n  ✓ ${bundles[0]}: ad hoc, Sandbox, network.client, kein network.server.`);
}

function artifactManifestPath(path, value) {
	const suffix = value.includes('windows') ? '.exe' : '';
	const expectedName = `presidio-sidecar-${value}${suffix}`;
	if (basename(path) !== expectedName) {
		fail(`Unerwarteter Sidecar-Name: ${basename(path)} (erwartet: ${expectedName}).`);
	}
	return resolve(path, '..', `presidio-sidecar-${value}.manifest.json`);
}

function verifyArtifactManifest(path, value, requireCodesigned) {
	const manifestPath = artifactManifestPath(path, value);
	if (!existsSync(manifestPath)) fail(`Artefaktmanifest des Sidecars fehlt: ${manifestPath}`);
	let manifest;
	try {
		manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
	} catch (error) {
		fail(`Artefaktmanifest des Sidecars ist ungültig: ${error.message}`);
	}
	if (
		manifest.schema !== 1 ||
		manifest.target !== value ||
		manifest.binary !== basename(path) ||
		manifest.pyinstaller !== 'onefile'
	) {
		fail('Artefaktmanifest und angefordertes Sidecar stimmen nicht überein.');
	}
	if (manifest.sha256 !== sha256(path)) {
		fail('SHA-256 des Sidecars stimmt nicht mit seinem Artefaktmanifest überein.');
	}
	if (requireCodesigned && manifest.codesigned !== true) {
		fail('Das macOS-Release-Sidecar wurde nicht innerhalb des PyInstaller-Builds signiert.');
	}
}

function refreshArtifactManifest(path, value, codesigned) {
	const manifestPath = artifactManifestPath(path, value);
	const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
	manifest.sha256 = sha256(path);
	manifest.codesigned = codesigned;
	writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
}

function sha256(path) {
	return createHash('sha256').update(readFileSync(path)).digest('hex');
}

function allPresent(...names) {
	return names.every((name) => Boolean(process.env[name]?.trim()));
}

function requireEnvironment(name) {
	if (!process.env[name]?.trim()) {
		fail(`${name} fehlt; ein Release wird nicht stillschweigend unsigniert gebaut.`);
	}
}

function run(command, commandArgs, cwd, env = process.env) {
	const result = spawnSync(command, commandArgs, {
		cwd,
		stdio: 'inherit',
		env,
		shell: false
	});
	if (result.error) fail(`${command} konnte nicht gestartet werden: ${result.error.message}`);
	if (result.status !== 0) fail(`${command} ist mit Exit-Code ${result.status ?? 'unbekannt'} fehlgeschlagen.`);
}

function fail(message) {
	console.error(`\n  ✗ ${message}\n`);
	process.exit(1);
}
