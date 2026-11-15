#!/usr/bin/env node
/**
 * Build the Python sidecar as a Tauri-compatible PyInstaller one-file binary.
 *
 * This script only orchestrates: asset preparation and PyInstaller run
 * inside the locked sidecar/uv environment. No cross-compilation is performed;
 * each target is built on a runner with the same platform/architecture.
 */

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const SIDECAR = resolve(ROOT, "sidecar");
const DEFAULT_ASSETS = resolve(SIDECAR, "bundle-assets");
const DEFAULT_OUTPUT = resolve(ROOT, "app", "src-tauri", "binaries");
const DEFAULT_ENTITLEMENTS = resolve(
  ROOT,
  "app",
  "src-tauri",
  "SidecarEntitlements.plist",
);

const HELP = `
Sidecar als plattformeigenes Tauri-Binary bauen

Aufruf:
  node scripts/build-sidecar.mjs [Optionen]

Optionen:
  --target TRIPLE            Ziel; Vorgabe wird aus Hostsystem ermittelt
  --target-triple TRIPLE     Kompatibler Alias fuer --target
  --tesseract-root PFAD     Portabler Tesseract-Ordner oder Binary (nicht macOS)
  --vision-ocr PFAD         Fertiger Vision-Helfer (macOS); sonst swift build
  --with-models            Modelle ins Paket (vor Phase I der Standard); dann:
  --gliner-snapshot PFAD    Bereits lokaler, gepinnter GLiNER-Snapshot
  --tessdata-dir PFAD       Ordner mit deu/eng.traineddata (nicht macOS)
  --tessdata-revision REV   Voller Commit-Hash der tessdata-Quelle (nicht macOS)
  --spacy-model PFAD        Optionaler de_core_news_lg-Datenordner
  --assets-dir PFAD         Staging-Ausgabe (Vorgabe sidecar/bundle-assets)
  --output-dir PFAD         Binary-Ausgabe (Vorgabe app/src-tauri/binaries)
  --skip-prepare            Vorhandenes, zielgleiches Asset-Manifest verwenden
  --assets-only             Nach erfolgreicher Asset-Vorbereitung beenden
  --codesign-identity ID    macOS-Identity (sonst APPLE_SIGNING_IDENTITY)
  --entitlements PFAD       Sidecar-Entitlements fuer PyInstaller/codesign
  --require-signing         macOS-Build ohne echte Identity ablehnen
  --print-target            Ermitteltes Host-Triple ausgeben und beenden
  -h, --help                Diese Hilfe anzeigen

Seit Phase I enthaelt das Sidecar keine Modelle: Die App laedt sie bei der
Ersteinrichtung und prueft sie gegen app/src-tauri/models.lock.json. Im Paket
bleibt Tesseract als Programm – ausser auf macOS: Dort erkennt seit Phase U
Apple Vision, und das Paket traegt den Helfer unname-vision-ocr
(vision-ocr/, swift build -c release --package-path vision-ocr).

Alle Asset-Optionen koennen auch ueber PRESIDIO_TESSERACT_ROOT,
PRESIDIO_BUNDLE_MODELS=1, PRESIDIO_GLINER_SNAPSHOT, PRESIDIO_TESSDATA_DIR und
PRESIDIO_TESSDATA_REVISION gesetzt werden. PRESIDIO_ASSET_TRUST_LOCK und
PRESIDIO_ASSET_TRUST_ID binden ihre Quellbaum-Hashes an den reviewten
Repository-Lock. Die Asset-Vorbereitung fuehrt keine Downloads aus.
`;

function fail(message) {
  console.error(`Sidecar-Build fehlgeschlagen: ${message}`);
  process.exit(2);
}

function valueAfter(argv, index, option) {
  const value = argv[index + 1];
  if (!value || value.startsWith("--")) fail(`${option} braucht einen Wert.`);
  return value;
}

function parseArgs(argv) {
  const options = {
    target: null,
    glinerSnapshot: null,
    tesseractRoot: null,
    visionOcr: null,
    tessdataDirectory: null,
    tessdataRevision: null,
    spacyModel: null,
    withModels: process.env.PRESIDIO_BUNDLE_MODELS === "1",
    assetsDirectory: DEFAULT_ASSETS,
    outputDirectory: DEFAULT_OUTPUT,
    codesignIdentity:
      process.env.PRESIDIO_CODESIGN_IDENTITY ??
      process.env.APPLE_SIGNING_IDENTITY ??
      null,
    entitlements: null,
    skipPrepare: false,
    assetsOnly: false,
    requireSigning: process.env.PRESIDIO_REQUIRE_CODESIGN === "1",
    printTarget: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const option = argv[index];
    if (option === "-h" || option === "--help") {
      console.log(HELP.trim());
      process.exit(0);
    }
    if (option === "--skip-prepare") options.skipPrepare = true;
    else if (option === "--with-models") options.withModels = true;
    else if (option === "--assets-only") options.assetsOnly = true;
    else if (option === "--require-signing") options.requireSigning = true;
    else if (option === "--print-target") options.printTarget = true;
    else if (option === "--target" || option === "--target-triple")
      options.target = valueAfter(argv, index++, option);
    else if (option === "--gliner-snapshot")
      options.glinerSnapshot = valueAfter(argv, index++, option);
    else if (option === "--tesseract-root")
      options.tesseractRoot = valueAfter(argv, index++, option);
    else if (option === "--vision-ocr")
      options.visionOcr = resolve(valueAfter(argv, index++, option));
    else if (option === "--tessdata-dir")
      options.tessdataDirectory = valueAfter(argv, index++, option);
    else if (option === "--tessdata-revision")
      options.tessdataRevision = valueAfter(argv, index++, option);
    else if (option === "--spacy-model")
      options.spacyModel = valueAfter(argv, index++, option);
    else if (option === "--assets-dir")
      options.assetsDirectory = resolve(valueAfter(argv, index++, option));
    else if (option === "--output-dir")
      options.outputDirectory = resolve(valueAfter(argv, index++, option));
    else if (option === "--codesign-identity")
      options.codesignIdentity = valueAfter(argv, index++, option);
    else if (option === "--entitlements")
      options.entitlements = resolve(valueAfter(argv, index++, option));
    else fail(`Unbekannte Option ${option}`);
  }
  return options;
}

function hostTriple() {
  const key = `${process.platform}/${process.arch}`;
  const triples = {
    "darwin/arm64": "aarch64-apple-darwin",
    "darwin/x64": "x86_64-apple-darwin",
    "win32/x64": "x86_64-pc-windows-msvc",
    "win32/arm64": "aarch64-pc-windows-msvc",
    "linux/x64": "x86_64-unknown-linux-gnu",
    "linux/arm64": "aarch64-unknown-linux-gnu",
  };
  const triple = triples[key];
  if (!triple) fail(`Nicht unterstuetztes Hostsystem ${key}.`);
  return triple;
}

function run(command, args, environment = {}) {
  const result = spawnSync(command, args, {
    cwd: ROOT,
    env: { ...process.env, ...environment },
    stdio: "inherit",
    shell: false,
  });
  if (result.error) fail(`${command} konnte nicht gestartet werden: ${result.error.message}`);
  if (result.status !== 0) fail(`${command} endete mit Code ${result.status}.`);
}

function addOption(args, option, value) {
  if (value) args.push(option, value);
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

const options = parseArgs(process.argv.slice(2));
const host = hostTriple();
if (options.printTarget) {
  console.log(host);
  process.exit(0);
}
const target = options.target ?? process.env.TARGET_TRIPLE ?? host;
if (target !== host) {
  fail(
    `PyInstaller kann nicht cross-kompilieren (Host ${host}, Ziel ${target}). ` +
      "Einen passenden Runner verwenden.",
  );
}

if (!options.skipPrepare) {
  const prepare = [
    "run",
    "--project",
    SIDECAR,
    "--frozen",
    "python",
    resolve(ROOT, "scripts", "prepare-sidecar-assets.py"),
    "--target",
    target,
    "--destination",
    options.assetsDirectory,
  ];
  addOption(prepare, "--tesseract-root", options.tesseractRoot);
  addOption(prepare, "--vision-ocr", options.visionOcr);
  if (options.withModels) {
    prepare.push("--with-models");
    addOption(prepare, "--gliner-snapshot", options.glinerSnapshot);
    addOption(prepare, "--tessdata-dir", options.tessdataDirectory);
    addOption(prepare, "--tessdata-revision", options.tessdataRevision);
    addOption(prepare, "--spacy-model", options.spacyModel);
  } else if (
    options.glinerSnapshot ||
    options.tessdataDirectory ||
    options.spacyModel
  ) {
    fail("Modellquellen nur zusammen mit --with-models.");
  }
  run("uv", prepare);
}

const assetManifest = resolve(options.assetsDirectory, "asset-manifest.json");
if (!existsSync(assetManifest)) fail(`Asset-Manifest fehlt: ${assetManifest}`);
const assetMetadata = JSON.parse(readFileSync(assetManifest, "utf8"));
if (assetMetadata.schema !== 1 || assetMetadata.target !== target) {
  fail(`Asset-Manifest passt nicht zum Ziel ${target}.`);
}
if (options.assetsOnly) process.exit(0);

let entitlements = options.entitlements;
if (
  process.platform === "darwin" &&
  options.codesignIdentity &&
  !entitlements &&
  existsSync(DEFAULT_ENTITLEMENTS)
) {
  entitlements = DEFAULT_ENTITLEMENTS;
}
if (
  process.platform === "darwin" &&
  options.requireSigning &&
  (!options.codesignIdentity || options.codesignIdentity === "-")
) {
  fail("Release-macOS-Build braucht --codesign-identity/APPLE_SIGNING_IDENTITY.");
}
if (process.platform === "darwin" && options.codesignIdentity && !entitlements) {
  fail("Signierter macOS-Sidecar braucht SidecarEntitlements.plist.");
}
if (entitlements && !existsSync(entitlements)) fail(`Entitlements fehlen: ${entitlements}`);

mkdirSync(options.outputDirectory, { recursive: true });
const workDirectory = resolve(SIDECAR, ".pyinstaller", target);
const binaryBase = `presidio-sidecar-${target}`;
const pyinstaller = [
  "run",
  "--project",
  SIDECAR,
  "--frozen",
  "pyinstaller",
  "--clean",
  "--noconfirm",
  "--distpath",
  options.outputDirectory,
  "--workpath",
  workDirectory,
  resolve(SIDECAR, "presidio-sidecar.spec"),
];
const buildEnvironment = {
  PRESIDIO_SIDECAR_TARGET: target,
  PRESIDIO_SIDECAR_BINARY_NAME: binaryBase,
  PRESIDIO_SIDECAR_ASSETS: resolve(options.assetsDirectory),
  PRESIDIO_CODESIGN_IDENTITY: options.codesignIdentity ?? "",
  PRESIDIO_ENTITLEMENTS_FILE: entitlements ?? "",
  PYTHONHASHSEED: "0",
  SOURCE_DATE_EPOCH: process.env.SOURCE_DATE_EPOCH ?? "0",
};
run("uv", pyinstaller, buildEnvironment);

const executable = resolve(
  options.outputDirectory,
  process.platform === "win32" ? `${binaryBase}.exe` : binaryBase,
);
if (!existsSync(executable)) fail(`PyInstaller-Ausgabe fehlt: ${executable}`);

if (process.platform === "darwin" && options.codesignIdentity) {
  run("codesign", ["--verify", "--deep", "--strict", "--verbose=2", executable]);
}

const artifactManifest = {
  schema: 1,
  target,
  binary: basename(executable),
  sha256: sha256(executable),
  asset_manifest_sha256: sha256(assetManifest),
  codesigned: Boolean(process.platform === "darwin" && options.codesignIdentity),
  pyinstaller: "onefile",
};
const artifactManifestPath = resolve(
  options.outputDirectory,
  `${binaryBase}.manifest.json`,
);
writeFileSync(
  artifactManifestPath,
  `${JSON.stringify(artifactManifest, null, 2)}\n`,
  "utf8",
);

console.log(`Sidecar-Binary gebaut: ${executable}`);
console.log(`Artefaktmanifest: ${artifactManifestPath}`);
