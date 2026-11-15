#!/usr/bin/env node
/**
 * Resets the app's local state for a fresh start, mainly to test first-run
 * setup.
 *
 * Deletes app data, caches, logs and WebView data, for the current bundle
 * identifier and for the one used before the rename (there is no migration
 * between them). Also deletes the sidecar's default folder, used when it ran
 * without the shell, and `PRESIDIO_STATE_DIR` if set.
 *
 * This includes the history, the token table and the keys: codes in earlier
 * output can no longer be restored afterwards. So without `--yes` the script
 * only lists what it would delete.
 *
 * Usage: node scripts/reset-state.mjs [--yes]
 *   --yes  really delete (quit the app first)
 */

import { existsSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const IDENTIFIERS = ["org.unname.desktop", "org.presidio-ui.desktop"];
const SIDECAR_FOLDERS = ["unname", "Presidio UI", "presidio-ui"];

function paths() {
  const home = homedir();
  const list = [];
  if (process.platform === "darwin") {
    const library = join(home, "Library");
    for (const id of IDENTIFIERS) {
      list.push(
        // The signed bundle runs sandboxed: its data lives in the container.
        // Only Data/ is ours; macOS protects the container's own metadata.
        join(library, "Containers", id, "Data"),
        join(library, "Application Support", id),
        join(library, "Caches", id),
        join(library, "Logs", id),
        join(library, "WebKit", id),
        join(library, "HTTPStorages", id),
        join(library, "HTTPStorages", `${id}.binarycookies`),
        join(library, "Saved Application State", `${id}.savedState`),
        join(library, "Preferences", `${id}.plist`),
      );
    }
    for (const name of SIDECAR_FOLDERS) {
      list.push(join(library, "Application Support", name));
    }
  } else if (process.platform === "win32") {
    const roaming = process.env.APPDATA ?? join(home, "AppData", "Roaming");
    const local = process.env.LOCALAPPDATA ?? join(home, "AppData", "Local");
    for (const id of IDENTIFIERS) {
      // LOCALAPPDATA\<id> also holds the WebView2 data (EBWebView).
      list.push(join(roaming, id), join(local, id));
    }
    for (const name of SIDECAR_FOLDERS) list.push(join(local, name));
  } else {
    const data = process.env.XDG_DATA_HOME ?? join(home, ".local", "share");
    const cache = process.env.XDG_CACHE_HOME ?? join(home, ".cache");
    const config = process.env.XDG_CONFIG_HOME ?? join(home, ".config");
    const state = process.env.XDG_STATE_HOME ?? join(home, ".local", "state");
    for (const id of IDENTIFIERS) {
      list.push(join(data, id), join(cache, id), join(config, id));
    }
    for (const name of SIDECAR_FOLDERS) list.push(join(state, name));
  }
  if (process.env.PRESIDIO_STATE_DIR) list.push(process.env.PRESIDIO_STATE_DIR);
  return [...new Set(list)];
}

const reallyDelete = process.argv.includes("--yes");
const existing = paths().filter((path) => existsSync(path));

if (existing.length === 0) {
  console.log("Nothing to do: the app has no local state.");
  process.exit(0);
}

console.log(reallyDelete ? "Deleting:" : "Would delete:");
for (const path of existing) console.log(`  ${path}`);

if (!reallyDelete) {
  console.log(
    "\nThe history, the token table and the keys will be lost.\n" +
      "Quit the app, then run again with --yes to delete.",
  );
  process.exit(0);
}

let failures = 0;
for (const path of existing) {
  try {
    rmSync(path, { recursive: true, force: true });
  } catch (cause) {
    failures += 1;
    console.error(`  not deleted: ${path} (${cause.message})`);
  }
}
console.log(
  failures === 0
    ? "Reset. The next start begins without history and without a token table."
    : `${failures} path(s) could not be deleted. Is the app still running?`,
);
process.exit(failures === 0 ? 0 : 1);
