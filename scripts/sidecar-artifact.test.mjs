/**
 * Tests for the sidecar layout rule, the folder hash and Mach-O detection.
 *
 * Run: node --test "scripts/*.test.mjs"
 */

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  artifactSha256,
  frameworkBundles,
  isMachO,
  machOFiles,
  sidecarLayout,
} from "./sidecar-artifact.mjs";

function folder(build) {
  const root = mkdtempSync(join(tmpdir(), "sidecar-artifact-"));
  build(root);
  return root;
}

function onedir(root) {
  mkdirSync(join(root, "_internal", "Python.framework"), { recursive: true });
  writeFileSync(join(root, "presidio-sidecar"), "exe");
  chmodSync(join(root, "presidio-sidecar"), 0o755);
  writeFileSync(join(root, "_internal", "Python.framework", "Python"), "lib");
  symlinkSync("Python.framework/Python", join(root, "_internal", "Python"));
}

test("macOS gets onedir, every other target onefile", () => {
  assert.equal(sidecarLayout("aarch64-apple-darwin"), "onedir");
  assert.equal(sidecarLayout("x86_64-apple-darwin"), "onedir");
  assert.equal(sidecarLayout("x86_64-pc-windows-msvc"), "onefile");
});

test("identical trees hash alike, wherever they live", (t) => {
  const first = folder(onedir);
  const second = folder(onedir);
  t.after(() => [first, second].forEach((path) => rmSync(path, { recursive: true, force: true })));
  assert.equal(artifactSha256(first), artifactSha256(second));
});

test("a resolved symlink, a lost executable bit or changed content change the hash", (t) => {
  const reference = folder(onedir);
  const resolved = folder((root) => {
    onedir(root);
    rmSync(join(root, "_internal", "Python"));
    writeFileSync(join(root, "_internal", "Python"), "lib");
  });
  const notExecutable = folder((root) => {
    onedir(root);
    chmodSync(join(root, "presidio-sidecar"), 0o644);
  });
  const changed = folder((root) => {
    onedir(root);
    writeFileSync(join(root, "_internal", "Python.framework", "Python"), "other");
  });
  t.after(() =>
    [reference, resolved, notExecutable, changed].forEach((path) =>
      rmSync(path, { recursive: true, force: true }),
    ),
  );
  const expected = artifactSha256(reference);
  for (const other of [resolved, notExecutable, changed]) {
    assert.notEqual(artifactSha256(other), expected);
  }
});

test("the same command exits 0 only for matching trees", (t) => {
  const first = folder(onedir);
  const second = folder(onedir);
  const other = folder((root) => {
    onedir(root);
    chmodSync(join(root, "presidio-sidecar"), 0o644);
  });
  t.after(() =>
    [first, second, other].forEach((path) => rmSync(path, { recursive: true, force: true })),
  );
  const script = join(import.meta.dirname, "sidecar-artifact.mjs");
  const same = (a, b) => spawnSync(process.execPath, [script, "same", a, b]).status;
  assert.equal(same(first, second), 0);
  assert.equal(same(first, other), 1);
  assert.equal(spawnSync(process.execPath, [script, "same", first]).status, 2);
});

test("frameworks are found as folders, never through a symlink", (t) => {
  const root = folder((path) => {
    onedir(path);
    symlinkSync("_internal/Python.framework", join(path, "Alias.framework"));
  });
  t.after(() => rmSync(root, { recursive: true, force: true }));
  assert.deepEqual(frameworkBundles(root), [join(root, "_internal", "Python.framework")]);
});

test("Mach-O detection reads the magic, not the name", (t) => {
  const root = folder((path) => {
    const write = (name, words) => {
      const header = Buffer.alloc(8);
      header.writeUInt32BE(words[0], 0);
      header.writeUInt32BE(words[1], 4);
      writeFileSync(join(path, name), header);
    };
    write("thin", [0xcffaedfe, 0]); // 64-bit little endian, as on arm64
    write("universal", [0xcafebabe, 2]); // two architectures
    write("Java.class", [0xcafebabe, 52]); // class file version 52
    writeFileSync(join(path, "script.py"), "print('hallo')\n");
    symlinkSync("thin", join(path, "link"));
  });
  t.after(() => rmSync(root, { recursive: true, force: true }));
  assert.equal(isMachO(join(root, "thin")), true);
  assert.equal(isMachO(join(root, "universal")), true);
  assert.equal(isMachO(join(root, "Java.class")), false);
  assert.equal(isMachO(join(root, "script.py")), false);
  // Symlinks are skipped: their target is checked once, as itself.
  assert.deepEqual(machOFiles(root), [join(root, "thin"), join(root, "universal")]);
});
