#!/usr/bin/env node
/**
 * Open the release pull request from main into `release`.
 *
 * Asks whether this is a patch, minor or major release, shows the version each
 * choice leads to, and opens the PR (or updates the open one) with the matching
 * `release:*` label. Merging the PR starts .github/workflows/release.yml, which
 * builds and tests the packages, tags the version and drafts the GitHub
 * release. The full flow is in docs/releasing.md.
 *
 * Usage:
 *   node scripts/release.mjs [--level patch|minor|major] [--yes] [--dry-run]
 *                            [--repo OWNER/NAME]
 *
 * `--dry-run` only reads: it prints the PR it would open and changes nothing
 * on GitHub. Needs git and the GitHub CLI, signed in (`gh auth login`).
 */

import { spawnSync } from "node:child_process";
import { createInterface } from "node:readline/promises";

import {
  bump,
  formatVersion,
  LABEL_PREFIX,
  latestVersion,
  LEVELS,
} from "./release/version.mjs";

const BASE = "release";
const HEAD = "main";
const LABELS = {
  patch: { color: "c2e0c6", description: "Release: bug fixes only (x.y.Z)" },
  minor: { color: "0e8a16", description: "Release: new features (x.Y.0)" },
  major: { color: "b60205", description: "Release: breaking changes (X.0.0)" },
};

const args = process.argv.slice(2);
const yes = args.includes("--yes");
const dryRun = args.includes("--dry-run");
const levelArgument = valueOf("--level");
if (levelArgument && !LEVELS.includes(levelArgument)) {
  fail(`--level must be one of ${LEVELS.join(", ")}.`);
}

const repo = valueOf("--repo") ?? repositoryFromRemote();
run("gh", ["auth", "status"], { quiet: true });

console.log(`\n  Repository: ${repo}`);
run("git", ["fetch", "--quiet", "--tags", "origin"]);

let baseRef = `origin/${BASE}`;
if (!remoteBranchExists(BASE)) {
  // A PR needs a base that main is ahead of. Starting `release` at main's first
  // commit makes the first release PR carry the whole history.
  const roots = output("git", ["rev-list", "--max-parents=0", `origin/${HEAD}`]);
  baseRef = roots.trim().split("\n").at(-1);
  console.log(`\n  The branch "${BASE}" does not exist on origin yet.`);
  if (dryRun) {
    console.log(`  [dry run] Would create it at the first commit of ${HEAD} (${baseRef.slice(0, 7)}).`);
  } else {
    if (!(await confirm(`  Create it at the first commit of ${HEAD} (${baseRef.slice(0, 7)})?`))) {
      fail(`"${BASE}" is needed as the PR base.`);
    }
    run("git", ["push", "origin", `${baseRef}:refs/heads/${BASE}`]);
    run("git", ["fetch", "--quiet", "origin", BASE]);
  }
}

const ahead = Number(output("git", ["rev-list", "--count", `${baseRef}..origin/${HEAD}`]));
if (ahead === 0) {
  console.log(`\n  origin/${HEAD} has nothing that is not already in ${BASE}. Nothing to release.\n`);
  process.exit(0);
}
const unpushed = localMainAhead();
if (unpushed > 0) {
  console.log(
    `\n  ⚠ Your local ${HEAD} has ${unpushed} commit(s) that are not on origin/${HEAD}.` +
      `\n    The release takes origin/${HEAD}; push first if they belong in it.`,
  );
}

const tags = output("git", ["tag", "--list", "v*"]).split("\n").filter(Boolean);
const current = latestVersion(tags);
const currentText = tags.length > 0 ? `v${formatVersion(current)}` : "none yet";
const candidates = Object.fromEntries(
  LEVELS.map((level) => [level, formatVersion(bump(current, level))]),
);

const log = output("git", ["log", "--no-merges", "--format=- %h %s", `${baseRef}..origin/${HEAD}`])
  .split("\n")
  .filter(Boolean);
console.log(`\n  ${ahead} commit(s) from ${HEAD} go into this release:`);
for (const line of log.slice(0, 15)) console.log(`    ${line}`);
if (log.length > 15) console.log(`    … and ${log.length - 15} more`);

const level = levelArgument ?? (await askLevel());
const version = candidates[level];
const label = LABEL_PREFIX + level;
const title = `Release v${version}`;
const body = pullRequestBody(level, version, log);

if (dryRun) {
  console.log(`\n  [dry run] Would open or update the PR ${HEAD} → ${BASE} with ${label}:`);
  console.log(`\n  ${title}\n`);
  for (const line of body.split("\n")) console.log(`    ${line}`);
  process.exit(0);
}

ensureLabels();
const existing = JSON.parse(
  output("gh", [
    "pr",
    "list",
    "-R",
    repo,
    "--base",
    BASE,
    "--head",
    HEAD,
    "--state",
    "open",
    "--json",
    "number,url,labels",
  ]),
);
let url;
if (existing.length > 0) {
  const [pr] = existing;
  const stale = pr.labels
    .map(({ name }) => name)
    .filter((name) => name.startsWith(LABEL_PREFIX) && name !== label);
  const edit = ["pr", "edit", String(pr.number), "-R", repo, "--title", title, "--body-file", "-"];
  edit.push("--add-label", label);
  if (stale.length > 0) edit.push("--remove-label", stale.join(","));
  run("gh", edit, { input: body });
  url = pr.url;
  console.log(`\n  ✓ Updated the open release PR to ${label} → v${version}.`);
} else {
  url = output(
    "gh",
    [
      "pr",
      "create",
      "-R",
      repo,
      "--base",
      BASE,
      "--head",
      HEAD,
      "--title",
      title,
      "--body-file",
      "-",
      "--label",
      label,
    ],
    { input: body },
  ).trim();
  console.log(`\n  ✓ Opened the release PR with ${label} → v${version}.`);
}
console.log(`    ${url}`);
console.log(
  "\n  Next: wait for the checks, then merge with “Create a merge commit”." +
    "\n  The Release workflow builds and tests both packages, tags the version" +
    "\n  and creates a draft release for you to try and publish.\n",
);

async function askLevel() {
  if (!process.stdin.isTTY) fail("Not an interactive terminal; pass --level patch|minor|major.");
  console.log(`\n  Last release: ${currentText}`);
  LEVELS.forEach((name, index) => {
    console.log(`    ${index + 1}) ${name.padEnd(5)} → ${candidates[name]}`);
  });
  const prompt = createInterface({ input: process.stdin, output: process.stdout });
  try {
    for (;;) {
      const answer = (await prompt.question("  Which one? [1-3] ")).trim().toLowerCase();
      const chosen = LEVELS[Number(answer) - 1] ?? (LEVELS.includes(answer) ? answer : undefined);
      if (chosen) return chosen;
      console.log("  Please answer 1, 2, 3, patch, minor or major.");
    }
  } finally {
    prompt.close();
  }
}

async function confirm(question) {
  if (yes) return true;
  if (!process.stdin.isTTY) return false;
  const prompt = createInterface({ input: process.stdin, output: process.stdout });
  try {
    return /^y(es)?$/i.test((await prompt.question(`${question} [y/N] `)).trim());
  } finally {
    prompt.close();
  }
}

function ensureLabels() {
  const present = new Set(
    JSON.parse(output("gh", ["label", "list", "-R", repo, "--limit", "500", "--json", "name"])).map(
      ({ name }) => name,
    ),
  );
  for (const level of LEVELS) {
    const name = LABEL_PREFIX + level;
    if (present.has(name)) continue;
    const { color, description } = LABELS[level];
    run("gh", ["label", "create", name, "-R", repo, "--color", color, "--description", description]);
  }
}

function pullRequestBody(level, version, commits) {
  const shown = commits.slice(0, 50);
  const more = commits.length > shown.length ? [`- … and ${commits.length - shown.length} more`] : [];
  return [
    `Release **v${version}** (${level}; last release: ${currentText}).`,
    "",
    "Merging this PR with **Create a merge commit** starts the Release workflow. It",
    `builds and tests the macOS and Windows packages, tags \`v${version}\` and creates a`,
    "draft GitHub release to try before publishing.",
    "",
    `The \`${LABEL_PREFIX}${level}\` label decides the version. To pick another, swap the`,
    "label; the **Release PR** check shows the resulting version.",
    "",
    "### Commits",
    "",
    ...shown,
    ...more,
    "",
  ].join("\n");
}

function repositoryFromRemote() {
  // The remote may use an SSH host alias (git@github-work:owner/name.git) that gh
  // cannot map to github.com, so take owner/name from the path and pass -R.
  const remote = output("git", ["remote", "get-url", "origin"]).trim();
  const match = /[:/]([^/:]+\/[^/]+?)(?:\.git)?\/?$/.exec(remote);
  if (!match) fail(`Cannot read owner/name from the origin URL ${remote}; pass --repo.`);
  return match[1];
}

function remoteBranchExists(name) {
  const result = spawnSync("git", ["ls-remote", "--exit-code", "--heads", "origin", name], {
    encoding: "utf8",
  });
  if (result.status === 0) return true;
  if (result.status === 2) return false;
  fail(`git ls-remote failed: ${result.stderr}`);
}

function localMainAhead() {
  const local = spawnSync("git", ["rev-parse", "--verify", "--quiet", HEAD], { encoding: "utf8" });
  if (local.status !== 0) return 0;
  return Number(output("git", ["rev-list", "--count", `origin/${HEAD}..${HEAD}`]));
}

function valueOf(name) {
  const index = args.indexOf(name);
  if (index === -1) return undefined;
  const value = args[index + 1];
  if (!value || value.startsWith("--")) fail(`${name} needs a value.`);
  return value;
}

function output(command, commandArgs, options = {}) {
  return run(command, commandArgs, { ...options, quiet: true });
}

function run(command, commandArgs, { input, quiet = false } = {}) {
  const result = spawnSync(command, commandArgs, {
    encoding: "utf8",
    input,
    stdio: [input === undefined ? "inherit" : "pipe", quiet ? "pipe" : "inherit", "pipe"],
  });
  if (result.error) fail(`${command} could not start: ${result.error.message}`);
  if (result.status !== 0) {
    fail(`${command} ${commandArgs.slice(0, 2).join(" ")} failed:\n${result.stderr.trim()}`);
  }
  return result.stdout ?? "";
}

function fail(message) {
  console.error(`\n  ✗ ${message}\n`);
  process.exit(1);
}
