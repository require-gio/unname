/**
 * Tests for the release version logic and the version stamp.
 *
 * Run: node --test "scripts/release/*.test.mjs"
 */

import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";

import { LOCATIONS, readVersions, setVersion } from "./set-version.mjs";
import { bump, latestVersion, levelFromLabels, parseTag, resolveRelease } from "./version.mjs";

const ROOT = resolve(import.meta.dirname, "..", "..");

test("only plain app tags count as versions", () => {
  assert.deepEqual(parseTag("v1.2.3"), { major: 1, minor: 2, patch: 3 });
  for (const tag of ["models-v1", "v1", "bundle-inputs-123", "v1.2", "v1.2.3-beta", "v01.2.3"]) {
    assert.equal(parseTag(tag), null, tag);
  }
  const latest = latestVersion(["models-v1", "v0.9.0", "v0.10.0", "v0.2.5", "bundle-inputs-7"]);
  assert.deepEqual(latest, { major: 0, minor: 10, patch: 0 });
  assert.deepEqual(latestVersion(["models-v1"]), { major: 0, minor: 0, patch: 0 });
});

test("bump resets the lower fields and respects the MSI limits", () => {
  const base = { major: 1, minor: 4, patch: 7 };
  assert.deepEqual(bump(base, "patch"), { major: 1, minor: 4, patch: 8 });
  assert.deepEqual(bump(base, "minor"), { major: 1, minor: 5, patch: 0 });
  assert.deepEqual(bump(base, "major"), { major: 2, minor: 0, patch: 0 });
  assert.throws(() => bump({ major: 0, minor: 255, patch: 0 }, "minor"), /installer limit/);
  assert.throws(() => bump(base, "huge"), /Unknown bump level/);
});

test("exactly one release label decides the level", () => {
  assert.equal(levelFromLabels(["documentation", "release:minor"]), "minor");
  assert.throws(() => levelFromLabels(["documentation"]), /needs one of the labels/);
  assert.throws(() => levelFromLabels(["release:patch", "release:major"]), /exactly one/);
  assert.throws(() => levelFromLabels(["release:big"]), /Unknown release label/);
});

test("the first release starts from 0.0.0", () => {
  const result = resolveRelease({ tags: ["models-v1"], level: "minor" });
  assert.deepEqual(result, {
    previous: "0.0.0",
    level: "minor",
    version: "0.1.0",
    tag: "v0.1.0",
    reused: false,
  });
});

test("a re-run reuses the tag already on the commit instead of bumping again", () => {
  const result = resolveRelease({
    tags: ["v0.1.0", "v0.2.0"],
    tagsAtCommit: ["v0.2.0"],
    labels: ["release:minor"],
  });
  assert.equal(result.version, "0.2.0");
  assert.equal(result.reused, true);
});

function copyVersionFiles(lineEnding) {
  const root = mkdtempSync(join(tmpdir(), "unname-version-"));
  for (const [file] of LOCATIONS) {
    const target = join(root, file);
    mkdirSync(dirname(target), { recursive: true });
    cpSync(resolve(ROOT, file), target);
    if (lineEnding === "\r\n") {
      const text = readFileSync(target, "utf8").replace(/\r?\n/g, "\r\n");
      writeFileSync(target, text, "utf8");
    }
  }
  return root;
}

for (const [name, lineEnding] of [
  ["LF", "\n"],
  ["CRLF", "\r\n"],
]) {
  test(`setVersion stamps every declaration (${name})`, () => {
    const root = copyVersionFiles(lineEnding);
    try {
      setVersion("3.14.159", root);
      for (const [file, found] of Object.entries(readVersions(root))) {
        assert.ok(found.length > 0, file);
        assert.ok(
          found.every((value) => value === "3.14.159"),
          `${file}: ${found}`,
        );
      }
      // Only the two app declarations change, never a dependency's version.
      const lock = readFileSync(join(root, "app/package-lock.json"), "utf8");
      assert.equal(lock.split('"version": "3.14.159"').length - 1, 2);
      assert.ok(lock.includes("\r\n") === (lineEnding === "\r\n"), "line endings kept");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
}

test("setVersion refuses pre-release versions and leaves the tree untouched", () => {
  const root = copyVersionFiles("\n");
  try {
    const before = readVersions(root);
    assert.throws(() => setVersion("1.0.0-beta.1", root), /plain major.minor.patch/);
    assert.deepEqual(readVersions(root), before);
    // A second declaration makes the pattern ambiguous: fail before writing anything.
    const init = join(root, "sidecar/src/presidio_sidecar/__init__.py");
    writeFileSync(init, `${readFileSync(init, "utf8")}\n__version__ = "9.9.9"\n`, "utf8");
    assert.throws(() => setVersion("1.0.0", root), /found 2/);
    const pkg = readFileSync(join(root, "app/package.json"), "utf8");
    assert.match(pkg, /"version": "0\.0\.1"/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
