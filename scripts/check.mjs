#!/usr/bin/env node
/**
 * Run all automated checks for the project.
 *
 * One command, one result: get repository state without remembering several steps.
 *
 * Usage: node scripts/check.mjs [--fast]
 *   --fast  skip live gate, builds, browser, process, and Rust tests
 */

import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const APP = resolve(ROOT, "app");
const SIDECAR = resolve(ROOT, "sidecar");
const TAURI = resolve(APP, "src-tauri");
const QUALITY_TMP = mkdtempSync(join(tmpdir(), "presidio-quality-"));
const QUALITY_PREDICTIONS = resolve(QUALITY_TMP, "predictions");
const QUALITY_REPORT = resolve(QUALITY_TMP, "report.json");

const fast = process.argv.includes("--fast");

/** @type {{name: string, cwd: string, cmd: string, args: string[], lang?: boolean}[]} */
const STEPS = [
  {
    name: "Sidecar: Formatierung",
    cwd: SIDECAR,
    cmd: "uv",
    args: [
      "run",
      "ruff",
      "format",
      "--check",
      ".",
      "../scripts/quality",
      "../scripts/prepare-sidecar-assets.py",
      "../scripts/llm-round-trip.py",
      "../scripts/performance.py",
      "../scripts/draft-measurement.py",
      "../scripts/layout-corpus.py",
      "../scripts/models",
      "../scripts/second_opinion",
    ],
  },
  {
    name: "Sidecar: Lint",
    cwd: SIDECAR,
    cmd: "uv",
    args: [
      "run",
      "ruff",
      "check",
      ".",
      "../scripts/quality",
      "../scripts/prepare-sidecar-assets.py",
      "../scripts/llm-round-trip.py",
      "../scripts/performance.py",
      "../scripts/draft-measurement.py",
      "../scripts/layout-corpus.py",
      "../scripts/models",
      "../scripts/second_opinion",
    ],
  },
  {
    name: "Sidecar: Tests (Sicherheit, Erkennung, Verarbeitung, Tresor, Prozess)",
    cwd: SIDECAR,
    cmd: "uv",
    args: ["run", "pytest", "-q"],
    lang: true,
  },
  {
    name: "Qualität: Messgeschirr testen",
    cwd: ROOT,
    cmd: "uv",
    args: [
      "run",
      "--project",
      "sidecar",
      "--frozen",
      "python",
      "-m",
      "unittest",
      "discover",
      "-s",
      "scripts/quality/tests",
      "-v",
    ],
  },
  {
    name: "Qualität: versionierten 3×200-Korpus validieren",
    cwd: ROOT,
    cmd: "uv",
    args: [
      "run",
      "--project",
      "sidecar",
      "--frozen",
      "python",
      "-m",
      "scripts.quality.gate",
      "validate",
      "--catalog",
      "testdata/quality/synthetic-v1/catalog.json",
      "--minimum-documents",
      "200",
    ],
  },
  {
    name: "Qualität: Live-Erkennung mit und ohne Stammdaten",
    cwd: ROOT,
    cmd: "uv",
    args: [
      "run",
      "--project",
      "sidecar",
      "--frozen",
      "python",
      "-m",
      "scripts.quality.run_sidecar",
      "--catalog",
      "testdata/quality/synthetic-v1/catalog.json",
      "--profiles",
      "profiles",
      "--engine",
      "model-free",
      "--output",
      QUALITY_PREDICTIONS,
    ],
    lang: true,
  },
  {
    name: "Qualität: Recall-Regressionsgate",
    cwd: ROOT,
    cmd: "uv",
    args: [
      "run",
      "--project",
      "sidecar",
      "--frozen",
      "python",
      "-m",
      "scripts.quality.gate",
      "evaluate",
      "--catalog",
      "testdata/quality/synthetic-v1/catalog.json",
      "--predictions",
      QUALITY_PREDICTIONS,
      "--config",
      "testdata/quality/gates/synthetic.json",
      "--baseline",
      "testdata/quality/baseline.synthetic.json",
      "--output",
      QUALITY_REPORT,
    ],
    lang: true,
  },
  {
    name: "Release: Versionslogik und Versionsstempel",
    cwd: ROOT,
    cmd: "node",
    args: ["--test", "scripts/release/*.test.mjs"],
  },
  {
    name: "Vertrag: API-Typen v2 aktuell (Pydantic → Schema → api.gen.ts)",
    cwd: ROOT,
    cmd: "node",
    args: ["scripts/api-types.mjs", "--check"],
  },
  {
    name: "Oberfläche: Typprüfung",
    cwd: APP,
    cmd: "npm",
    args: ["run", "check"],
    lang: true,
  },
  {
    name: "Oberfläche: Lint, Formatierung und Design-Tokens",
    cwd: APP,
    cmd: "npm",
    args: ["run", "lint"],
  },
  {
    name: "Oberfläche: Tests (Fail-Closed, API-Client, Etiketten, Prüfen, Zurückübersetzen, Prüfbericht, Kontrast, Wörterbuch, Maskierung)",
    cwd: APP,
    cmd: "npm",
    args: ["run", "test"],
  },
  {
    name: "Oberfläche: Build und Guard gegen externe Ressourcen",
    cwd: APP,
    cmd: "npm",
    args: ["run", "build:guarded"],
    lang: true,
  },
  {
    name: "Oberfläche: Abläufe, axe je Board, Referenzbilder, Leistung, Offline (Playwright)",
    cwd: APP,
    cmd: "npm",
    args: ["run", "test:smoke"],
    lang: true,
  },
  {
    name: "Hülle: Formatierung",
    cwd: TAURI,
    cmd: "cargo",
    args: ["fmt", "--check"],
  },
  {
    name: "Hülle: Lint",
    cwd: TAURI,
    cmd: "cargo",
    args: ["clippy", "--all-targets", "--", "-D", "warnings"],
    lang: true,
  },
  {
    name: "Hülle: Tests (Sperre, Eingang, Speichern, Modelle)",
    cwd: TAURI,
    cmd: "cargo",
    args: ["test"],
    lang: true,
  },
];

const selected = fast
  ? STEPS.filter((step) => !step.lang)
  : STEPS;

console.log(
  `\n  ${selected.length} Prüfschritte${fast ? " (schnell)" : ""}\n`,
);

const failed = [];

for (const step of selected) {
  process.stdout.write(`  … ${step.name}\n`);
  const result = spawnSync(step.cmd, step.args, {
    cwd: step.cwd,
    stdio: "inherit",
    shell: process.platform === "win32",
  });

  if (result.status !== 0) {
    failed.push(step.name);
    console.error(`  ✗ ${step.name}\n`);
  } else {
    console.log(`  ✓ ${step.name}\n`);
  }
}

rmSync(QUALITY_TMP, { recursive: true, force: true });

if (failed.length > 0) {
  console.error(`\n  ${failed.length} Schritt(e) fehlgeschlagen:`);
  for (const name of failed) console.error(`    ✗ ${name}`);
  console.error("");
  process.exit(1);
}

console.log("  Alle automatisierten Prüfungen bestanden.\n");
console.log("  Noch ausstehende externe Abnahmen:");
console.log(
  "    1. Netzwerkmitschnitt der laufenden nativen App auf ausgehende Aufrufe prüfen (A-12)",
);
console.log(
  '       (macOS: sudo tcpdump -i any -n "not host 127.0.0.1 and not host ::1").',
);
console.log(
  "    2. Paket auf einem echten Windows-11-x64-Gerät mit 2 Kernen und 8 GB RAM abnehmen (A-14).",
);
console.log(
  "    3. Erkennung an mindestens 200 realen, vorab pseudonymisierten Dokumenten messen (A-22).",
);
console.log("       Details: acceptance/spec-11.json, docs/roadmap.md");
console.log("");
