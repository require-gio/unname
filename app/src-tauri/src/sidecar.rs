//! Start, monitoring, and lifecycle of the Python sidecar.
//!
//! The most dangerous state is a filter that silently stops filtering (spec §2).
//! Therefore, fail-closed applies throughout:
//!
//! * As long as the sidecar does not demonstrably respond, the shell reports
//!   `can_upload: false` and `can_export: false`. The UI does not invent this
//!   rule; it reads it here.
//! * If the process disappears, **all running jobs are marked as failed**. No
//!   partial results.
//! * The watchdog checks `GET /health` regularly in addition to process
//!   lifetime. A hanging process is indistinguishable from a working one at the
//!   OS level — but for protection purposes, it is very much so.
//! * Every restart ends up in the diagnostic report.
//!
//! On macOS (and Linux), the shell communicates with the sidecar via a Unix
//! domain socket in a private folder, not via a port. Windows stays with TCP
//! on 127.0.0.1. The UI sees neither: it calls the `unname-api` scheme
//! (`api_scheme.rs`), and only the shell sets the token.
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tokio::sync::Notify;

use crate::activity::BusyActivity;
use crate::diagnostics::{DiagnosticsLog, Severity};
use crate::platform_security::SidecarRuntimeDirectories;

/// Name of the bundled binary (`externalBin` in `tauri.conf.json`).
const SIDECAR_NAME: &str = "presidio-sidecar";
/// 2: API v2 (entry, catalog, settings, text mode, Apply as dry-run).
const SIDECAR_PROTOCOL_VERSION: u64 = 2;
/// `sockaddr_un.sun_path` holds up to 104 bytes on macOS, including the
/// trailing null.
#[cfg(unix)]
const SOCKET_PATH_MAX_BYTES: usize = 103;

/// Event the UI listens to. Every state change is sent.
pub const STATUS_EVENT: &str = "sidecar://status";

const HEALTH_INTERVAL: Duration = Duration::from_secs(5);
/// Long timeout. The liveness check asks "is the service still running?" rather
/// than "is it currently idle?". Loading a model from disk blocks the process
/// for seconds — a short timeout would cause the shell to mark a healthy service
/// as dead.
const HEALTH_TIMEOUT: Duration = Duration::from_secs(10);
/// Upload and export are blocked only after several consecutive failures. A
/// single hiccup under load is not a crash; punishing it immediately would
/// strip the view from the user in the middle of their work.
const HEALTH_FAILURES_UNTIL_BLOCKED: u32 = 3;
/// The process is only considered hung and terminated much later.
const HEALTH_FAILURES_UNTIL_DEAD: u32 = 6;
/// Wait times between restart attempts, in seconds.
const RESTART_BACKOFF_S: [u64; 5] = [1, 2, 5, 10, 20];
/// The client gives up on idle connections well before the sidecar does
/// (uvicorn `timeout_keep_alive`, `sidecar/src/presidio_sidecar/__main__.py`).
///
/// Previously, the client kept them for 90 s, uvicorn closed them after 5 s —
/// exactly in sync with the liveness check. The next ping sometimes hit a
/// connection that the sidecar was just closing, and failed with "error sending
/// request". Whoever closes first must never be the server.
const POOL_IDLE_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum SidecarStatus {
    /// Process started, handshake still pending.
    Starting,
    Ready {
        /// Only for TCP (Windows); `None` for Unix sockets.
        port: Option<u16>,
        /// `unix` or `tcp`.
        transport: String,
        pid: u32,
        version: String,
    },
    /// Unreachable. `restarting` indicates whether the shell is still trying.
    Unreachable { reason: String, restarting: bool },
    /// Intentionally stopped (app is closing).
    Stopped,
}

/// Shell's connection to the sidecar. It never leaves the shell: the token is
/// known only to the shell since the token is set by the shell.
#[derive(Clone, Debug)]
pub struct SidecarConnection {
    /// The base URL is `http://127.0.0.1:<port>` or `http://localhost` for a
    /// Unix socket; the host is then only a header, and the connection is made
    /// via the socket.
    pub base_url: String,
    pub token: String,
    /// A fully configured client (socket or TCP) for all calls on this
    /// connection. A `reqwest::Client` is cheap to clone.
    client: reqwest::Client,
}

impl SidecarConnection {
    pub fn tcp(port: u16, token: String) -> Result<Self, String> {
        Ok(Self {
            base_url: format!("http://127.0.0.1:{port}"),
            token,
            client: client_builder()
                .build()
                .map_err(|error| format!("HTTP-Client für den Sidecar: {error}"))?,
        })
    }

    #[cfg(unix)]
    pub fn unix(socket: PathBuf, token: String) -> Result<Self, String> {
        Ok(Self {
            base_url: "http://localhost".to_string(),
            token,
            client: client_builder()
                .unix_socket(socket)
                .build()
                .map_err(|error| format!("HTTP-Client für den Sidecar: {error}"))?,
        })
    }

    pub fn client(&self) -> reqwest::Client {
        self.client.clone()
    }

    /// What the UI receives instead of an address and token.
    pub fn for_webview(&self, generation: u64) -> WebviewConnection {
        WebviewConnection {
            base_url: crate::api_scheme::webview_base_url().to_string(),
            generation,
        }
    }
}

/// Common settings for both transport methods.
fn client_builder() -> reqwest::ClientBuilder {
    reqwest::Client::builder().pool_idle_timeout(POOL_IDLE_TIMEOUT)
}

/// An error of a request with its entire chain of causes.
///
/// `reqwest::Error` shows only the top level when formatted — „error sending
/// request for url (…)". Whether a timeout, a refused socket, or a connection
/// closed by the sidecar lies behind it is only in the causes. Without them, a
/// hanging service in the log is indistinguishable from an expired connection.
pub(crate) fn describe_request_error(error: &reqwest::Error) -> String {
    let kind = if error.is_timeout() {
        "timeout"
    } else if error.is_connect() {
        "connect"
    } else if error.is_body() || error.is_decode() {
        "body"
    } else {
        "request"
    };
    let mut text = error.to_string();
    let mut source = std::error::Error::source(error);
    while let Some(cause) = source {
        let cause_text = cause.to_string();
        // Hyper and reqwest occasionally repeat their message verbatim.
        if !text.ends_with(&cause_text) {
            text.push_str(": ");
            text.push_str(&cause_text);
        }
        source = cause.source();
    }
    format!("[{kind}] {text}")
}

/// Address for the UI: its own scheme, no token. `generation`
/// changes with each new sidecar process; the UI uses this to detect that jobs
/// and states from the old connection are gone.
#[derive(Clone, Debug, Serialize)]
pub struct WebviewConnection {
    pub base_url: String,
    pub generation: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct SidecarSnapshot {
    pub status: SidecarStatus,
    pub restarts: u64,
    pub changed_at: String,
    /// **The single source of truth** for the locks in the UI.
    pub can_upload: bool,
    pub can_export: bool,
    /// If name detection or language is missing, the setup is pending
    /// and `can_upload` remains locked. `None` until the sidecar has reported
    /// this yet.
    pub setup_required: Option<bool>,
}

pub struct SidecarState {
    status: Mutex<SidecarStatus>,
    changed_at: Mutex<String>,
    connection: Mutex<Option<SidecarConnection>>,
    /// Only a last successful health state releases the connection and the
    /// processing locks. The internal connection remains intact during short
    /// outages so the watchdog can continue to check.
    health_available: AtomicBool,
    /// Last finding `setup_required` from `/health`.
    setup: Mutex<Option<bool>>,
    child: Mutex<Option<CommandChild>>,
    restarts: AtomicU64,
    /// Increments as soon as a new connection is established. A health watchdog
    /// for an older connection detects this to know it is no longer
    /// responsible.
    generation: AtomicU64,
    shutting_down: AtomicBool,
    manual_restart: Notify,
}

impl Default for SidecarState {
    fn default() -> Self {
        Self {
            status: Mutex::new(SidecarStatus::Starting),
            changed_at: Mutex::new(now_iso8601()),
            connection: Mutex::new(None),
            health_available: AtomicBool::new(false),
            setup: Mutex::new(None),
            child: Mutex::new(None),
            restarts: AtomicU64::new(0),
            generation: AtomicU64::new(0),
            shutting_down: AtomicBool::new(false),
            manual_restart: Notify::new(),
        }
    }
}

impl SidecarState {
    pub fn snapshot(&self) -> SidecarSnapshot {
        let status = self
            .status
            .lock()
            .expect("Sidecar-Sperre vergiftet")
            .clone();
        // A handshake alone is not enough: After the first failed health check,
        // it locks immediately, even if the process is still monitored until
        // the third miscount.
        let ready = matches!(status, SidecarStatus::Ready { .. })
            && self.health_available.load(Ordering::SeqCst);
        let setup_required = *self.setup.lock().expect("Sidecar-Sperre vergiftet");
        SidecarSnapshot {
            status,
            restarts: self.restarts.load(Ordering::Relaxed),
            changed_at: self
                .changed_at
                .lock()
                .expect("Sidecar-Sperre vergiftet")
                .clone(),
            // Without name detection and language, nothing is accepted — not
            // even while the finding is still pending (fail-closed).
            can_upload: ready && setup_required == Some(false),
            can_export: ready,
            setup_required,
        }
    }

    /// Connection for **read** calls: profiles, hit lists, and already computed
    /// states.
    ///
    /// It intentionally survives a health outage. Previously it vanished on the
    /// first failed ping, causing the entire UI to collapse — even users only
    /// reading their hits suddenly faced an empty, red page. The safety
    /// guarantee does not depend on hiding the address: it depends on
    /// `can_upload`/`can_export`, which remain strictly fail-closed.
    pub fn connection(&self) -> Option<SidecarConnection> {
        if !matches!(
            *self.status.lock().expect("Sidecar-Sperre vergiftet"),
            SidecarStatus::Ready { .. }
        ) {
            return None;
        }
        self.watchdog_connection()
    }

    /// Connection for **write** calls (export). It additionally requires a
    /// verified healthy service.
    pub fn writable_connection(&self) -> Option<SidecarConnection> {
        if !self.snapshot().can_export {
            return None;
        }
        self.watchdog_connection()
    }

    /// Connection for submitting documents (file input, E8). Like export, it
    /// requires a verified healthy service: `can_upload` is the same lock that
    /// closes the UI's dropzone.
    pub fn upload_connection(&self) -> Option<SidecarConnection> {
        if !self.snapshot().can_upload {
            return None;
        }
        self.watchdog_connection()
    }

    /// Internal connection for further health checks. It is intentionally not
    /// exposed via a Tauri command.
    fn watchdog_connection(&self) -> Option<SidecarConnection> {
        self.connection
            .lock()
            .expect("Sidecar-Sperre vergiftet")
            .clone()
    }

    /// Changes the health availability flag and reports whether a new status
    /// event is needed.
    fn set_health_available(&self, available: bool) -> bool {
        let changed = self.health_available.swap(available, Ordering::SeqCst) != available;
        if changed {
            *self.changed_at.lock().expect("Sidecar-Sperre vergiftet") = now_iso8601();
        }
        changed
    }

    fn set_status(&self, status: SidecarStatus) {
        *self.status.lock().expect("Sidecar-Sperre vergiftet") = status;
        *self.changed_at.lock().expect("Sidecar-Sperre vergiftet") = now_iso8601();
    }

    pub fn webview_connection(&self) -> Option<WebviewConnection> {
        let connection = self.connection()?;
        Some(connection.for_webview(self.generation()))
    }

    fn generation(&self) -> u64 {
        self.generation.load(Ordering::SeqCst)
    }

    /// Reads `setup_required` from `/health` and reports whether it changed. A
    /// report without the field counts as "pending".
    fn set_setup_required(&self, value: Option<bool>) -> bool {
        let mut setup = self.setup.lock().expect("Sidecar-Sperre vergiftet");
        let changed = *setup != value;
        *setup = value;
        if changed {
            *self.changed_at.lock().expect("Sidecar-Sperre vergiftet") = now_iso8601();
        }
        changed
    }

    /// Drops the connection. From here, `connection()` returns nothing — the UI
    /// can no longer accidentally call the sidecar.
    fn clear_connection(&self) {
        self.set_health_available(false);
        *self.setup.lock().expect("Sidecar-Sperre vergiftet") = None;
        *self.connection.lock().expect("Sidecar-Sperre vergiftet") = None;
        self.generation.fetch_add(1, Ordering::SeqCst);
    }

    fn take_child(&self) -> Option<CommandChild> {
        self.child.lock().expect("Sidecar-Sperre vergiftet").take()
    }

    /// Terminates the running process. The supervisor sees the end and decides
    /// whether to restart.
    ///
    /// Intentionally hard: temporary job data must anyway be cleaned up
    /// crash-safely (TTL, Phase 1) — a graceful farewell would only appear
    /// cleaner.
    pub fn kill_child(&self) {
        if let Some(child) = self.take_child() {
            let _ = child.kill();
        }
    }

    /// Shuts down the sidecar permanently; the supervisor does not restart it.
    pub fn shutdown(&self) {
        self.shutting_down.store(true, Ordering::SeqCst);
        self.clear_connection();
        self.kill_child();
    }

    pub fn request_restart(&self) {
        self.kill_child();
        // Wakes the supervisor if it is waiting after too many failed attempts.
        self.manual_restart.notify_one();
    }
}

/// Starts the supervisor. Runs until the app is terminated.
pub fn supervise(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        let mut attempt = 0usize;

        loop {
            let state = app.state::<SidecarState>();
            if state.shutting_down.load(Ordering::SeqCst) {
                break;
            }

            set_status_and_notify(&app, SidecarStatus::Starting);
            let outcome = run_once(&app).await;
            if outcome.became_ready {
                attempt = 0;
            }

            // Fail-closed: lock first, then talk about a restart.
            let state = app.state::<SidecarState>();
            state.clear_connection();
            state.take_child();

            let diagnostics = app.state::<DiagnosticsLog>();
            if state.shutting_down.load(Ordering::SeqCst) {
                set_status_and_notify(&app, SidecarStatus::Stopped);
                break;
            }

            match RESTART_BACKOFF_S.get(attempt) {
                Some(&wait_s) => {
                    attempt += 1;
                    state.restarts.fetch_add(1, Ordering::Relaxed);
                    diagnostics.record(
                        "sidecar_restart",
                        Severity::Warning,
                        format!(
                            "Sidecar beendet — Neustartversuch {attempt} von {} in {wait_s}s.",
                            RESTART_BACKOFF_S.len()
                        ),
                        json!({ "grund": outcome.reason }),
                    );
                    set_status_and_notify(
                        &app,
                        SidecarStatus::Unreachable {
                            reason: outcome.reason.clone(),
                            restarting: true,
                        },
                    );
                    tokio::time::sleep(Duration::from_secs(wait_s)).await;
                }
                None => {
                    // Giving up is correct here: a restart every second hides
                    // the cause and the UI would remain locked anyway. The user
                    // decides on a new attempt.
                    diagnostics.record(
                        "sidecar_restart",
                        Severity::Error,
                        "Sidecar startet nicht mehr. Kein weiterer automatischer Versuch.",
                        json!({ "grund": outcome.reason, "versuche": attempt }),
                    );
                    set_status_and_notify(
                        &app,
                        SidecarStatus::Unreachable {
                            reason: outcome.reason.clone(),
                            restarting: false,
                        },
                    );
                    app.state::<SidecarState>().manual_restart.notified().await;
                    attempt = 0;
                }
            }
        }
    });
}

struct RunOutcome {
    became_ready: bool,
    reason: String,
}

/// Starts the process and returns as soon as it ends.
async fn run_once(app: &AppHandle) -> RunOutcome {
    let diagnostics = app.state::<DiagnosticsLog>();

    let (command, origin) = match resolve_command(app) {
        Ok(value) => value,
        Err(reason) => {
            return RunOutcome {
                became_ready: false,
                reason,
            }
        }
    };
    // Lives until the end of this run; on exit, the folder and socket
    // disappear, even if the sidecar was killed hard.
    let socket = match SocketDirectory::create() {
        Ok(value) => value,
        Err(reason) => {
            return RunOutcome {
                became_ready: false,
                reason,
            }
        }
    };
    let command = match &socket {
        Some(socket) => command.args(["--uds".to_string(), socket.path_string()]),
        None => command,
    };
    let expected_socket = socket.as_ref().map(|socket| socket.path().to_path_buf());

    diagnostics.record(
        "startup",
        Severity::Info,
        format!("Sidecar wird gestartet ({origin})."),
        json!({ "quelle": origin }),
    );

    let (mut events, child) = match command.spawn() {
        Ok(value) => value,
        Err(error) => {
            return RunOutcome {
                became_ready: false,
                reason: format!("Sidecar ließ sich nicht starten: {error}"),
            }
        }
    };

    *app.state::<SidecarState>()
        .child
        .lock()
        .expect("Sidecar-Sperre vergiftet") = Some(child);

    let mut became_ready = false;
    let mut reason = "Sidecar-Prozess beendet.".to_string();
    // The last stderr line is the only trace available on a failed start.
    // Without it, the diagnostic report would only contain „beendet".
    let mut last_stderr: Option<String> = None;

    while let Some(event) = events.recv().await {
        match event {
            CommandEvent::Stdout(line) => {
                if handle_stdout_line(app, &line, expected_socket.as_deref()) {
                    became_ready = true;
                }
            }
            CommandEvent::Stderr(line) => {
                // stderr is the sidecar's log channel, not a protocol.
                let text = String::from_utf8_lossy(&line).trim_end().to_string();
                if !text.is_empty() {
                    let (level, message) = sidecar_log_level(&text);
                    log::log!(level, "sidecar: {message}");
                    last_stderr = Some(text);
                }
            }
            CommandEvent::Error(error) => {
                reason = format!("Fehler am Sidecar-Prozess: {error}");
            }
            CommandEvent::Terminated(payload) => {
                reason = describe_termination(payload.code, payload.signal);
                break;
            }
            _ => {}
        }
    }

    if !became_ready {
        reason = match last_stderr {
            Some(text) => format!("Sidecar endete ohne Handshake: {reason} Letzte Meldung: {text}"),
            None => format!("Sidecar endete ohne Handshake: {reason}"),
        };
    }

    RunOutcome {
        became_ready,
        reason,
    }
}

/// Determine the log level for a sidecar stderr line and the message without
/// the timestamp.
///
/// The sidecar writes `2026-11-08 09:22:10,360 INFO uvicorn.error: …`
/// (`configure_logging` in `sidecar/src/presidio_sidecar/__main__.py`).
/// Previously every line ended up as a warning in the log, including
/// „Application startup complete." — real warnings were lost in the noise.
/// Anything not matching this format (tracebacks, output before logging)
/// remains a warning: This is almost always something someone should see.
fn sidecar_log_level(line: &str) -> (log::Level, &str) {
    let parsed = (|| {
        // The timestamp format `JJJJ-MM-TT HH:MM:SS,mmm` is exactly 23
        // characters long, followed by a space.
        let stamp = line.get(..23)?;
        let bytes = stamp.as_bytes();
        let shape = bytes.iter().enumerate().all(|(index, byte)| match index {
            4 | 7 => *byte == b'-',
            10 => *byte == b' ',
            13 | 16 => *byte == b':',
            19 => *byte == b',',
            _ => byte.is_ascii_digit(),
        });
        if !shape {
            return None;
        }
        let rest = line.get(23..)?.strip_prefix(' ')?;
        let (level, message) = rest.split_once(' ')?;
        let level = match level {
            "DEBUG" => log::Level::Debug,
            "INFO" => log::Level::Info,
            "WARNING" => log::Level::Warn,
            "ERROR" | "CRITICAL" => log::Level::Error,
            _ => return None,
        };
        Some((level, message))
    })();
    parsed.unwrap_or((log::Level::Warn, line))
}

/// Process a stdout line. Return `true` if it was the handshake.
fn handle_stdout_line(app: &AppHandle, line: &[u8], expected_socket: Option<&Path>) -> bool {
    let text = String::from_utf8_lossy(line);
    let text = text.trim();
    if text.is_empty() {
        return false;
    }

    let payload: Value = match serde_json::from_str(text) {
        Ok(value) => value,
        Err(_) => {
            // stdout belongs solely to the log. Anything else there is an error
            // in the sidecar and is made visible, not swallowed.
            app.state::<DiagnosticsLog>().record(
                "startup",
                Severity::Warning,
                "Unerwartete Ausgabe auf stdout des Sidecars (kein JSON).",
                json!({ "laenge": text.len() }),
            );
            return false;
        }
    };

    match payload.get("event").and_then(Value::as_str) {
        Some("ready") => {
            let connection =
                parse_ready_connection(&payload, expected_socket).and_then(ReadyEndpoint::connect);
            let (connection, port, transport) = match connection {
                Ok(fields) => fields,
                Err(reason) => {
                    app.state::<DiagnosticsLog>().record(
                        "startup",
                        Severity::Error,
                        reason,
                        json!({}),
                    );
                    app.state::<SidecarState>().kill_child();
                    return false;
                }
            };

            let state = app.state::<SidecarState>();
            *state.connection.lock().expect("Sidecar-Sperre vergiftet") = Some(connection);
            state.set_health_available(true);
            let generation = state.generation.fetch_add(1, Ordering::SeqCst) + 1;

            set_status_and_notify(
                app,
                SidecarStatus::Ready {
                    port,
                    transport: transport.to_string(),
                    pid: payload.get("pid").and_then(Value::as_u64).unwrap_or(0) as u32,
                    version: payload
                        .get("version")
                        .and_then(Value::as_str)
                        .unwrap_or("unbekannt")
                        .to_string(),
                },
            );
            watch_health(app.clone(), generation);
            true
        }
        Some("diagnostic") => {
            app.state::<DiagnosticsLog>().record_from_sidecar(&payload);
            false
        }
        Some("stopping") => {
            log::info!("Sidecar meldet Herunterfahren.");
            false
        }
        _ => false,
    }
}

/// Regular liveness check via `GET /health`.
///
/// Without it, a hung sidecar would remain as "running": the operating system
/// continues to report the process as alive, even though no job gets through.
fn watch_health(app: AppHandle, generation: u64) {
    tauri::async_runtime::spawn(async move {
        // If the watcher ends because the sidecar crashes, is replaced, or
        // stops responding, the activity also ends.
        let _activity = ActivityGuard(app.clone());
        let mut failures = 0u32;
        // The first check immediately: only it tells whether the initial setup
        // aussteht. Bis dahin bleibt Ablegen gesperrt.
        let mut first = true;

        loop {
            if !first {
                tokio::time::sleep(HEALTH_INTERVAL).await;
            }
            first = false;

            let state = app.state::<SidecarState>();
            // A newer connection has taken over; this guard is finished.
            if state.generation() != generation {
                return;
            }
            let Some(connection) = state.watchdog_connection() else {
                return;
            };

            let response = probe_health(&connection).await;

            // During the HTTP call, a new sidecar generation may have already
            // taken over. A late response from the old connection must not
            // change its release.
            let state = app.state::<SidecarState>();
            if state.generation() != generation {
                return;
            }

            let (healthy, problem) = match response {
                Ok(response) if response.status().is_success() => {
                    let body = response.bytes().await.ok();
                    let setup = body.as_deref().and_then(setup_required_from_health);
                    let busy = body.as_deref().is_some_and(busy_from_health);
                    if state.generation() != generation {
                        return;
                    }
                    app.state::<BusyActivity>().set(busy);
                    if state.set_setup_required(setup) {
                        notify_snapshot(&app);
                    }
                    (true, None)
                }
                Ok(response) => (
                    false,
                    Some(format!("Health antwortet mit {}", response.status())),
                ),
                Err(error) => (
                    false,
                    Some(format!(
                        "Health nicht erreichbar: {}",
                        describe_request_error(&error)
                    )),
                ),
            };

            let failures_before = failures;
            let outcome = update_health_failures(&mut failures, healthy);
            match problem {
                Some(problem) => log::log!(
                    health_failure_level(failures),
                    "{problem} (Fehlversuch {failures} in Folge)"
                ),
                None if failures_before >= HEALTH_FAILURES_UNTIL_WARNING => log::info!(
                    "Health wieder erreichbar nach {failures_before} Fehlversuchen in Folge."
                ),
                None => {}
            }
            // Blocking occurs only after several consecutive failures. A single
            // lapse—such as while a model is loading—is not a failure, and
            // running jobs are not aborted for it: the sidecar continues
            // calculating. If the process truly dies, the supervisor marks it
            // fail-closed as failed anyway.
            if state.set_health_available(healthy || !outcome.blocked) {
                notify_snapshot(&app);
            }

            if outcome.dead {
                app.state::<DiagnosticsLog>().record(
                    "sidecar_restart",
                    Severity::Warning,
                    "Sidecar antwortet nicht mehr auf /health — Prozess wird beendet.",
                    json!({ "fehlversuche": failures }),
                );
                app.state::<SidecarState>().kill_child();
                return;
            }
        }
    });
}

/// A `GET /health` with at most one retry.
///
/// Retries occur only if the request never reaches the sidecar or the
/// connection drops mid-flight—typically a dormant connection the server just
/// closed. The second attempt uses a fresh connection. A timeout is not
/// retried: then the sidecar did not answer for 10 s, and that is exactly what
/// the guard counts. The query is read-only; a retry changes nothing.
async fn probe_health(connection: &SidecarConnection) -> reqwest::Result<reqwest::Response> {
    let attempt = || {
        connection
            .client()
            .get(format!("{}/health", connection.base_url))
            .bearer_auth(&connection.token)
            .timeout(HEALTH_TIMEOUT)
            .send()
    };
    match attempt().await {
        Err(error) if should_retry_health(&error) => {
            log::debug!(
                "Health-Anfrage wird wiederholt: {}",
                describe_request_error(&error)
            );
            attempt().await
        }
        result => result,
    }
}

fn should_retry_health(error: &reqwest::Error) -> bool {
    !error.is_timeout() && (error.is_connect() || error.is_request())
}

/// Warning starts from this consecutive failure. A single lapse is expected and
/// harmless; it belongs in the debug log, not in warnings meant for someone to
/// read.
const HEALTH_FAILURES_UNTIL_WARNING: u32 = 2;

fn health_failure_level(failures: u32) -> log::Level {
    if failures >= HEALTH_FAILURES_UNTIL_WARNING {
        log::Level::Warn
    } else {
        log::Level::Debug
    }
}

/// Stops activity against App Nap as soon as the health guard ends.
struct ActivityGuard(AppHandle);

impl Drop for ActivityGuard {
    fn drop(&mut self) {
        self.0.state::<BusyActivity>().set(false);
    }
}

fn set_status_and_notify(app: &AppHandle, status: SidecarStatus) {
    let state = app.state::<SidecarState>();
    state.set_status(status);
    notify_snapshot(app);
}

fn notify_snapshot(app: &AppHandle) {
    let state = app.state::<SidecarState>();
    let snapshot = state.snapshot();
    if let Err(error) = app.emit(STATUS_EVENT, &snapshot) {
        log::error!("Statusereignis nicht zustellbar: {error}");
    }
}

/// What follows from a measurement series.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct HealthOutcome {
    /// Block upload and export.
    blocked: bool,
    /// Terminate the process and restart it via the supervisor.
    dead: bool,
}

/// Scale the reaction based on the number of consecutive failures.
///
/// Previously, even a single failure blocked access. This was the actual cause
/// of the unusable UI: a single slow ping — for example while a language model
/// was loading — was enough to shut everything down in the middle of work.
fn update_health_failures(failures: &mut u32, healthy: bool) -> HealthOutcome {
    if healthy {
        *failures = 0;
    } else {
        *failures = failures.saturating_add(1);
    }
    HealthOutcome {
        blocked: *failures >= HEALTH_FAILURES_UNTIL_BLOCKED,
        dead: *failures >= HEALTH_FAILURES_UNTIL_DEAD,
    }
}

/// `setup_required` from a `/health` report; if missing, it is open.
fn setup_required_from_health(body: &[u8]) -> Option<bool> {
    serde_json::from_slice::<Value>(body)
        .ok()?
        .get("setup_required")?
        .as_bool()
}

/// `busy` from a `/health` report: ongoing or pending jobs.
fn busy_from_health(body: &[u8]) -> bool {
    serde_json::from_slice::<Value>(body)
        .ok()
        .and_then(|report| report.get("busy")?.as_bool())
        .unwrap_or(false)
}

/// Endpoint from the registration.
#[derive(Debug, PartialEq, Eq)]
enum ReadyEndpoint {
    Port(u16, String),
    #[cfg_attr(not(unix), allow(dead_code))]
    Socket(PathBuf, String),
}

impl ReadyEndpoint {
    fn connect(self) -> Result<(SidecarConnection, Option<u16>, &'static str), &'static str> {
        let unusable = |error: String| {
            log::error!("{error}");
            "HTTP-Client für den Sidecar nicht einstellbar."
        };
        match self {
            Self::Port(port, token) => SidecarConnection::tcp(port, token)
                .map(|connection| (connection, Some(port), "tcp"))
                .map_err(unusable),
            #[cfg(unix)]
            Self::Socket(path, token) => SidecarConnection::unix(path, token)
                .map(|connection| (connection, None, "unix"))
                .map_err(unusable),
            #[cfg(not(unix))]
            Self::Socket(..) => Err("Unix-Socket gibt es auf diesem System nicht."),
        }
    }
}

/// Validates the ready notification. With a given socket, the sidecar must
/// report exactly this path and no port; without it, a port. A sidecar that
/// connects elsewhere is ignored.
fn parse_ready_connection(
    payload: &Value,
    expected_socket: Option<&Path>,
) -> Result<ReadyEndpoint, &'static str> {
    if payload.get("protocol").and_then(Value::as_u64) != Some(SIDECAR_PROTOCOL_VERSION) {
        return Err("Handshake des Sidecars mit inkompatibler Protokollversion.");
    }
    let token = payload
        .get("token")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or("Handshake des Sidecars ohne Token.")?
        .to_string();
    if let Some(expected) = expected_socket {
        let reported = payload
            .get("socket")
            .and_then(Value::as_str)
            .ok_or("Handshake des Sidecars ohne Socket.")?;
        if Path::new(reported) != expected || payload.get("port").is_some() {
            return Err("Handshake des Sidecars mit fremdem Endpunkt.");
        }
        return Ok(ReadyEndpoint::Socket(expected.to_path_buf(), token));
    }
    if payload.get("socket").is_some() {
        return Err("Handshake des Sidecars mit unerwartetem Socket.");
    }
    let port = payload
        .get("port")
        .and_then(Value::as_u64)
        .and_then(|value| u16::try_from(value).ok())
        .filter(|value| *value != 0)
        .ok_or("Handshake des Sidecars ohne gueltigen Port.")?;
    Ok(ReadyEndpoint::Port(port, token))
}

/// Private directory (0700) for the sidecar run's socket.
///
/// It resides in the shell's temp folder; under the sandbox this is
/// `~/Library/Containers/org.unname.desktop/Data/tmp`, keeping the path well
/// below the `sun_path` limit. One new directory per run: an old socket never
/// blocks a restart.
struct SocketDirectory {
    #[cfg(unix)]
    directory: tempfile::TempDir,
    #[cfg(unix)]
    path: PathBuf,
}

impl SocketDirectory {
    #[cfg(unix)]
    fn create() -> Result<Option<Self>, String> {
        use std::os::unix::fs::PermissionsExt;
        // Set 0700 immediately upon creation; tempfile's default would be 0755.
        let directory = tempfile::Builder::new()
            .prefix("unname-")
            .permissions(std::fs::Permissions::from_mode(0o700))
            .tempdir()
            .map_err(|error| format!("Ordner für den Sidecar-Socket: {error}"))?;
        let path = directory.path().join("s");
        check_socket_path(&path)?;
        Ok(Some(Self { directory, path }))
    }

    /// Windows stays with TCP on 127.0.0.1.
    #[cfg(not(unix))]
    fn create() -> Result<Option<Self>, String> {
        Ok(None)
    }

    #[cfg(unix)]
    fn path(&self) -> &Path {
        debug_assert!(self.path.starts_with(self.directory.path()));
        &self.path
    }

    #[cfg(not(unix))]
    fn path(&self) -> &Path {
        unreachable!("kein Socket ohne Unix")
    }

    fn path_string(&self) -> String {
        self.path().to_string_lossy().into_owned()
    }
}

#[cfg(unix)]
fn check_socket_path(path: &Path) -> Result<(), String> {
    use std::os::unix::ffi::OsStrExt;
    let length = path.as_os_str().as_bytes().len();
    if length > SOCKET_PATH_MAX_BYTES {
        return Err(format!(
            "Pfad des Sidecar-Sockets ist {length} Byte lang (höchstens {SOCKET_PATH_MAX_BYTES})."
        ));
    }
    Ok(())
}

/// Finds the command to start.
///
/// The bundled sidecar (`bundled_command`) takes precedence. As long as it is
/// missing, a dev build falls back to the uv environment in the
/// repository—otherwise one would have to build a binary for every sidecar
/// change.
///
/// Important: `shell().sidecar()` only constructs a path and does not check if
/// anything exists there. Without the existence checks in `bundled_command`,
/// the shell would enter an infinite loop of start attempts against a
/// non-existent file.
fn resolve_command(
    app: &AppHandle,
) -> Result<(tauri_plugin_shell::process::Command, String), String> {
    let (command, origin) = if let Some(command) = bundled_command(app)? {
        (command, "gebündeltes Binary".to_string())
    } else {
        if !cfg!(debug_assertions) {
            return Err(format!(
                "Das gebündelte Sidecar-Binary '{SIDECAR_NAME}' fehlt. Ohne Sidecar wird nichts verarbeitet."
            ));
        }

        let python = dev_python()?;
        (
            app.shell()
                .command(python.to_string_lossy().to_string())
                .args(["-m", "presidio_sidecar"]),
            "Entwicklungsumgebung (uv)".to_string(),
        )
    };

    // Python continues to create its random, per-process isolated job root
    // underneath. On macOS, the parent directory was explicitly marked with
    // `NSURLIsExcludedFromBackupKey` beforehand.
    let command = app.state::<SidecarRuntimeDirectories>().configure(command);
    Ok((command, origin))
}

/// The sidecar the package carries, if any.
///
/// On macOS it is a PyInstaller onedir folder in `Contents/Resources`
/// (`tauri.bundle.macos.conf.json`). A onefile binary cannot start inside the
/// App Sandbox, and the folder cannot go into `Contents/MacOS`, where codesign
/// treats every file as code that must carry its own signature.
#[cfg(target_os = "macos")]
fn bundled_command(
    app: &AppHandle,
) -> Result<Option<tauri_plugin_shell::process::Command>, String> {
    let Ok(resources) = app.path().resource_dir() else {
        return Ok(None);
    };
    let executable = resources.join(SIDECAR_NAME).join(SIDECAR_NAME);
    Ok(executable
        .is_file()
        .then(|| app.shell().command(executable)))
}

/// Elsewhere it is the onefile binary next to the executable (`externalBin`).
#[cfg(not(target_os = "macos"))]
fn bundled_command(
    app: &AppHandle,
) -> Result<Option<tauri_plugin_shell::process::Command>, String> {
    if !sidecar_binary_present() {
        return Ok(None);
    }
    app.shell()
        .sidecar(SIDECAR_NAME)
        .map(Some)
        .map_err(|error| format!("Gebündeltes Sidecar-Binary nicht startbar: {error}"))
}

/// Is a sidecar binary present next to the executable?
///
/// Tauri appends the target system to the name (`…-x86_64-pc-windows-msvc`),
/// so we check the prefix rather than a fixed name.
#[cfg(not(target_os = "macos"))]
fn sidecar_binary_present() -> bool {
    let Ok(exe) = std::env::current_exe() else {
        return false;
    };
    let Some(directory) = exe.parent() else {
        return false;
    };
    let Ok(entries) = std::fs::read_dir(directory) else {
        return false;
    };

    entries.filter_map(Result::ok).any(|entry| {
        entry
            .file_name()
            .to_string_lossy()
            .starts_with(SIDECAR_NAME)
    })
}

fn dev_python() -> Result<PathBuf, String> {
    if let Ok(path) = std::env::var("PII_SIDECAR_PYTHON") {
        return Ok(PathBuf::from(path));
    }

    let relative = if cfg!(windows) {
        "../../sidecar/.venv/Scripts/python.exe"
    } else {
        "../../sidecar/.venv/bin/python"
    };
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(relative);

    if path.exists() {
        Ok(path)
    } else {
        Err(format!(
            "Keine Sidecar-Umgebung unter {}. Einmalig `uv sync` im Ordner sidecar/ ausführen.",
            path.display()
        ))
    }
}

fn describe_termination(code: Option<i32>, signal: Option<i32>) -> String {
    match (code, signal) {
        (_, Some(signal)) => format!("Sidecar durch Signal {signal} beendet."),
        (Some(0), _) => "Sidecar regulär beendet.".to_string(),
        (Some(code), _) => format!("Sidecar mit Code {code} beendet."),
        _ => "Sidecar beendet.".to_string(),
    }
}

fn now_iso8601() -> String {
    chrono::Utc::now()
        .to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unreachable(restarting: bool) -> SidecarStatus {
        SidecarStatus::Unreachable {
            reason: "Sidecar beendet.".to_string(),
            restarting,
        }
    }

    #[test]
    fn initial_state_is_locked() {
        // Only a ready notification opens the UI — not the absence of an error
        // message.
        let snapshot = SidecarState::default().snapshot();
        assert!(!snapshot.can_upload);
        assert!(!snapshot.can_export);
    }

    #[test]
    fn only_ready_unlocks() {
        let state = SidecarState::default();
        state.set_status(SidecarStatus::Ready {
            port: Some(51234),
            transport: "tcp".to_string(),
            pid: 4711,
            version: "0.1.0".to_string(),
        });
        state.set_health_available(true);
        state.set_setup_required(Some(false));

        let snapshot = state.snapshot();
        assert!(snapshot.can_upload);
        assert!(snapshot.can_export);
    }

    #[test]
    fn drop_stays_locked_without_models() {
        // Until name detection and language are available — and as long as the
        // finding is pending — the shell accepts nothing. Reading and exporting
        // already computed states remains possible.
        let state = SidecarState::default();
        state.set_status(SidecarStatus::Ready {
            port: Some(51234),
            transport: "tcp".to_string(),
            pid: 4711,
            version: "0.1.0".to_string(),
        });
        state.set_health_available(true);
        for setup in [None, Some(true)] {
            state.set_setup_required(setup);
            let snapshot = state.snapshot();
            assert!(!snapshot.can_upload, "{setup:?}");
            assert!(snapshot.can_export);
            assert_eq!(snapshot.setup_required, setup);
        }
        assert!(state.set_setup_required(Some(false)));
        assert!(!state.set_setup_required(Some(false)), "unverändert");
        assert!(state.snapshot().can_upload);

        state.clear_connection();
        assert_eq!(
            state.snapshot().setup_required,
            None,
            "neue Verbindung, neuer Befund"
        );
    }

    #[test]
    fn setup_finding_comes_from_health_report() {
        assert_eq!(
            setup_required_from_health(br#"{"setup_required": true}"#),
            Some(true)
        );
        assert_eq!(
            setup_required_from_health(br#"{"setup_required": false}"#),
            Some(false)
        );
        assert_eq!(setup_required_from_health(br#"{"status": "ok"}"#), None);
        assert_eq!(
            setup_required_from_health(br#"{"setup_required": "nein"}"#),
            None
        );
        assert_eq!(setup_required_from_health(b"kein json"), None);
    }

    #[test]
    fn busy_comes_from_health_report() {
        assert!(busy_from_health(br#"{"busy":true}"#));
        assert!(!busy_from_health(br#"{"busy":false}"#));
        assert!(!busy_from_health(br#"{"status":"ok"}"#));
        assert!(!busy_from_health(b"kein json"));
    }

    #[test]
    fn ready_without_healthy_state_stays_locked() {
        let state = SidecarState::default();
        state.set_status(SidecarStatus::Ready {
            port: Some(51234),
            transport: "tcp".to_string(),
            pid: 4711,
            version: "0.1.0".to_string(),
        });

        assert!(!state.snapshot().can_upload);
        assert!(!state.snapshot().can_export);
    }

    #[test]
    fn failure_locks_again() {
        let state = SidecarState::default();
        state.set_status(SidecarStatus::Ready {
            port: Some(51234),
            transport: "tcp".to_string(),
            pid: 4711,
            version: "0.1.0".to_string(),
        });
        state.set_health_available(true);
        state.set_setup_required(Some(false));
        assert!(state.snapshot().can_upload);

        for status in [
            unreachable(true),
            unreachable(false),
            SidecarStatus::Stopped,
        ] {
            state.set_status(status);
            let snapshot = state.snapshot();
            assert!(!snapshot.can_upload, "Upload muss gesperrt sein");
            assert!(!snapshot.can_export, "Export muss gesperrt sein");
        }
    }

    #[test]
    fn cleared_connection_is_no_longer_available() {
        let state = SidecarState::default();
        *state.connection.lock().unwrap() =
            Some(SidecarConnection::tcp(51234, "geheim".to_string()).unwrap());
        state.set_status(SidecarStatus::Ready {
            port: Some(51234),
            transport: "tcp".to_string(),
            pid: 4711,
            version: "0.1.0".to_string(),
        });
        state.set_health_available(true);
        assert!(state.connection().is_some());

        state.clear_connection();

        assert!(state.connection().is_none());
        assert!(state.writable_connection().is_none());
    }

    #[test]
    fn health_failure_locks_writes_but_keeps_reads() {
        // Users who only review their findings must not notice a single outage.
        // Write paths — upload and export — are blocked.
        let state = SidecarState::default();
        state.set_status(SidecarStatus::Ready {
            port: Some(51234),
            transport: "tcp".to_string(),
            pid: 4711,
            version: "0.1.0".to_string(),
        });
        *state.connection.lock().unwrap() =
            Some(SidecarConnection::tcp(51234, "geheim".to_string()).unwrap());
        state.set_health_available(true);
        let generation = state.generation();

        assert!(state.set_health_available(false));

        let snapshot = state.snapshot();
        assert!(!snapshot.can_upload);
        assert!(!snapshot.can_export);
        assert!(
            state.connection().is_some(),
            "Lesende Aufrufe bleiben möglich, solange der Prozess läuft."
        );
        assert!(
            state.writable_connection().is_none(),
            "Ein Export verlangt einen nachgewiesen gesunden Dienst."
        );
        assert_eq!(
            state.generation(),
            generation,
            "Ein kurzer Health-Aussetzer ersetzt nicht die Verbindungsgeneration."
        );
    }

    #[test]
    fn terminated_sidecar_serves_no_reads() {
        let state = SidecarState::default();
        *state.connection.lock().unwrap() =
            Some(SidecarConnection::tcp(51234, "geheim".to_string()).unwrap());
        state.set_status(SidecarStatus::Ready {
            port: Some(51234),
            transport: "tcp".to_string(),
            pid: 4711,
            version: "0.1.0".to_string(),
        });
        assert!(state.connection().is_some());

        for status in [unreachable(true), SidecarStatus::Stopped] {
            state.set_status(status);
            assert!(state.connection().is_none());
            assert!(state.writable_connection().is_none());
        }
    }

    #[test]
    fn healthy_check_reopens_after_short_outage() {
        let state = SidecarState::default();
        state.set_status(SidecarStatus::Ready {
            port: Some(51234),
            transport: "tcp".to_string(),
            pid: 4711,
            version: "0.1.0".to_string(),
        });
        *state.connection.lock().unwrap() =
            Some(SidecarConnection::tcp(51234, "geheim".to_string()).unwrap());
        state.set_health_available(true);
        state.set_health_available(false);

        assert!(state.set_health_available(true));
        state.set_setup_required(Some(false));
        assert!(state.snapshot().can_upload);
        assert!(state.snapshot().can_export);
        assert!(state.connection().is_some());
    }

    #[test]
    fn single_outages_lock_nothing_yet() {
        // The core improvement: a slow ping — for example while a language
        // model loads — must not lock the UI.
        let mut failures = 0;

        for _ in 1..HEALTH_FAILURES_UNTIL_BLOCKED {
            let outcome = update_health_failures(&mut failures, false);
            assert!(!outcome.blocked, "Ein einzelner Aussetzer ist kein Ausfall");
            assert!(!outcome.dead);
        }

        let outcome = update_health_failures(&mut failures, false);
        assert!(
            outcome.blocked,
            "Anhaltendes Schweigen sperrt das Schreiben"
        );
        assert!(!outcome.dead, "Zum Beenden reicht das noch nicht");
    }

    #[test]
    fn only_sustained_silence_requires_process_restart() {
        let mut failures = 0;

        for _ in 1..HEALTH_FAILURES_UNTIL_DEAD {
            assert!(!update_health_failures(&mut failures, false).dead);
        }

        assert!(update_health_failures(&mut failures, false).dead);
        assert_eq!(failures, HEALTH_FAILURES_UNTIL_DEAD);
    }

    #[test]
    fn recovery_resets_thresholds() {
        let mut failures = 0;
        update_health_failures(&mut failures, false);
        update_health_failures(&mut failures, false);

        let outcome = update_health_failures(&mut failures, true);

        assert_eq!(failures, 0);
        assert!(!outcome.blocked);
        assert!(!outcome.dead);
    }

    #[test]
    fn ready_handshake_accepts_only_matching_protocol_version() {
        let valid = json!({
            "event": "ready",
            "protocol": SIDECAR_PROTOCOL_VERSION,
            "port": 51234,
            "token": "sitzungstoken"
        });
        assert_eq!(
            parse_ready_connection(&valid, None).unwrap(),
            ReadyEndpoint::Port(51234, "sitzungstoken".to_string())
        );

        for invalid in [
            json!({"event": "ready", "protocol": 999, "port": 51234, "token": "x"}),
            // A sidecar with protocol 1 does not know the v2 API.
            json!({"event": "ready", "protocol": 1, "port": 51234, "token": "x"}),
            json!({"event": "ready", "port": 51234, "token": "x"}),
            json!({"event": "ready", "protocol": SIDECAR_PROTOCOL_VERSION, "port": 0, "token": "x"}),
            json!({"event": "ready", "protocol": SIDECAR_PROTOCOL_VERSION, "port": 51234, "token": ""}),
            // A reported socket without a preset socket is an error.
            json!({"event": "ready", "protocol": SIDECAR_PROTOCOL_VERSION, "socket": "/tmp/s", "token": "x"}),
        ] {
            assert!(parse_ready_connection(&invalid, None).is_err());
        }
    }

    #[test]
    fn ready_handshake_with_socket_only_at_given_path() {
        let expected = Path::new("/tmp/unname-abc/s");
        let valid = json!({
            "event": "ready",
            "protocol": SIDECAR_PROTOCOL_VERSION,
            "socket": "/tmp/unname-abc/s",
            "token": "sitzungstoken"
        });
        assert_eq!(
            parse_ready_connection(&valid, Some(expected)).unwrap(),
            ReadyEndpoint::Socket(expected.to_path_buf(), "sitzungstoken".to_string())
        );

        for invalid in [
            // An external path: The sidecar would not be in the private folder.
            json!({"event": "ready", "protocol": SIDECAR_PROTOCOL_VERSION, "socket": "/tmp/anders/s", "token": "x"}),
            // A port instead of the socket: It listens on the network.
            json!({"event": "ready", "protocol": SIDECAR_PROTOCOL_VERSION, "port": 51234, "token": "x"}),
            json!({"event": "ready", "protocol": SIDECAR_PROTOCOL_VERSION, "socket": "/tmp/unname-abc/s", "port": 1, "token": "x"}),
            json!({"event": "ready", "protocol": SIDECAR_PROTOCOL_VERSION, "socket": "/tmp/unname-abc/s", "token": ""}),
        ] {
            assert!(parse_ready_connection(&invalid, Some(expected)).is_err());
        }
    }

    #[cfg(unix)]
    #[test]
    fn socket_folder_is_private_short_and_removed() {
        use std::os::unix::fs::PermissionsExt;

        let socket = SocketDirectory::create()
            .unwrap()
            .expect("Unix nimmt den Socket");
        let directory = socket.path().parent().unwrap().to_path_buf();
        let mode = std::fs::metadata(&directory).unwrap().permissions().mode();
        assert_eq!(mode & 0o777, 0o700);
        assert!(
            !socket.path().exists(),
            "der Sidecar legt den Socket selbst an"
        );
        assert!(socket.path_string().len() <= SOCKET_PATH_MAX_BYTES);
        drop(socket);
        assert!(!directory.exists());

        assert!(check_socket_path(&PathBuf::from(format!("/tmp/{}", "x".repeat(120)))).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn socket_connection_speaks_http_without_port() {
        // A small HTTP service on the Unix socket instead of the sidecar: The
        // connection client must reach it with a token, without TCP.
        use std::io::{Read, Write};
        use std::os::unix::net::UnixListener;

        let socket = SocketDirectory::create().unwrap().unwrap();
        let listener = UnixListener::bind(socket.path()).unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut buffer = [0u8; 1024];
            while !request.windows(4).any(|window| window == b"\r\n\r\n") {
                let read = stream.read(&mut buffer).unwrap();
                request.extend_from_slice(&buffer[..read]);
            }
            stream
                .write_all(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\nconnection: close\r\n\r\nok")
                .unwrap();
            String::from_utf8(request).unwrap()
        });

        let connection =
            SidecarConnection::unix(socket.path().to_path_buf(), "geheim".to_string()).unwrap();
        let body = tauri::async_runtime::block_on(async {
            connection
                .client()
                .get(format!("{}/health", connection.base_url))
                .bearer_auth(&connection.token)
                .send()
                .await
                .unwrap()
                .text()
                .await
                .unwrap()
        });
        assert_eq!(body, "ok");
        let request = server.join().unwrap().to_lowercase();
        assert!(request.starts_with("get /health http/1.1"));
        assert!(request.contains("authorization: bearer geheim"));
    }

    /// Reads an HTTP request without a body up to the empty line; `false` at
    /// the end of the connection.
    #[cfg(unix)]
    fn read_request(stream: &mut std::os::unix::net::UnixStream) -> bool {
        use std::io::Read;
        let mut request = Vec::new();
        let mut buffer = [0u8; 1024];
        while !request.windows(4).any(|window| window == b"\r\n\r\n") {
            match stream.read(&mut buffer) {
                Ok(0) | Err(_) => return false,
                Ok(read) => request.extend_from_slice(&buffer[..read]),
            }
        }
        true
    }

    #[cfg(unix)]
    const KEEP_ALIVE_OK: &[u8] = b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\nok";

    #[cfg(unix)]
    #[test]
    fn health_retries_once_when_server_closes_idle_connection() {
        // Reproduces what uvicorn does with `timeout_keep_alive`: The first
        // response keeps the connection open, the next request on it is
        // accepted by the server and then closed without a response. Before
        // then, it was "Health not reachable: error sending request".
        use std::io::Write;
        use std::os::unix::net::UnixListener;

        let socket = SocketDirectory::create().unwrap().unwrap();
        let listener = UnixListener::bind(socket.path()).unwrap();
        let server = std::thread::spawn(move || {
            let (mut first, _) = listener.accept().unwrap();
            assert!(read_request(&mut first));
            first.write_all(KEEP_ALIVE_OK).unwrap();
            assert!(
                read_request(&mut first),
                "zweite Anfrage auf derselben Verbindung"
            );
            drop(first);
            let (mut second, _) = listener.accept().unwrap();
            assert!(read_request(&mut second));
            second.write_all(KEEP_ALIVE_OK).unwrap();
        });

        let connection =
            SidecarConnection::unix(socket.path().to_path_buf(), "geheim".to_string()).unwrap();
        tauri::async_runtime::block_on(async {
            for _ in 0..2 {
                let response = probe_health(&connection).await.unwrap();
                assert_eq!(response.text().await.unwrap(), "ok");
            }
        });
        server.join().unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn idle_connection_is_dropped_before_server_drops_it() {
        // The server keeps every connection open, but the client must not use
        // it after `POOL_IDLE_TIMEOUT`.
        use std::io::Write;
        use std::os::unix::net::UnixListener;
        use std::sync::atomic::AtomicUsize;
        use std::sync::Arc;

        let socket = SocketDirectory::create().unwrap().unwrap();
        let listener = UnixListener::bind(socket.path()).unwrap();
        let accepted = Arc::new(AtomicUsize::new(0));
        let counter = accepted.clone();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { return };
                counter.fetch_add(1, Ordering::SeqCst);
                std::thread::spawn(move || {
                    while read_request(&mut stream) {
                        if stream.write_all(KEEP_ALIVE_OK).is_err() {
                            return;
                        }
                    }
                });
            }
        });

        let connection =
            SidecarConnection::unix(socket.path().to_path_buf(), "geheim".to_string()).unwrap();
        tauri::async_runtime::block_on(async {
            probe_health(&connection).await.unwrap();
            probe_health(&connection).await.unwrap();
            assert_eq!(
                accepted.load(Ordering::SeqCst),
                1,
                "kurz hintereinander: gepoolt"
            );
            tokio::time::sleep(POOL_IDLE_TIMEOUT + Duration::from_millis(700)).await;
            probe_health(&connection).await.unwrap();
        });
        assert_eq!(
            accepted.load(Ordering::SeqCst),
            2,
            "nach der Ruhezeit: neu verbunden"
        );
    }

    #[test]
    fn client_drops_idle_connections_before_uvicorn() {
        // uvicorn: `timeout_keep_alive` in sidecar/src/presidio_sidecar/__main__.py.
        const SIDECAR_KEEP_ALIVE: Duration = Duration::from_secs(30);
        assert!(POOL_IDLE_TIMEOUT < SIDECAR_KEEP_ALIVE);
        assert!(POOL_IDLE_TIMEOUT < HEALTH_INTERVAL);
    }

    #[test]
    fn single_health_outage_is_not_a_warning() {
        assert_eq!(health_failure_level(1), log::Level::Debug);
        assert_eq!(health_failure_level(2), log::Level::Warn);
        assert_eq!(
            health_failure_level(HEALTH_FAILURES_UNTIL_BLOCKED),
            log::Level::Warn
        );
    }

    #[test]
    fn error_description_names_kind_and_cause() {
        // Port 9 (discard) is practically never open on 127.0.0.1.
        let error = tauri::async_runtime::block_on(async {
            reqwest::Client::new()
                .get("http://127.0.0.1:9/health")
                .send()
                .await
                .unwrap_err()
        });
        let text = describe_request_error(&error);
        assert!(text.starts_with("[connect] "), "{text}");
        assert!(
            text.len() > error.to_string().len(),
            "die Ursache fehlt: {text}"
        );
        assert!(should_retry_health(&error));
    }

    #[test]
    fn ui_gets_scheme_and_no_token() {
        let connection = SidecarConnection::tcp(51234, "geheim".to_string()).unwrap();
        let webview = serde_json::to_value(connection.for_webview(7)).unwrap();
        assert_eq!(
            webview,
            json!({"base_url": crate::api_scheme::webview_base_url(), "generation": 7})
        );
        assert!(!webview.to_string().contains("geheim"));
        assert!(!webview.to_string().contains("51234"));
    }

    #[test]
    fn new_connection_replaces_old_health_watchers() {
        let state = SidecarState::default();
        let before = state.generation();

        state.clear_connection();

        assert_ne!(
            state.generation(),
            before,
            "Ein Waechter der alten Verbindung muss sich abmelden koennen"
        );
    }

    #[test]
    fn sidecar_stderr_keeps_its_level() {
        assert_eq!(
            sidecar_log_level(
                "2026-11-08 09:22:10,360 INFO uvicorn.error: Started server process [23106]"
            ),
            (
                log::Level::Info,
                "uvicorn.error: Started server process [23106]"
            )
        );
        assert_eq!(
            sidecar_log_level("2026-11-08 09:22:10,360 WARNING py.warnings: veraltet").0,
            log::Level::Warn
        );
        assert_eq!(
            sidecar_log_level("2026-11-08 09:22:10,360 CRITICAL x: weg").0,
            log::Level::Error
        );
        assert_eq!(
            sidecar_log_level("2026-11-08 09:22:10,360 DEBUG x: fein").0,
            log::Level::Debug
        );
        // Everything else remains a warning, unchanged.
        for line in [
            "Traceback (most recent call last):",
            "  from .app import create_app",
            "2026-11-08 09:22:10,360 NOTICE x: unbekannte Stufe",
            "2026-11-08 09:22:10 INFO ohne Millisekunden",
            "ä",
        ] {
            assert_eq!(sidecar_log_level(line), (log::Level::Warn, line));
        }
    }

    #[test]
    fn termination_is_described() {
        assert!(describe_termination(Some(0), None).contains("regulär"));
        assert!(describe_termination(Some(2), None).contains("Code 2"));
        assert!(describe_termination(None, Some(9)).contains("Signal 9"));
    }
}
