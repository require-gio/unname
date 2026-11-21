#!/usr/bin/env node
/**
 * Write one version into every file that declares the app or sidecar version.
 *
 * The release pipeline stamps the checkout before it builds; the repository
 * itself keeps its development version, and the tag records what was
 * released. To rebuild a release locally, check out its tag and run this with
 * the tag's version first.
 *
 * Usage:
 *   node scripts/release/set-version.mjs 0.2.0
 *   node scripts/release/set-version.mjs --print
 *
 * Every pattern must match exactly once, so a reformatted file fails loudly
 * instead of shipping with a stale version. `\r?\n` keeps it working on a
 * Windows checkout with CRLF line endings.
 */

import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const ROOT = resolve(import.meta.dirname, "..", "..");
const VERSION = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

/** File → patterns whose second group is the version. */
export const LOCATIONS = Object.freeze([
  ["app/package.json", [/^(\t"version": ")([^"]*)(")/m]],
  [
    "app/package-lock.json",
    [
      /^(\t"version": ")([^"]*)(")/m,
      /^(\t\t"": \{\r?\n\t\t\t"name": "app",\r?\n\t\t\t"version": ")([^"]*)(")/m,
    ],
  ],
  ["app/src-tauri/tauri.conf.json", [/^(\t"version": ")([^"]*)(")/m]],
  ["app/src-tauri/Cargo.toml", [/^(\[package\]\r?\nname = "app"\r?\nversion = ")([^"]*)(")/m]],
  ["app/src-tauri/Cargo.lock", [/^(\[\[package\]\]\r?\nname = "app"\r?\nversion = ")([^"]*)(")/m]],
  [
    "sidecar/pyproject.toml",
    [/^(\[project\]\r?\nname = "presidio-sidecar"\r?\nversion = ")([^"]*)(")/m],
  ],
  [
    "sidecar/uv.lock",
    [/^(\[\[package\]\]\r?\nname = "presidio-sidecar"\r?\nversion = ")([^"]*)(")/m],
  ],
  ["sidecar/src/presidio_sidecar/__init__.py", [/^(__version__ = ")([^"]*)(")/m]],
]);

function only(text, pattern, file) {
  const global = new RegExp(pattern.source, `${pattern.flags}g`);
  const count = [...text.matchAll(global)].length;
  if (count !== 1) {
    throw new Error(`${file}: expected one version declaration, found ${count} (${pattern}).`);
  }
}

/** Current version per file and pattern, e.g. {"app/package.json": ["0.0.1"]}. */
export function readVersions(root = ROOT) {
  const versions = {};
  for (const [file, patterns] of LOCATIONS) {
    const text = readFileSync(resolve(root, file), "utf8");
    versions[file] = patterns.map((pattern) => {
      only(text, pattern, file);
      return pattern.exec(text)[2];
    });
  }
  return versions;
}

export function setVersion(version, root = ROOT) {
  if (!VERSION.test(version)) {
    throw new Error(`"${version}" is not a plain major.minor.patch version.`);
  }
  // Check every file before writing any, so a mismatch leaves the tree untouched.
  const updates = LOCATIONS.map(([file, patterns]) => {
    const path = resolve(root, file);
    let text = readFileSync(path, "utf8");
    for (const pattern of patterns) {
      only(text, pattern, file);
      text = text.replace(pattern, (_match, before, _old, after) => before + version + after);
    }
    return [path, text];
  });
  for (const [path, text] of updates) writeFileSync(path, text, "utf8");
  const stale = Object.entries(readVersions(root)).filter(([, found]) =>
    found.some((value) => value !== version),
  );
  if (stale.length > 0) {
    throw new Error(`Version not applied in: ${stale.map(([file]) => file).join(", ")}.`);
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  try {
    const [argument] = process.argv.slice(2);
    if (!argument) throw new Error("Usage: set-version.mjs <major.minor.patch> | --print");
    if (argument !== "--print") setVersion(argument);
    for (const [file, found] of Object.entries(readVersions())) {
      console.log(`${found.join(", ").padEnd(10)} ${file}`);
    }
  } catch (error) {
    console.error(`::error::${error.message}`);
    process.exit(1);
  }
}
