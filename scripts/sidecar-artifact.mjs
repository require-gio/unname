/**
 * Shape of the frozen sidecar, shared by build-sidecar.mjs and build-distribution.mjs.
 *
 * macOS gets a PyInstaller onedir folder, Windows a onefile binary. The macOS app
 * runs in the App Sandbox, and a onefile binary cannot start there: its bootloader
 * synchronises with the unpacked child through a System V semaphore, which the
 * sandbox denies ("Failed to initialize sync semaphore!"). A onedir build has no
 * bootloader child and unpacks nothing, so it also starts faster.
 */

import { createHash } from "node:crypto";
import { closeSync, lstatSync, openSync, readFileSync, readSync, readdirSync, readlinkSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

/** Name of the executable inside a onedir folder, and of that folder in the app bundle. */
export const SIDECAR_EXECUTABLE = "presidio-sidecar";

/** "onedir" on macOS, "onefile" everywhere else. */
export function sidecarLayout(target) {
  return target.includes("apple-darwin") ? "onedir" : "onefile";
}

/**
 * SHA-256 of a file, or of a folder tree: every entry's relative path, type,
 * executable bit and content (or symlink target), in sorted order. Two trees
 * hash alike only if a copy kept every file, symlink and permission.
 */
export function artifactSha256(path) {
  const stat = lstatSync(path);
  if (stat.isFile()) return fileSha256(path);
  if (!stat.isDirectory()) throw new Error(`Weder Datei noch Ordner: ${path}`);
  const lines = treeEntries(path).map(({ rel, full, stat: entry }) => {
    if (entry.isSymbolicLink()) return `L ${rel} -> ${readlinkSync(full)}`;
    if (entry.isDirectory()) return `D ${rel}`;
    const executable = (entry.mode & 0o111) !== 0 ? "x" : "-";
    return `F ${rel} ${executable} ${fileSha256(full)}`;
  });
  return createHash("sha256").update(lines.join("\n")).digest("hex");
}

/** `.framework` bundles below a folder (real folders, not symlinks to them). */
export function frameworkBundles(root) {
  return treeEntries(root)
    .filter(({ full, stat }) => stat.isDirectory() && full.endsWith(".framework"))
    .map(({ full }) => full);
}

/** Regular Mach-O files (not symlinks) below a folder, for signature checks. */
export function machOFiles(root) {
  return treeEntries(root)
    .filter(({ full, stat }) => stat.isFile() && isMachO(full))
    .map(({ full }) => full);
}

const MACH_O_MAGICS = new Set([0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe]);
const FAT_MAGICS = new Set([0xcafebabe, 0xcafebabf]);

export function isMachO(path) {
  const header = Buffer.alloc(8);
  const fd = openSync(path, "r");
  let read;
  try {
    read = readSync(fd, header, 0, 8, 0);
  } finally {
    closeSync(fd);
  }
  if (read < 4) return false;
  const magic = header.readUInt32BE(0);
  if (MACH_O_MAGICS.has(magic)) return true;
  // Java class files share 0xcafebabe; there the next word holds the class
  // version (45 or more), in a universal binary the architecture count.
  return FAT_MAGICS.has(magic) && read === 8 && header.readUInt32BE(4) < 45;
}

function treeEntries(root) {
  const entries = [];
  const walk = (folder) => {
    for (const name of readdirSync(folder).sort()) {
      const full = join(folder, name);
      const stat = lstatSync(full);
      entries.push({ rel: relative(root, full).split(sep).join("/"), full, stat });
      if (stat.isDirectory()) walk(full);
    }
  };
  walk(root);
  return entries;
}

function fileSha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

// `node scripts/sidecar-artifact.mjs same A B` exits 0 only if both trees match
// file by file, symlinks and executable bits included. The workflows use it to
// compare the sidecar in the DMG with the staged one; `diff -r` follows the
// framework symlinks and reports loops.
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [command, first, second] = process.argv.slice(2);
  if (command !== "same" || !first || !second) {
    console.error("Aufruf: node scripts/sidecar-artifact.mjs same <pfad> <pfad>");
    process.exit(2);
  }
  process.exit(artifactSha256(first) === artifactSha256(second) ? 0 : 1);
}
