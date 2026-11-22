#!/usr/bin/env node
/**
 * Smoke test of the actually built desktop artifacts.
 *
 * 1. On Windows the frozen sidecar reports Ready immediately and answers /health.
 * 2. The release binary stays alive, spawns this sidecar as a child and its /health
 *    only responds on loopback.
 *
 * Usage:
 *   node scripts/smoke-packaged-app.mjs --app <path_arg> --sidecar <path_arg>
 *                                           [--skip-direct-sidecar]
 *
 * `--skip-direct-sidecar` is needed for the final macOS sidecar with
 * com.apple.security.inherit: it must run only as a child of the sandbox app.
 * Its Ready state is still checked via HTTP on the child process.
 *
 * 3. On macOS the directly started sidecar reports via /health that OCR is Apple
 *    Vision and that warm-up with the bundled helper succeeded (no Tesseract in the package).
 */
import { mkdtempSync, realpathSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { request as httpRequest } from "node:http";

const START_TIMEOUT_MS = Number(
  process.env.PACKAGED_SMOKE_TIMEOUT_MS ?? 180_000,
);
const POLL_MS = 500;
const DIREKTES_SIDECAR_UEBERSPRINGEN = process.argv.includes(
  "--skip-direct-sidecar",
);

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || !process.argv[index + 1]) {
    throw new Error(`Pflichtargument fehlt: ${name} <pfad>`);
  }
  return realpathSync(resolve(process.argv[index + 1]));
}

function checkFile(path_arg, label) {
  if (!statSync(path_arg).isFile())
    throw new Error(`${label} ist keine Datei: ${path_arg}`);
}

function pause(ms) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
}

async function mitTimeout(promise, ms, message_text) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(message_text)), ms);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function offlineEnvironment(stateDirectory) {
  return {
    ...process.env,
    PRESIDIO_STATE_DIR: stateDirectory,
    HF_HUB_OFFLINE: "1",
    TRANSFORMERS_OFFLINE: "1",
    HF_DATASETS_OFFLINE: "1",
    // An accidental HTTP request is forced onto an unused loopback port and cannot leave the device.
    HTTP_PROXY: "http://127.0.0.1:9",
    HTTPS_PROXY: "http://127.0.0.1:9",
    ALL_PROXY: "http://127.0.0.1:9",
    NO_PROXY: "127.0.0.1,localhost,::1",
  };
}

function processEnd(proc_handle) {
  return new Promise((resolvePromise) => {
    if (proc_handle.exitCode !== null || proc_handle.signalCode !== null) {
      resolvePromise({ code: proc_handle.exitCode, signal: proc_handle.signalCode });
      return;
    }
    proc_handle.once("exit", (code, signal) => resolvePromise({ code, signal }));
  });
}

async function terminate(proc_handle, signal = "SIGTERM") {
  if (!proc_handle || proc_handle.exitCode !== null || proc_handle.signalCode !== null)
    return;
  if (process.platform === "win32") {
    await terminateWindowsTree(proc_handle);
  } else {
    proc_handle.kill(signal);
    try {
      await mitTimeout(
        processEnd(proc_handle),
        15_000,
        "Prozessende dauerte zu lang.",
      );
    } catch {
      proc_handle.kill("SIGKILL");
      await processEnd(proc_handle);
    }
  }
  // A surviving grandchild could still hold the write ends of these pipes;
  // Node would then wait for them forever after the last check.
  proc_handle.stdout?.destroy();
  proc_handle.stderr?.destroy();
}

/**
 * On Windows, kill() ends only the given process (TerminateProcess). The
 * onefile sidecar's Python child would survive it as an orphan — started with
 * --keep-running-without-parent it never exits, and it keeps this script's
 * pipes open. The leaves of the tree go first, so the onefile bootloader can
 * remove its unpack folder and exit by itself; whatever is still running after
 * the grace period is ended with taskkill /T /F.
 */
async function terminateWindowsTree(proc_handle) {
  const tree = getDescendants(windowsProcesses(), proc_handle.pid);
  const parents = new Set(tree.map((entry) => entry.ppid));
  for (const leaf of tree.filter((entry) => !parents.has(entry.pid))) {
    try {
      process.kill(leaf.pid);
    } catch {
      // Already gone.
    }
  }
  try {
    await mitTimeout(processEnd(proc_handle), 5_000, "Prozessende dauerte zu lang.");
  } catch {
    spawnSync("taskkill", ["/PID", String(proc_handle.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
    await mitTimeout(
      processEnd(proc_handle),
      15_000,
      `Prozess ${proc_handle.pid} ließ sich nicht beenden.`,
    );
  }
}

function startSidecar(sidecar, env, stateDirectory, extra = []) {
  const proc_handle = spawn(
    sidecar,
    [
      "--keep-running-without-parent",
      "--state-directory",
      stateDirectory,
      ...extra,
    ],
    {
      cwd: dirname(sidecar),
      env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    },
  );

let stdout = "";
let stderr = "";
proc_handle.stderr.setEncoding("utf8");
proc_handle.stderr.on("data", (chunk) => {
  stderr = `${stderr}${chunk}`.slice(-8_000);
});

const ready = new Promise((resolvePromise, reject) => {
  proc_handle.stdout.setEncoding("utf8");
  proc_handle.stdout.on("data", (chunk) => {
    stdout += chunk;
    let newline;
    while ((newline = stdout.indexOf("\n")) >= 0) {
      const line = stdout.slice(0, newline).trim();
      stdout = stdout.slice(newline + 1);
      if (!line) continue;
      let payload;
      try {
        payload = JSON.parse(line);
      } catch {
        reject(
          new Error(
            `Sidecar-stdout enthält kein JSON: ${line.slice(0, 200)}`,
          ),
        );
        return;
      }
      if (payload.event === "ready") resolvePromise(payload);
    }
  });
  proc_handle.once("error", reject);
  proc_handle.once("exit", (code, signal) => {
    reject(
      new Error(
        `Sidecar endete vor Ready (Code ${code}, Signal ${signal}).\n${stderr.trim()}`,
      ),
    );
  });
});
return { proc_handle, ready };
}

async function checkHealth(ready) {
  if (
    !Number.isInteger(ready.port) ||
    ready.port < 1 ||
    ready.port > 65_535 ||
    typeof ready.token !== "string" ||
    ready.token.length < 16
  ) {
    throw new Error(
      "Ready-Handshake enthält keinen gültigen Port bzw. kein Token.",
    );
  }
  const response = await fetch(`http://127.0.0.1:${ready.port}/health`, {
    headers: { Authorization: `Bearer ${ready.token}` },
    signal: AbortSignal.timeout(15_000),
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(
      `/health antwortet mit HTTP ${response.status}: ${text.slice(0, 500)}`,
    );
  }
  let health;
  try {
    health = JSON.parse(text);
  } catch {
    throw new Error("/health liefert kein JSON.");
  }
  if (
    health.service !== "presidio-sidecar" ||
    health.protocol !== ready.protocol
  ) {
    throw new Error(
      "/health und Ready-Handshake gehören nicht zum erwarteten Sidecar-Protokoll.",
    );
  }
  return health;
}

/** HTTP over a Unix domain socket; fetch cannot use it. */
function amSocket(socketPath, path_arg, token) {
  return new Promise((resolvePromise, reject) => {
    const http_req_spike = httpRequest(
      {
        socketPath,
        path: path_arg,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        timeout: 15_000,
      },
      (response) => {
        let text = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => (text += chunk));
        response.on("end", () =>
          resolvePromise({ status: response.statusCode, text }),
        );
      },
    );
    http_req_spike.on("timeout", () => http_req_spike.destroy(new Error("Zeitlimit")));
    http_req_spike.on("error", reject);
    http_req_spike.end();
  });
}

/** Using --uds the sidecar reports a socket instead of a port. */
async function checkHealthOnSocket(ready, socketPath) {
  if (ready.socket !== socketPath || ready.port !== undefined) {
    throw new Error(
      `Ready-Handshake mit --uds nennt nicht genau den Socket: ${JSON.stringify({ socket: ready.socket, port: ready.port })}`,
    );
  }
  const mode_val = statSync(socketPath).mode & 0o777;
  if (mode_val !== 0o600)
    throw new Error(`Socket trägt ${mode_val.toString(8)} statt 600.`);
  if (loopbackListener(ready.pid).length > 0)
    throw new Error("Sidecar lauscht mit --uds trotzdem auf TCP.");
  const withoutToken = await amSocket(socketPath, "/health");
  if (withoutToken.status !== 401)
    throw new Error(`/health ohne Token am Socket: HTTP ${withoutToken.status}`);
  const mitToken = await amSocket(socketPath, "/health", ready.token);
  if (
    mitToken.status !== 200 ||
    JSON.parse(mitToken.text).service !== "presidio-sidecar"
  )
    throw new Error(`/health am Socket: HTTP ${mitToken.status}`);
}

/** Vision OCR in frozen package – helper found, probe and warmup succeeded. */
async function checkOcr(ready) {
  // First Vision call per program and macOS build takes ~50 s to compile.
  const deadline = Date.now() + Math.max(START_TIMEOUT_MS, 300_000);
  let ocr;
  while (Date.now() < deadline) {
    const response = await fetch(`http://127.0.0.1:${ready.port}/health`, {
      headers: { Authorization: `Bearer ${ready.token}` },
    });
    ocr = (await response.json()).ocr;
    if (ocr?.state === "missing")
      throw new Error(`Texterkennung fehlt im Paket: ${JSON.stringify(ocr)}`);
    if (ocr?.state === "ready" && ocr.warmup_ms !== null) break;
    await pause(POLL_MS * 4);
  }
  if (ocr?.engine !== "apple-vision" || ocr.revision !== 3 || !ocr.os_build) {
    throw new Error(`Unerwartete Texterkennung: ${JSON.stringify(ocr)}`);
  }
  if (ocr.warmup_ms === null)
    throw new Error("Das Aufwärmen der Texterkennung lief nicht durch.");
  return ocr;
}

function windowsProcesses() {
  const skript = [
    "Get-CimInstance Win32_Process",
    "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath",
    "ConvertTo-Json -Compress",
  ].join(" | ");
  const result = spawnSync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-Command", skript],
    {
      encoding: "utf8",
      windowsHide: true,
    },
  );
  if (result.status !== 0)
    throw new Error(`Prozessabfrage fehlgeschlagen: ${result.stderr}`);
  const parsed = JSON.parse(result.stdout || "[]");
  return (Array.isArray(parsed) ? parsed : [parsed]).map((proc_handle) => ({
    pid: Number(proc_handle.ProcessId),
    ppid: Number(proc_handle.ParentProcessId),
    name: proc_handle.ExecutablePath || proc_handle.Name || "",
  }));
}

function unixProcesses() {
  const result = spawnSync("ps", ["-axo", "pid=,ppid=,command="], {
    encoding: "utf8",
  });
  if (result.status !== 0)
    throw new Error(`Prozessabfrage fehlgeschlagen: ${result.stderr}`);
  return result.stdout
    .split("\n")
    .map((line) => line.match(/^\s*(\d+)\s+(\d+)\s+(.+)$/))
    .filter(Boolean)
    .map((match_info) => ({
      pid: Number(match_info[1]),
      ppid: Number(match_info[2]),
      name: match_info[3],
    }));
}

function processes() {
  return process.platform === "win32" ? windowsProcesses() : unixProcesses();
}

function getDescendants(table, wurzelPid) {
  const ids = new Set([wurzelPid]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const proc_handle of table) {
      if (ids.has(proc_handle.ppid) && !ids.has(proc_handle.pid)) {
        ids.add(proc_handle.pid);
        changed = true;
      }
    }
  }
  return table.filter(
    (proc_handle) => proc_handle.pid !== wurzelPid && ids.has(proc_handle.pid),
  );
}

function isSidecarProcess(proc_handle) {
  return /(?:^|[\\/])presidio-sidecar(?:-|\.exe(?:\s|$)|\s|$)/i.test(
    proc_handle.name,
  );
}

function loopbackListener(pid) {
  if (process.platform === "win32") {
    const skript =
      `Get-NetTCPConnection -State Listen -OwningProcess ${pid} -ErrorAction SilentlyContinue | ` +
      "Select-Object LocalAddress,LocalPort | ConvertTo-Json -Compress";
    const result = spawnSync(
      "powershell.exe",
      ["-NoProfile", "-NonInteractive", "-Command", skript],
      { encoding: "utf8", windowsHide: true },
    );
    if (result.status !== 0 || !result.stdout.trim()) return [];
    const parsed = JSON.parse(result.stdout);
    return (Array.isArray(parsed) ? parsed : [parsed]).map((listener) => ({
      address: listener.LocalAddress,
      port: Number(listener.LocalPort),
    }));
  }

  const result = spawnSync(
    "lsof",
    ["-nP", "-a", "-p", String(pid), "-iTCP", "-sTCP:LISTEN", "-F", "n"],
    {
      encoding: "utf8",
    },
  );
  if (result.status !== 0 && result.status !== 1) {
    throw new Error(`Listener-Abfrage fehlgeschlagen: ${result.stderr}`);
  }
  return result.stdout
    .split("\n")
    .filter((line) => line.startsWith("n"))
    .map((line) => line.slice(1).match(/^(?:\[([^\]]+)\]|([^:]+)):(\d+)$/))
    .filter(Boolean)
    .map((match_info) => ({
      address: match_info[1] || match_info[2],
      port: Number(match_info[3]),
    }));
}

function isLoopback(address) {
  return address === "127.0.0.1" || address === "::1" || address === "[::1]";
}

async function respondsAsSidecar({ address, port }) {
  const host =
    address.includes(":") && !address.startsWith("[")
      ? `[${address}]`
      : address;
  try {
    const response = await fetch(`http://${host}:${port}/health`, {
      signal: AbortSignal.timeout(2_000),
    });
    // Without the secret token passed only to the app, the sidecar must fail-closed.
    // Uvicorn serves requests only after the lifespan start and ready handshake are complete.
    return response.status === 401;
  } catch {
    return false;
  }
}

/** Unix sockets of a process, as the shell creates them (folder unname-…, file s). */
function socketListener(pid) {
  const result = spawnSync(
    "lsof",
    ["-nP", "-a", "-p", String(pid), "-U", "-F", "n"],
    { encoding: "utf8" },
  );
  if (result.status !== 0 && result.status !== 1) {
    throw new Error(`Socket-Abfrage fehlgeschlagen: ${result.stderr}`);
  }
  return [
    ...new Set(
      result.stdout
        .split("\n")
        .filter((line) => line.startsWith("n/"))
        .map((line) => line.slice(1))
        .filter((path_arg) => /\/unname-[^/]+\/s$/.test(path_arg)),
    ),
  ];
}

async function respondsOnSocket(socketPath) {
  try {
    // Without a token the socket must also fail-closed with 401.
    return (await amSocket(socketPath, "/health")).status === 401;
  } catch {
    return false;
  }
}

async function warteAufAppSidecar(appProzess) {
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (appProzess.exitCode !== null || appProzess.signalCode !== null) {
      throw new Error(
        `App endete vor dem Sidecar-Start (Code ${appProzess.exitCode}, Signal ${appProzess.signalCode}).`,
      );
    }
    // The onefile sidecar on Windows runs as two processes: the bootloader the
    // app spawns, and the child it starts, which alone holds the listener.
    // Both carry the same name, so every one of them is checked. (The macOS
    // onedir sidecar is a single process.)
    const sidecars = getDescendants(processes(), appProzess.pid).filter(
      isSidecarProcess,
    );
    for (const sidecar of sidecars) {
      if (process.platform !== "win32") {
        // Ensure the bundled sidecar listens on the Unix socket, not on a TCP port.
        if (loopbackListener(sidecar.pid).length > 0) {
          throw new Error(
            "Gebündeltes Sidecar lauscht auf TCP statt am Unix-Socket (Phase Z1).",
          );
        }
        for (const path_arg of socketListener(sidecar.pid)) {
          if (await respondsOnSocket(path_arg))
            return { sidecar, listener: [{ address: "unix", port: path_arg }] };
        }
        continue;
      }
      const listener = loopbackListener(sidecar.pid);
      if (listener.some(({ address }) => !isLoopback(address))) {
        throw new Error(
          `Gebündeltes Sidecar lauscht nicht nur auf Loopback: ${JSON.stringify(listener)}`,
        );
      }
      for (const address_str of listener) {
        if (await respondsAsSidecar(address_str)) return { sidecar, listener };
      }
    }
    await pause(POLL_MS);
  }
  throw new Error(
    `App hat innerhalb von ${START_TIMEOUT_MS / 1000}s kein lauschendes Sidecar gestartet.`,
  );
}

// Keep socket path short: sun_path is limited to 104 bytes; tmpdir() can be long.
const app = argument("--app");
const sidecar = argument("--sidecar");
checkFile(app, "App-Binary");
checkFile(sidecar, "Sidecar-Binary");

const stateDirectory = mkdtempSync(join(tmpdir(), "unname-smoke-"));
const env = offlineEnvironment(stateDirectory);
let direktesSidecar;
let appProzess;
let appSidecarPid;

try {
  if (!DIREKTES_SIDECAR_UEBERSPRINGEN) {
    console.log(`  … Eingefrorenes Sidecar prüfen: ${sidecar}`);
    const started = startSidecar(sidecar, env, stateDirectory);
    direktesSidecar = started.proc_handle;
    const ready = await mitTimeout(
      started.ready,
      START_TIMEOUT_MS,
      `Sidecar meldete innerhalb von ${START_TIMEOUT_MS / 1000}s kein Ready.`,
    );
    const health = await checkHealth(ready);
    console.log(
      `  ✓ Sidecar Ready und /health erfolgreich (PID ${ready.pid}, ` +
        `${health.platform?.machine ?? "Architektur unbekannt"}).`,
    );
    if (process.platform === "darwin") {
      const ocr = await checkOcr(ready);
      console.log(
        `  ✓ Texterkennung Apple Vision · Revision ${ocr.revision} · macOS ` +
          `${ocr.os_version} (${ocr.os_build}), aufgewärmt in ${ocr.warmup_ms} ms.`,
      );
    }
    await terminate(direktesSidecar);
    direktesSidecar = undefined;

    if (process.platform !== "win32") {
      // Keep socket path short: sun_path is limited to 104 bytes; tmpdir() can be long.
      const socketFolder = mkdtempSync("/tmp/unname-");
      const socketPath = join(socketFolder, "s");
      try {
        const startedOnSocket = startSidecar(sidecar, env, stateDirectory, [
          "--uds",
          socketPath,
        ]);
        direktesSidecar = startedOnSocket.proc_handle;
        const readyAmSocket = await mitTimeout(
          startedOnSocket.ready,
          START_TIMEOUT_MS,
          `Sidecar mit --uds meldete innerhalb von ${START_TIMEOUT_MS / 1000}s kein Ready.`,
        );
        await checkHealthOnSocket(readyAmSocket, socketPath);
        console.log(
          "  ✓ Sidecar am Unix-Socket (0600, ohne Port) und /health erfolgreich.",
        );
        await terminate(direktesSidecar);
        direktesSidecar = undefined;
      } finally {
        rmSync(socketFolder, { recursive: true, force: true });
      }
    }
  }

  console.log(`  … Gebautes App-Binary prüfen: ${app}`);
  appProzess = spawn(app, [], {
    cwd: dirname(app),
    env,
    stdio: "ignore",
    windowsHide: false,
  });
  let appStartError;
  appProzess.on("error", (error) => {
    appStartError = error;
  });
  const { sidecar: kind, listener } = await warteAufAppSidecar(appProzess);
  appSidecarPid = kind.pid;
  await pause(2_000);
  if (appStartError)
    throw new Error(
      `App konnte nicht gestartet werden: ${appStartError.message}`,
    );
  if (
    appProzess.exitCode !== null ||
    !getDescendants(processes(), appProzess.pid).some((p) => p.pid === kind.pid)
  ) {
    throw new Error(
      "App oder gebündeltes Sidecar blieb nach dem Start nicht stabil am Leben.",
    );
  }
  console.log(
    `  ✓ App läuft (PID ${appProzess.pid}), Sidecar PID ${kind.pid} ist Ready ` +
      `auf ${listener.map(({ address, port }) => `${address}:${port}`).join(", ")}.`,
  );
} finally {
  await terminate(appProzess);
  await terminate(direktesSidecar);
  if (appSidecarPid) {
    for (let versuch = 0; versuch < 20; versuch += 1) {
      if (!processes().some((proc_handle) => proc_handle.pid === appSidecarPid)) break;
      await pause(500);
    }
    if (processes().some((proc_handle) => proc_handle.pid === appSidecarPid)) {
      process.kill(appSidecarPid, "SIGKILL");
    }
  }
  // Windows may hold file handles for a moment after a process ended.
  rmSync(stateDirectory, { recursive: true, force: true, maxRetries: 10, retryDelay: 500 });
}
