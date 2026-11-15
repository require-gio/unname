#!/usr/bin/env node
/**
 * Build guard for offline delivery (spec §9.6).
 *
 * Checks both source files and the final bundle:
 * - no runtime-loaded stylesheets, scripts or fonts;
 * - each bundled font file must match exactly a declared package;
 * - package version, license ID and stored license text must match.
 *
 * Usage: node scripts/check-no-external-assets.mjs [build-directory]
 */

import {
  existsSync,
  readdirSync,
  readFileSync,
  realpathSync,
  statSync,
} from "node:fs";
import { createHash } from "node:crypto";
import { basename, dirname, extname, join, relative, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const BUILD_DIR = resolve(process.argv[2] ?? join(ROOT, "app", "build"));
const MANIFEST_PATH = join(
  ROOT,
  "THIRD_PARTY_NOTICES",
  "fonts",
  "manifest.json",
);
const SOURCES = [join(ROOT, "app", "src"), join(ROOT, "app", "static")];

const TEXT_EXTENSIONS = new Set([
  ".html",
  ".htm",
  ".css",
  ".js",
  ".mjs",
  ".cjs",
  ".ts",
  ".tsx",
  ".svelte",
  ".json",
  ".svg",
  ".webmanifest",
  ".txt",
]);
const FONT_EXTENSIONS = new Set([".woff", ".woff2", ".ttf", ".otf"]);

/**
 * Rules target runtime dependencies. A plain URL in a license comment is allowed;
 * a browser fetch via import/url/link/script is not. Protocol-relative URLs are also caught.
 */
const VERBOTEN = [
  {
    name: "Google Fonts (CSS-Host)",
    pattern: /(?:https?:)?\/\/fonts\.googleapis\.com/gi,
    notice:
      "Schriften über @fontsource-variable/* einbinden statt über einen CDN.",
  },
  {
    name: "Google Fonts (Dateihost)",
    pattern: /(?:https?:)?\/\/fonts\.gstatic\.com/gi,
    notice: "Schriften über @fontsource-variable/* einbinden statt über einen CDN.",
  },
  {
    name: "@import von einer URL",
    pattern: /@import\s+(?:url\(\s*)?["']?(?:https?:)?\/\//gi,
    notice: "CSS zur Bauzeit einbinden, nicht zur Laufzeit nachladen.",
  },
  {
    name: "url() auf einen fremden Host",
    pattern: /url\(\s*["']?(?:https?:)?\/\//gi,
    notice:
      "Datei als Projektabhängigkeit einbinden; Vite legt sie ins Bundle.",
  },
  {
    name: "<link> auf eine externe Adresse",
    pattern: /<link\b[^>]*\bhref\s*=\s*["'](?:https?:)?\/\//gi,
    notice: "Stylesheets und Schriften gehören ins Bundle.",
  },
  {
    name: "<script> von einer externen Adresse",
    pattern: /<script\b[^>]*\bsrc\s*=\s*["'](?:https?:)?\/\//gi,
    notice: "Skripte gehören ins Bundle.",
  },
];

function* files(directory) {
  if (!existsSync(directory)) return;
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) yield* files(path);
    else if (entry.isFile()) yield path;
  }
}

function lineOf(content, index) {
  return content.slice(0, index).split("\n").length;
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function normalizeLineEndings(content) {
  // The license text must be identical; line-break differences in archives are irrelevant.
  return content.replaceAll("\r\n", "\n").replace(/\s+/g, " ").trim();
}

function within(base, path) {
  const relativ = relative(realpathSync(base), realpathSync(path));
  return (
    relativ !== ".." &&
    !relativ.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`)
  );
}

function checkTextFile(path, findings, isSource) {
  if (!TEXT_EXTENSIONS.has(extname(path).toLowerCase())) return false;
  const content = readFileSync(path, "utf8");
  // Example comments can document the guard itself.
  // In the final bundle such comments are stripped; in source we only remove block/HTML comments.
  const checkContent = isSource
    ? content.replace(/\/\*[\s\S]*?\*\//g, "").replace(/<!--[\s\S]*?-->/g, "")
    : content;
  for (const rule of VERBOTEN) {
    rule.pattern.lastIndex = 0;
    for (const match of checkContent.matchAll(rule.pattern)) {
      findings.push(
        `${relative(ROOT, path)}:${lineOf(checkContent, match.index)} — ${rule.name}\n` +
          `      gefunden: ${match[0].slice(0, 80)}\n` +
          `      ${rule.notice}`,
      );
    }
  }
  return true;
}

function loadFontProofs(findings) {
  if (!existsSync(MANIFEST_PATH)) {
    findings.push(`Lizenzmanifest fehlt: ${relative(ROOT, MANIFEST_PATH)}.`);
    return [];
  }

  let manifest;
  try {
    manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf8"));
  } catch (error_msg) {
    findings.push(`Lizenzmanifest ist kein gültiges JSON: ${error_msg.message}`);
    return [];
  }
  if (
    manifest.schemaVersion !== 1 ||
    !Array.isArray(manifest.fonts) ||
    manifest.fonts.length === 0
  ) {
    findings.push(
      "Lizenzmanifest braucht schemaVersion 1 und mindestens einen fonts-Eintrag.",
    );
    return [];
  }

  const manifestDir = dirname(MANIFEST_PATH);
  return manifest.fonts.flatMap((entry, index) => {
    const label = `fonts[${index}]`;
    const pflichtfelder = [
      "family",
      "package",
      "packageVersion",
      "licenseId",
      "licenseFile",
      "sourceDirectory",
      "bundleNamePattern",
    ];
    for (const field of pflichtfelder) {
      if (typeof entry[field] !== "string" || entry[field].length === 0) {
        findings.push(`${label}.${field} fehlt.`);
        return [];
      }
    }

    let bundleNamePattern;
    try {
      bundleNamePattern = new RegExp(entry.bundleNamePattern, "i");
    } catch (error_msg) {
      findings.push(
        `${label}.bundleNamePattern ist ungültig: ${error_msg.message}`,
      );
      return [];
    }

    const sourceDirectory = resolve(ROOT, entry.sourceDirectory);
    const packageRoot = dirname(sourceDirectory);
    const packageJson = join(packageRoot, "package.json");
    const packageLicense = join(packageRoot, "LICENSE");
    const noticeLicense = resolve(manifestDir, entry.licenseFile);
    for (const path of [
      sourceDirectory,
      packageJson,
      packageLicense,
      noticeLicense,
    ]) {
      if (!existsSync(path)) {
        findings.push(
          `${label}: benötigte Datei fehlt: ${relative(ROOT, path)}.`,
        );
        return [];
      }
    }
    if (!within(join(ROOT, "THIRD_PARTY_NOTICES"), noticeLicense)) {
      findings.push(
        `${label}: licenseFile muss unter THIRD_PARTY_NOTICES liegen.`,
      );
      return [];
    }

    let pkg;
    try {
      pkg = JSON.parse(readFileSync(packageJson, "utf8"));
    } catch (error_msg) {
      findings.push(
        `${label}: package.json ist ungültig: ${error_msg.message}`,
      );
      return [];
    }
    if (
      pkg.name !== entry.package ||
      pkg.version !== entry.packageVersion ||
      pkg.license !== entry.licenseId
    ) {
      findings.push(
        `${label}: installiert ist ${pkg.name}@${pkg.version} (${pkg.license}), ` +
          `deklariert ist ${entry.package}@${entry.packageVersion} (${entry.licenseId}).`,
      );
    }

    const original = normalizeLineEndings(
      readFileSync(packageLicense, "utf8"),
    );
    const proof = normalizeLineEndings(
      readFileSync(noticeLicense, "utf8"),
    );
    if (original !== proof) {
      findings.push(
        `${label}: ${relative(ROOT, noticeLicense)} stimmt nicht inhaltlich ` +
          `mit der Paketlizenz überein.`,
      );
    }

    const sources = [...files(sourceDirectory)].filter((path) =>
      FONT_EXTENSIONS.has(extname(path).toLowerCase()),
    );
    if (sources.length === 0) {
      findings.push(
        `${label}: keine Schriftdateien in ${relative(ROOT, sourceDirectory)}.`,
      );
      return [];
    }
    return [
      {
        ...entry,
        bundleNamePattern,
        proof: relative(ROOT, noticeLicense),
        sourcesByHash: new Map(
          sources.map((path) => [sha256(path), relative(ROOT, path)]),
        ),
        used: 0,
      },
    ];
  });
}

function check() {
  const findings = [];
  let buildStat;
  try {
    buildStat = statSync(BUILD_DIR);
  } catch {
    return {
      findings: [`Kein Build-Verzeichnis unter ${BUILD_DIR}. Zuerst bauen.`],
      checked: 0,
      fontFiles: 0,
      assignments: [],
    };
  }
  if (!buildStat.isDirectory()) {
    return {
      findings: [`${BUILD_DIR} ist kein Verzeichnis.`],
      checked: 0,
      fontFiles: 0,
      assignments: [],
    };
  }

  let checked = 0;
  const fontFiles = [];
  for (const base of [...SOURCES, BUILD_DIR]) {
    const isSource = base !== BUILD_DIR;
    for (const path of files(base)) {
      if (
        base === BUILD_DIR &&
        FONT_EXTENSIONS.has(extname(path).toLowerCase())
      ) {
        fontFiles.push(path);
        continue;
      }
      if (checkTextFile(path, findings, isSource)) checked += 1;
    }
  }

  if (fontFiles.length === 0) {
    findings.push(
      "Keine mitgelieferte Schriftdatei (.woff/.woff2/.ttf/.otf) im Bundle. " +
        "Die lokalen Font-Importe fehlen oder greifen nicht.",
    );
  }

  const proofs = loadFontProofs(findings);
  const assignments = [];
  for (const path of fontFiles) {
    const digest = sha256(path);
    const matching = proofs.filter(
      (proof) =>
        proof.bundleNamePattern.test(basename(path)) &&
        proof.sourcesByHash.has(digest),
    );
    if (matching.length !== 1) {
      findings.push(
        `${relative(ROOT, path)}: ${matching.length === 0 ? "kein" : "kein eindeutiger"} ` +
          "Lizenz- und Herkunftsnachweis im Font-Manifest.",
      );
      continue;
    }
    const proof = matching[0];
    proof.used += 1;
    assignments.push(
      `${relative(ROOT, path)} ← ${proof.sourcesByHash.get(digest)}; ` +
        `${proof.licenseId}: ${proof.proof}`,
    );
  }
  for (const proof of proofs) {
    if (proof.used === 0) {
      findings.push(
        `${proof.package}@${proof.packageVersion}: Manifesteintrag deckt keine ` +
          "gebündelte Schriftdatei ab.",
      );
    }
  }

  return {
    findings,
    checked,
    fontFiles: fontFiles.length,
    assignments,
  };
}

const { findings, checked, fontFiles, assignments } = check();

if (findings.length > 0) {
  console.error(
    "\n  Build abgebrochen: Offline- oder Font-Lizenzprüfung fehlgeschlagen.\n",
  );
  for (const finding of findings) console.error(`  ✗ ${finding}`);
  console.error(`\n  ${findings.length} Befund(e).\n`);
  process.exit(1);
}

console.log(
  `  ✓ Keine externen Laufzeitressourcen. ${checked} Textdatei(en) geprüft, ` +
    `${fontFiles} Schriftdatei(en) mit Lizenznachweis.`,
);
for (const assignment of assignments) console.log(`    · ${assignment}`);
