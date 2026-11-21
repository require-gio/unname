#!/usr/bin/env node
/**
 * Version arithmetic for app releases.
 *
 * App releases are tagged `v<major>.<minor>.<patch>`. Model mirrors and build
 * inputs have their own tag families (`models-v1`, `bundle-inputs-<run id>`),
 * which never parse as an app version and so never move the release number.
 *
 * The bump level comes from exactly one `release:patch|minor|major` label on
 * the pull request from main into `release`.
 *
 * Usage:
 *   node scripts/release/version.mjs resolve --labels '["release:minor"]' [--commit SHA]
 *   node scripts/release/version.mjs resolve --level minor [--commit SHA]
 *
 * `resolve` prints `key=value` lines for `$GITHUB_OUTPUT`: previous, level,
 * version, tag and reused. When an app tag already points at `--commit`, that
 * version is reused, so re-running a release builds the same version instead
 * of bumping again.
 */

import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";

export const LEVELS = Object.freeze(["patch", "minor", "major"]);
export const LABEL_PREFIX = "release:";

const TAG = /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
// The MSI ProductVersion caps each field and has no room for pre-release suffixes.
const LIMITS = { major: 255, minor: 255, patch: 65_535 };

/** `v1.2.3` → {major: 1, minor: 2, patch: 3}; anything else → null. */
export function parseTag(tag) {
  const match = TAG.exec(String(tag).trim());
  if (!match) return null;
  return { major: Number(match[1]), minor: Number(match[2]), patch: Number(match[3]) };
}

export function formatVersion({ major, minor, patch }) {
  return `${major}.${minor}.${patch}`;
}

function compare(a, b) {
  return a.major - b.major || a.minor - b.minor || a.patch - b.patch;
}

/** Highest app version among the tags; 0.0.0 before the first release. */
export function latestVersion(tags) {
  const versions = tags.map(parseTag).filter(Boolean).sort(compare);
  return versions.at(-1) ?? { major: 0, minor: 0, patch: 0 };
}

export function bump(version, level) {
  if (!LEVELS.includes(level)) {
    throw new Error(`Unknown bump level "${level}"; expected one of ${LEVELS.join(", ")}.`);
  }
  const next =
    level === "major"
      ? { major: version.major + 1, minor: 0, patch: 0 }
      : level === "minor"
        ? { major: version.major, minor: version.minor + 1, patch: 0 }
        : { ...version, patch: version.patch + 1 };
  for (const [field, limit] of Object.entries(LIMITS)) {
    if (next[field] > limit) {
      throw new Error(
        `${formatVersion(next)} exceeds the Windows installer limit (${field} ≤ ${limit}).`,
      );
    }
  }
  return next;
}

/** The single release level among PR labels; throws for none or several. */
export function levelFromLabels(labels) {
  const levels = labels
    .filter((label) => label.startsWith(LABEL_PREFIX))
    .map((label) => label.slice(LABEL_PREFIX.length));
  const unknown = levels.filter((level) => !LEVELS.includes(level));
  if (unknown.length > 0) {
    throw new Error(`Unknown release label: ${unknown.map((l) => LABEL_PREFIX + l).join(", ")}.`);
  }
  if (levels.length !== 1) {
    const expected = LEVELS.map((level) => LABEL_PREFIX + level).join(", ");
    throw new Error(
      levels.length === 0
        ? `The release PR needs one of the labels ${expected}.`
        : `The release PR has ${levels.length} release labels; keep exactly one of ${expected}.`,
    );
  }
  return levels[0];
}

/**
 * Decide the version of a release.
 *
 * @param {{tags: string[], tagsAtCommit?: string[], level?: string, labels?: string[]}} input
 */
export function resolveRelease({ tags, tagsAtCommit = [], level, labels }) {
  const existing = tagsAtCommit.map(parseTag).filter(Boolean).sort(compare).at(-1);
  if (existing) {
    const version = formatVersion(existing);
    return { previous: version, level: "existing", version, tag: `v${version}`, reused: true };
  }
  const chosen = level ?? levelFromLabels(labels ?? []);
  const previous = latestVersion(tags);
  const version = formatVersion(bump(previous, chosen));
  return {
    previous: formatVersion(previous),
    level: chosen,
    version,
    tag: `v${version}`,
    reused: false,
  };
}

function git(args) {
  const result = spawnSync("git", args, { encoding: "utf8" });
  if (result.error || result.status !== 0) {
    throw new Error(`git ${args.join(" ")} failed: ${result.error?.message ?? result.stderr}`);
  }
  return result.stdout.split("\n").map((line) => line.trim()).filter(Boolean);
}

function option(args, name) {
  const index = args.indexOf(name);
  if (index === -1) return undefined;
  const value = args[index + 1];
  if (value === undefined || value.startsWith("--")) throw new Error(`${name} needs a value.`);
  return value;
}

function main(args) {
  const [command, ...rest] = args;
  if (command !== "resolve") {
    throw new Error("Usage: version.mjs resolve (--labels JSON | --level LEVEL) [--commit SHA]");
  }
  const labelsJson = option(rest, "--labels");
  const level = option(rest, "--level");
  const commit = option(rest, "--commit");
  if ((labelsJson === undefined) === (level === undefined)) {
    throw new Error("Pass exactly one of --labels or --level.");
  }
  const labels = labelsJson === undefined ? undefined : JSON.parse(labelsJson);
  if (labels !== undefined && !Array.isArray(labels)) {
    throw new Error("--labels must be a JSON array of label names.");
  }
  const result = resolveRelease({
    tags: git(["tag", "--list", "v*"]),
    tagsAtCommit: commit ? git(["tag", "--list", "v*", "--points-at", commit]) : [],
    level,
    labels,
  });
  for (const [key, value] of Object.entries(result)) console.log(`${key}=${value}`);
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  try {
    main(process.argv.slice(2));
  } catch (error) {
    console.error(`::error::${error.message}`);
    process.exit(1);
  }
}
