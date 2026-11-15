#!/usr/bin/env node
/**
 * Build the download service `org.unname.desktop.download.xpc` and place it in
 * app/src-tauri/binaries/. From there Tauri copies it via `bundle.macOS.files`
 * to Contents/XPCServices.
 *
 * Sign the service here before Tauri signs the app: with its own sandbox and
 * `network.client`, Hardened Runtime and timestamp. Without identity (unsigned
 * smoke, development) sign ad-hoc with the same entitlements so the sandbox
 * also works locally.
 *
 * Usage:
 *   node scripts/build-download-xpc.mjs --target aarch64-apple-darwin
 *        [--identity "Developer ID Application: …"]
 */

import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const ROOT = resolve(import.meta.dirname, '..');
const PACKAGE = resolve(ROOT, 'download-xpc');
export const SERVICE_NAME = 'org.unname.desktop.download';
const BUNDLE = resolve(ROOT, 'app', 'src-tauri', 'binaries', `${SERVICE_NAME}.xpc`);
const ENTITLEMENTS = resolve(PACKAGE, 'Entitlements.plist');

const ARCHITECTURES = {
	'aarch64-apple-darwin': 'arm64',
	'x86_64-apple-darwin': 'x86_64'
};

const args = process.argv.slice(2);
const target = valueOf('--target');
const identity = valueOf('--identity') ?? '-';
const architecture = ARCHITECTURES[target];
if (!architecture) fail(`Kein macOS-Ziel: ${target ?? '(fehlt)'}`);

const swiftArgs = ['build', '-c', 'release', '--package-path', PACKAGE, '--arch', architecture];
run('swift', swiftArgs);
// The output location depends on the SwiftPM version; SwiftPM names it itself.
const binPath = spawnSync('swift', [...swiftArgs, '--show-bin-path'], { encoding: 'utf8' });
if (binPath.status !== 0) fail(`swift build --show-bin-path: ${binPath.stderr}`);
const built = resolve(binPath.stdout.trim(), 'unname-download');
if (!existsSync(built)) fail(`Gebauter Dienst fehlt: ${built}`);

rmSync(BUNDLE, { recursive: true, force: true });
mkdirSync(resolve(BUNDLE, 'Contents', 'MacOS'), { recursive: true });
cpSync(built, resolve(BUNDLE, 'Contents', 'MacOS', 'unname-download'));
const version = JSON.parse(
	readFileSync(resolve(ROOT, 'app', 'src-tauri', 'tauri.conf.json'), 'utf8')
).version;
const plist = readFileSync(resolve(PACKAGE, 'Info.plist'), 'utf8').replaceAll(
	'<string>0.0.0</string>',
	`<string>${version}</string>`
);
writeFileSync(resolve(BUNDLE, 'Contents', 'Info.plist'), plist, 'utf8');

const signing = ['--force', '--options', 'runtime', '--entitlements', ENTITLEMENTS, '--sign', identity];
if (identity !== '-') signing.push('--timestamp');
run('codesign', [...signing, BUNDLE]);
run('codesign', ['--verify', '--strict', '--verbose=2', BUNDLE]);
verifyEntitlements(BUNDLE);

console.log(`\n  ✓ ${SERVICE_NAME}.xpc für ${target} gebaut (${identity === '-' ? 'ad hoc' : 'signiert'}).\n`);

/** Exact sandbox and network.client – nothing else, especially no inherit. */
function verifyEntitlements(path) {
	const result = spawnSync('codesign', ['--display', '--entitlements', ':-', path], {
		encoding: 'utf8'
	});
	if (result.status !== 0) fail(`Entitlements des Dienstes unlesbar: ${result.stderr}`);
	const keys = [...result.stdout.matchAll(/<key>([^<]+)<\/key>\s*<true\s*\/>/g)].map((m) => m[1]);
	const expected = ['com.apple.security.app-sandbox', 'com.apple.security.network.client'];
	if (keys.sort().join(',') !== expected.sort().join(',')) {
		fail(`Der Dienst trägt unerwartete Entitlements: ${keys.join(', ')}`);
	}
}

function valueOf(name) {
	const index = args.indexOf(name);
	if (index === -1) return undefined;
	const value = args[index + 1];
	if (!value || value.startsWith('--')) fail(`${name} braucht einen Wert.`);
	return value;
}

function run(command, commandArgs) {
	const result = spawnSync(command, commandArgs, { cwd: ROOT, stdio: 'inherit', shell: false });
	if (result.error) fail(`${command} konnte nicht gestartet werden: ${result.error.message}`);
	if (result.status !== 0) fail(`${command} ist mit Exit-Code ${result.status} fehlgeschlagen.`);
}

function fail(message) {
	console.error(`\n  ✗ ${message}\n`);
	process.exit(1);
}
