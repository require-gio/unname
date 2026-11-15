#!/usr/bin/env node
/**
 * Verify that only design tokens are used for colors.
 *
 * All colors must come from `app/src/lib/styles/tokens.css`. The script flags any
 * occurrences in other `app/src` files:
 * - Hex literals (`#4338ca`) and color functions (`rgb(…)`, `oklch(…)` …);
 * - Tailwind utility classes from the default palette (`bg-red-500`, `text-white` …) —
 *   the palette is disabled in `theme.css`, so these classes produce no CSS;
 * - `dark:` usage in components — dark mode only swaps tokens.
 *
 * ESLint (`svelte/no-inline-styles`) reports inline styles in Svelte files.
 *
 * Excludes: test files (they rely on color values), the color math in
 * `theme/color.ts`, and image assets.
 *
 * Run with: node scripts/check-design-tokens.mjs
 */

import { readdirSync, readFileSync } from "node:fs";
import { extname, join, relative, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const SRC = join(ROOT, "app", "src");
const TOKENS = join(SRC, "lib", "styles", "tokens.css");

const ENDINGS = new Set([".svelte", ".ts", ".js", ".css"]);

/** Paths relative to `app/src` that are excluded, with reasons. */
const EXCEPTIONS = [
  { pattern: /\.(spec|test)\.ts$/, reason: "Tests rechnen mit Farbwerten" },
  { pattern: /^lib\/theme\/color\.ts$/, reason: "Farbmathematik" },
];

const PALETTE =
  "red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone";
const UTILITY =
  "bg|text|border|border-[trblxy]|ring|ring-offset|outline|fill|stroke|from|via|to|shadow|divide|placeholder|accent|caret|decoration";

const RULES = [
  {
    name: "Hex-Farbwert",
    pattern:
      /(?<![\w&/#-])#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})(?![\w-])/gi,
    notice: "Ein Token aus tokens.css verwenden (bg-surface, text-muted …).",
  },
  {
    name: "Farbfunktion",
    pattern: /(?<![\w-])(?:rgba?|hsla?|oklch|oklab|lab|lch|hwb)\(/gi,
    notice: "Farbwerte gehören nach tokens.css.",
  },
  {
    name: "Klasse der Standardpalette",
    pattern: new RegExp(
      `(?<![\\w-])(?:${UTILITY})-(?:(?:${PALETTE})-\\d{2,3}|black|white)(?![\\w-])`,
      "g",
    ),
    notice:
      "Die Palette ist abgeschaltet; ein semantisches Token verwenden (bg-red-1, text-on-ink …).",
  },
  {
    name: "dark:-Variante",
    pattern: /(?<![\w-])dark:/g,
    notice: "Der Dunkelmodus tauscht nur Tokens; kein dark: in Komponenten.",
    nur: /\.svelte$/,
  },
];

function* files(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) yield* files(path);
    else if (entry.isFile() && ENDINGS.has(extname(entry.name)))
      yield path;
  }
}

/**
 * Comments are ignored: they may contain prohibited content and explanations.
 * They are replaced with spaces to keep line numbers accurate.
 */
function without_comments(content) {
  const blank_out = (text) => text.replace(/[^\n]/g, " ");
  return content
    .replace(/\/\*[\s\S]*?\*\//g, blank_out)
    .replace(/<!--[\s\S]*?-->/g, blank_out)
    .replace(/^\s*\/\/.*$/gm, blank_out);
}

function line_of(content, index) {
  return content.slice(0, index).split("\n").length;
}

const findings = [];
let checked = 0;

for (const path of files(SRC)) {
  if (path === TOKENS) continue;
  const relativ = relative(SRC, path).split("\\").join("/");
  if (EXCEPTIONS.some((ausnahme) => ausnahme.pattern.test(relativ))) continue;
  checked += 1;
  const content = without_comments(readFileSync(path, "utf8"));
  for (const rule of RULES) {
    if (rule.nur && !rule.nur.test(relativ)) continue;
    rule.pattern.lastIndex = 0;
    for (const match of content.matchAll(rule.pattern)) {
      findings.push(
        `app/src/${relativ}:${line_of(content, match.index)} — ${rule.name}: ${match[0]}\n` +
          `      ${rule.notice}`,
      );
    }
  }
}

if (findings.length > 0) {
  console.error("\n  Design-System verletzt:\n");
  for (const finding of findings) console.error(`  ✗ ${finding}`);
  console.error(`\n  ${findings.length} Befund(e).\n`);
  process.exit(1);
}

console.log(
  `  ✓ Farben nur aus tokens.css, keine Palettenklassen, kein dark: — ${checked} Datei(en) geprüft.`,
);
