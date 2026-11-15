//! File inputs come through the shell.
//!
//! The UI has no file system access — not even indirect access via the sidecar.
//! Files come from only two sources triggered by a human: the system dialog
//! (`pick_inputs`) and a drop zone on the window (`WindowEvent::DragDrop`) — or
//! onto the dock icon or via "Open with" (`RunEvent::Opened`). The shell
//! remembers the paths under a random identifier; the UI receives only the
//! identifier and the file name. To submit, it names the identifier; the shell
//! reads the bytes and forwards them as multipart to the sidecar (`POST
//! /entries/{id}/files`). No one reads a path invented by the UI itself.
//!
//! Why the shell reads and not the sidecar: In the macOS bundle, the sidecar
//! inherits the app's sandbox but not the permissions that a dialog or a drop
//! zone grants later (the same applies to saving, `saving.rs`).

use std::collections::hash_map::RandomState;
use std::collections::HashMap;
use std::fs::{self, File};
use std::hash::{BuildHasher, Hasher};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, DragDropEvent, Emitter, Manager};
use tauri_plugin_dialog::DialogExt;

use crate::finder::{self, CloudState};
use crate::saving::EntryOrigins;
use crate::sidecar::{SidecarConnection, SidecarState};

/// Drag over event (`{ "active": bool }`).
pub const DRAG_EVENT: &str = "inputs://drag";
/// Drop zone selection event.
pub const DROP_EVENT: &str = "inputs://dropped";
/// Files opened via the dock or "Open with"; retrieve them with `take_opened`.
pub const OPENED_EVENT: &str = "inputs://opened";

/// The sidecar accepts no more files per request (`MAX_FILES_PER_REQUEST`); a
/// larger folder is truncated and reported.
pub const MAX_FILES: usize = 200;
/// Base profile limit (`base_profile.py`, `max_file_mb`).
pub const MAX_FILE_BYTES: u64 = 50 * 1024 * 1024;
/// Folder search does not go deeper than this.
const MAX_DEPTH: usize = 8;
/// Formats of the base profile (`base_profile.py` and
/// `DOCUMENT_FORMAT_ALIASES`).
///
/// Applies only when browsing a folder: There lies much that no one wanted to
/// protect (archives, programs, system preview images). A singly selected file,
/// however, always goes to the sidecar so it is rejected with reason, rather
/// than disappearing silently.
const FOLDER_EXTENSIONS: [&str; 11] = [
    "pdf", "docx", "xlsx", "csv", "txt", "md", "markdown", "png", "jpg", "jpeg", "jpe",
];
/// A request to the sidecar carries at most this many files or bytes. The body
/// resides entirely in memory once; a folder with 200 large scans should not
/// occupy gigabytes for this.
const BATCH_FILES: usize = 50;
const BATCH_BYTES: usize = 32 * 1024 * 1024;
/// Noted selections expire. A drop zone that no one submits should not keep
/// paths in memory permanently.
const SELECTION_TTL: Duration = Duration::from_secs(10 * 60);
const MAX_SELECTIONS: usize = 8;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;

/// What the UI learns about a selection: names, no paths.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct SelectionInfo {
    pub id: String,
    pub names: Vec<String>,
    /// Skipped in folder: links and files of other formats. Hidden files are
    /// not counted — no one sees them even in the file manager.
    pub skipped: usize,
    /// Found more than `MAX_FILES`; the rest is not included.
    pub truncated: bool,
}

/// The files collected from a user selection.
#[derive(Debug, Default, PartialEq)]
pub struct Collected {
    pub files: Vec<PathBuf>,
    pub skipped: usize,
    pub truncated: bool,
}

impl Collected {
    fn push(&mut self, path: PathBuf) {
        if self.files.len() >= MAX_FILES {
            self.truncated = true;
            return;
        }
        self.files.push(path);
    }
}

/// Resolve selected or dropped paths to files.
///
/// A chosen entry may be a symlink — the user intended it. Inside a folder the
/// search does not follow symlinks to avoid escaping or looping.
pub fn collect(chosen: &[PathBuf]) -> Collected {
    let mut collected = Collected::default();
    for path in chosen {
        if collected.truncated {
            break;
        }
        match fs::metadata(path) {
            Ok(metadata) if metadata.is_dir() => walk(path, 0, &mut collected),
            Ok(metadata) if metadata.is_file() => collected.push(path.clone()),
            _ => collected.skipped += 1,
        }
    }
    collected
}

fn walk(directory: &Path, depth: usize, collected: &mut Collected) {
    let Ok(entries) = fs::read_dir(directory) else {
        collected.skipped += 1;
        return;
    };
    // Sort order is deterministic: the same input always yields the same
    // entries, and truncating to `MAX_FILES` never drops a different item by
    // chance.
    let mut entries: Vec<_> = entries.filter_map(Result::ok).collect();
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        if collected.truncated {
            return;
        }
        if entry.file_name().to_string_lossy().starts_with('.') {
            continue;
        }
        let Ok(file_type) = entry.file_type() else {
            collected.skipped += 1;
            continue;
        };
        let path = entry.path();
        if file_type.is_symlink() {
            collected.skipped += 1;
        } else if file_type.is_dir() {
            if depth + 1 < MAX_DEPTH {
                walk(&path, depth + 1, collected);
            } else {
                collected.skipped += 1;
            }
        } else if file_type.is_file() && has_folder_extension(&path) {
            collected.push(path);
        } else {
            collected.skipped += 1;
        }
    }
}

fn has_folder_extension(path: &Path) -> bool {
    path.extension()
        .and_then(|extension| extension.to_str())
        .map(|extension| {
            let extension = extension.to_ascii_lowercase();
            FOLDER_EXTENSIONS.contains(&extension.as_str())
        })
        .unwrap_or(false)
}

fn display_name(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| "dokument".to_string())
}

pub fn info(id: String, collected: &Collected) -> SelectionInfo {
    SelectionInfo {
        id,
        names: collected
            .files
            .iter()
            .map(|path| display_name(path))
            .collect(),
        skipped: collected.skipped,
        truncated: collected.truncated,
    }
}

struct Selection {
    files: Vec<PathBuf>,
    created: Instant,
}

/// The recorded selections. Each can be submitted exactly once.
pub struct InputSelections {
    selections: Mutex<HashMap<String, Selection>>,
    counter: AtomicU64,
    /// Randomly keyed (`RandomState` draws keys from the OS); these become the
    /// identifiers.
    keys: RandomState,
    ttl: Duration,
}

impl Default for InputSelections {
    fn default() -> Self {
        Self::with_ttl(SELECTION_TTL)
    }
}

impl InputSelections {
    pub fn with_ttl(ttl: Duration) -> Self {
        Self {
            selections: Mutex::new(HashMap::new()),
            counter: AtomicU64::new(0),
            keys: RandomState::new(),
            ttl,
        }
    }

    pub fn remember(&self, files: Vec<PathBuf>) -> String {
        let id = self.new_id();
        let mut selections = self.selections.lock().expect("Eingangs-Sperre vergiftet");
        selections.retain(|_, selection| selection.created.elapsed() < self.ttl);
        while selections.len() >= MAX_SELECTIONS {
            let oldest = selections
                .iter()
                .min_by_key(|(_, selection)| selection.created)
                .map(|(id, _)| id.clone());
            match oldest {
                Some(oldest) => selections.remove(&oldest),
                None => break,
            };
        }
        selections.insert(
            id.clone(),
            Selection {
                files,
                created: Instant::now(),
            },
        );
        id
    }

    /// Retrieves a selection. A second call with the same ID returns nothing:
    /// repeated submission would otherwise duplicate every file in the entry.
    pub fn take(&self, id: &str) -> Option<Vec<PathBuf>> {
        let selection = self
            .selections
            .lock()
            .expect("Eingangs-Sperre vergiftet")
            .remove(id)?;
        (selection.created.elapsed() < self.ttl).then_some(selection.files)
    }

    fn new_id(&self) -> String {
        let number = self.counter.fetch_add(1, Ordering::Relaxed);
        let half = |salt: u8| {
            let mut hasher = self.keys.build_hasher();
            hasher.write_u64(number);
            hasher.write_u8(salt);
            hasher.finish()
        };
        format!("{:016x}{:016x}", half(1), half(2))
    }
}

/// Opens the system dialog for files or a folder. `None` means cancelled.
pub async fn pick(
    app: &AppHandle,
    folder: bool,
    title: String,
) -> Result<Option<Vec<PathBuf>>, String> {
    let (sender, receiver) = tokio::sync::oneshot::channel();
    let dialog = app.dialog().file().set_title(title);
    if folder {
        dialog.pick_folder(move |path| {
            let _ = sender.send(path.map(|path| vec![path]));
        });
    } else {
        dialog.pick_files(move |paths| {
            let _ = sender.send(paths);
        });
    }
    let chosen = receiver
        .await
        .map_err(|_| "Der Auswahldialog wurde unerwartet beendet.".to_string())?;
    Ok(chosen.map(|paths| {
        paths
            .into_iter()
            .filter_map(|path| path.into_path().ok())
            .collect()
    }))
}

/// Records a selection and describes it for the UI.
pub fn remember(selections: &InputSelections, chosen: &[PathBuf]) -> SelectionInfo {
    let collected = collect(chosen);
    let id = selections.remember(collected.files.clone());
    info(id, &collected)
}

/// The staging area on the window. Paths do not go to the UI, only the ID and
/// names.
pub fn handle_drag_drop(app: &AppHandle, event: &DragDropEvent) {
    match event {
        DragDropEvent::Enter { .. } => {
            let _ = app.emit(DRAG_EVENT, json!({ "active": true }));
        }
        DragDropEvent::Leave => {
            let _ = app.emit(DRAG_EVENT, json!({ "active": false }));
        }
        DragDropEvent::Drop { paths, .. } => {
            let _ = app.emit(DRAG_EVENT, json!({ "active": false }));
            if paths.is_empty() {
                return;
            }
            let paths = paths.clone();
            let app = app.clone();
            // Large folders are not scanned on the event thread.
            tauri::async_runtime::spawn_blocking(move || {
                let info = remember(&app.state::<InputSelections>(), &paths);
                let _ = app.emit(DROP_EVENT, info);
            });
        }
        _ => {}
    }
}

/// Documents with which macOS opened the app until the UI picks them up.
///
/// On cold start via a document, `RunEvent::Opened` arrives before the UI is
/// loaded; and the staging area only stops at the home page. Therefore the
/// shell sends no finished result, only a signal (`OPENED_EVENT`). The UI picks
/// up as soon as it is ready. Multiple open events before pickup land in one
/// selection.
#[derive(Default)]
pub struct OpenedInputs {
    paths: Mutex<Vec<PathBuf>>,
}

impl OpenedInputs {
    pub fn add(&self, paths: impl IntoIterator<Item = PathBuf>) {
        let mut pending = self.paths.lock().expect("Öffnen-Sperre vergiftet");
        for path in paths {
            if pending.len() >= MAX_FILES {
                break;
            }
            if !pending.contains(&path) {
                pending.push(path);
            }
        }
    }

    pub fn take(&self) -> Vec<PathBuf> {
        std::mem::take(&mut *self.paths.lock().expect("Öffnen-Sperre vergiftet"))
    }
}

/// `RunEvent::Opened`: Paths are recorded like a staging area, the window comes
/// to front. The app accepts no other URLs than files.
pub fn handle_opened(app: &AppHandle, urls: &[tauri::Url]) {
    let paths: Vec<PathBuf> = urls
        .iter()
        .filter(|url| url.scheme() == "file")
        .filter_map(|url| url.to_file_path().ok())
        .collect();
    if paths.is_empty() {
        return;
    }
    app.state::<OpenedInputs>().add(paths);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
    let _ = app.emit(OPENED_EVENT, ());
}

/// Retrieves the currently opened documents as a selection; returns `None` if
/// no documents are waiting.
pub fn take_opened(app: &AppHandle) -> Option<SelectionInfo> {
    let paths = app.state::<OpenedInputs>().take();
    if paths.is_empty() {
        return None;
    }
    Some(remember(&app.state::<InputSelections>(), &paths))
}

/// Reads the files in a selection and forwards them to the sidecar.
///
/// The result has the shape of the sidecar response (`entry`, `accepted`,
/// `rejected`), merged across all sub-requests. What the shell already rejects
/// beforehand (unreadable, too large) or could not pass through appears with
/// its own code under `rejected`; the UI translates the codes.
///
/// For each accepted file, the shell remembers the original path
/// (`EntryOrigins`): for the target folder „neben den Originalen" and for
/// „Nochmal bearbeiten" after the job TTL expires (E9).
pub async fn submit(
    sidecar: &SidecarState,
    selections: &InputSelections,
    origins: &EntryOrigins,
    entry_id: &str,
    selection: &str,
) -> Result<Value, String> {
    validate_entry_id(entry_id)?;
    let connection = sidecar.upload_connection().ok_or_else(|| {
        "Die lokale Verarbeitung ist nicht bereit; neue Dokumente bleiben gesperrt.".to_string()
    })?;
    let files = selections.take(selection).ok_or_else(|| {
        "Die Auswahl ist nicht mehr gültig. Bitte die Dateien noch einmal wählen.".to_string()
    })?;

    let client = connection.client();
    let mut outcome = Outcome::default();
    let mut batch: Vec<Part> = Vec::new();
    let mut batch_bytes = 0usize;
    for path in files {
        let read = tauri::async_runtime::spawn_blocking(move || read_input(&path))
            .await
            .map_err(|_| "Eine Datei konnte nicht gelesen werden.".to_string())?;
        let part = match read {
            Ok(part) => part,
            Err(rejection) => {
                outcome.rejected.push(rejection);
                continue;
            }
        };
        if !batch.is_empty()
            && (batch.len() >= BATCH_FILES || batch_bytes + part.content.len() > BATCH_BYTES)
        {
            send(
                &client,
                &connection,
                entry_id,
                &batch,
                &mut outcome,
                origins,
            )
            .await;
            batch.clear();
            batch_bytes = 0;
        }
        batch_bytes += part.content.len();
        batch.push(part);
    }
    if !batch.is_empty() {
        send(
            &client,
            &connection,
            entry_id,
            &batch,
            &mut outcome,
            origins,
        )
        .await;
    }
    Ok(outcome.into_value())
}

pub fn validate_entry_id(entry_id: &str) -> Result<(), String> {
    if !entry_id.is_empty()
        && entry_id.len() <= 128
        && entry_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        Ok(())
    } else {
        Err("Ungültige Eintrags-ID.".to_string())
    }
}

#[derive(Debug)]
pub struct Part {
    pub name: String,
    pub content: Vec<u8>,
    /// Where the file came from; only the shell knows this.
    pub path: Option<PathBuf>,
}

impl Part {
    pub fn sha256(&self) -> String {
        format!("{:x}", Sha256::digest(&self.content))
    }
}

fn rejection(name: &str, code: &str, message: &str) -> Value {
    json!({ "name": name, "code": code, "message": message })
}

pub fn read_input(path: &Path) -> Result<Part, Value> {
    let name = display_name(path);
    let unreadable = || {
        rejection(
            &name,
            "unreadable",
            "Die Datei konnte nicht gelesen werden.",
        )
    };
    let too_large = || rejection(&name, "file_too_large", "Die Datei ist größer als 50 MiB.");
    // An iCloud placeholder would have 0 bytes here or crash when read. It is
    // still requested: it will be present on the next drop.
    if finder::state(path) == CloudState::NotDownloaded {
        if let Err(error) = finder::start_download(path) {
            log::warn!("iCloud-Datei ließ sich nicht anfordern: {error}");
        }
        return Err(rejection(
            &name,
            "cloud_not_downloaded",
            "Die Datei liegt nur in iCloud und wird gerade geladen.",
        ));
    }
    let content = finder::read_coordinated(path, |granted| -> Result<Vec<u8>, Value> {
        let metadata = fs::metadata(granted).map_err(|_| unreadable())?;
        if !metadata.is_file() {
            return Err(unreadable());
        }
        if metadata.len() > MAX_FILE_BYTES {
            return Err(too_large());
        }
        // `take` enforces the limit even if the file grows during reading.
        let mut content = Vec::with_capacity(metadata.len() as usize);
        File::open(granted)
            .and_then(|file| file.take(MAX_FILE_BYTES + 1).read_to_end(&mut content))
            .map_err(|_| unreadable())?;
        if content.len() as u64 > MAX_FILE_BYTES {
            return Err(too_large());
        }
        Ok(content)
    })
    .map_err(|_| unreadable())??;
    Ok(Part {
        name,
        content,
        path: Some(path.to_path_buf()),
    })
}

#[derive(Default)]
struct Outcome {
    entry: Option<Value>,
    accepted: Vec<Value>,
    rejected: Vec<Value>,
}

impl Outcome {
    fn absorb(&mut self, response: Value) {
        let Value::Object(mut response) = response else {
            return;
        };
        if let Some(entry) = response.remove("entry").filter(Value::is_object) {
            self.entry = Some(entry);
        }
        for (key, target) in [
            ("accepted", &mut self.accepted),
            ("rejected", &mut self.rejected),
        ] {
            if let Some(Value::Array(items)) = response.remove(key) {
                target.extend(items);
            }
        }
    }

    fn not_submitted(&mut self, parts: &[Part]) {
        for part in parts {
            self.rejected.push(rejection(
                &part.name,
                "not_submitted",
                "Die Datei konnte nicht an die lokale Verarbeitung übergeben werden.",
            ));
        }
    }

    fn into_value(self) -> Value {
        json!({
            "entry": self.entry,
            "accepted": self.accepted,
            "rejected": self.rejected,
        })
    }
}

async fn send(
    client: &reqwest::Client,
    connection: &SidecarConnection,
    entry_id: &str,
    parts: &[Part],
    outcome: &mut Outcome,
    origins: &EntryOrigins,
) {
    let url = format!("{}/entries/{entry_id}/files", connection.base_url);
    match post_parts(client, connection, &url, parts).await {
        Ok(response) => {
            remember_origins(origins, entry_id, parts, &response);
            outcome.absorb(response);
        }
        Err(reason) => {
            log::warn!("Dateien wurden nicht übergeben: {reason}");
            outcome.not_submitted(parts);
        }
    }
}

/// Maps accepted files to their originals via the SHA-256 of the submitted
/// bytes, which the sidecar reports for each file. Names alone are
/// insufficient: two folders can contain files with identical names.
fn remember_origins(origins: &EntryOrigins, entry_id: &str, parts: &[Part], response: &Value) {
    let Some(accepted) = response.get("accepted").and_then(Value::as_array) else {
        return;
    };
    let hashes: Vec<String> = parts.iter().map(Part::sha256).collect();
    let mut used = vec![false; parts.len()];
    for item in accepted {
        let (Some(file_id), Some(hash)) = (
            item.get("id").and_then(Value::as_str),
            item.get("source_sha256").and_then(Value::as_str),
        ) else {
            continue;
        };
        let found = (0..parts.len()).find(|&index| !used[index] && hashes[index] == hash);
        if let Some(index) = found {
            used[index] = true;
            if let Some(path) = &parts[index].path {
                origins.remember_file(entry_id, file_id, path);
            }
        }
    }
}

/// Schickt Dateien als Multipart an einen Sidecar-Endpunkt.
pub async fn post_parts(
    client: &reqwest::Client,
    connection: &SidecarConnection,
    url: &str,
    parts: &[Part],
) -> Result<Value, String> {
    let (boundary, body) = multipart(parts);
    let response = client
        .post(url)
        .bearer_auth(&connection.token)
        .header(
            reqwest::header::CONTENT_TYPE,
            format!("multipart/form-data; boundary={boundary}"),
        )
        .body(body)
        .send()
        .await
        .map_err(|error| format!("nicht erreichbar: {error}"))?;
    let status = response.status();
    if response.content_length().unwrap_or(0) > MAX_RESPONSE_BYTES as u64 {
        return Err("unerwartet große Antwort".to_string());
    }
    let bytes = response
        .bytes()
        .await
        .map_err(|error| format!("Antwort nicht lesbar: {error}"))?;
    if bytes.len() > MAX_RESPONSE_BYTES {
        return Err("unerwartet große Antwort".to_string());
    }
    if !status.is_success() {
        return Err(sidecar_reason(&bytes).unwrap_or_else(|| format!("Status {status}")));
    }
    serde_json::from_slice(&bytes).map_err(|error| format!("Antwort ungültig: {error}"))
}

/// A rejection code (`detail.code`), if the sidecar provides one.
fn sidecar_reason(body: &[u8]) -> Option<String> {
    let payload: Value = serde_json::from_slice(body).ok()?;
    payload
        .get("detail")
        .and_then(|detail| detail.get("code"))
        .and_then(Value::as_str)
        .map(str::to_string)
}

/// Constructs a `multipart/form-data` body with one `file` field per file.
///
/// The boundary is random and guaranteed not to appear in any content —
/// otherwise the sidecar would truncate a file at this point.
fn multipart(parts: &[Part]) -> (String, Vec<u8>) {
    let keys = RandomState::new();
    let mut attempt = 0u64;
    loop {
        let boundary = format!(
            "unname-{:016x}{:016x}",
            keys.hash_one((attempt, 1u8)),
            keys.hash_one((attempt, 2u8))
        );
        let clash = parts.iter().any(|part| {
            contains(&part.content, boundary.as_bytes()) || part.name.contains(&boundary)
        });
        if !clash {
            let body = encode(parts, &boundary);
            return (boundary, body);
        }
        attempt += 1;
    }
}

fn encode(parts: &[Part], boundary: &str) -> Vec<u8> {
    let size = parts
        .iter()
        .map(|part| part.content.len() + part.name.len() + 160)
        .sum::<usize>();
    let mut body = Vec::with_capacity(size + boundary.len() + 8);
    for part in parts {
        body.extend_from_slice(b"--");
        body.extend_from_slice(boundary.as_bytes());
        body.extend_from_slice(b"\r\n");
        body.extend_from_slice(
            format!(
                "Content-Disposition: form-data; name=\"file\"; filename=\"{}\"\r\n",
                escape_filename(&part.name)
            )
            .as_bytes(),
        );
        body.extend_from_slice(b"Content-Type: application/octet-stream\r\n\r\n");
        body.extend_from_slice(&part.content);
        body.extend_from_slice(b"\r\n");
    }
    body.extend_from_slice(b"--");
    body.extend_from_slice(boundary.as_bytes());
    body.extend_from_slice(b"--\r\n");
    body
}

/// As browsers do (HTML, "multipart/form-data encoding algorithm"): UTF-8
/// remains raw; only quotes and line breaks are encoded. This ensures umlauts
/// arrive at the sidecar unchanged.
fn escape_filename(name: &str) -> String {
    name.replace('"', "%22")
        .replace('\r', "%0D")
        .replace('\n', "%0A")
}

fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    !needle.is_empty()
        && haystack.len() >= needle.len()
        && haystack
            .windows(needle.len())
            .any(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tree() -> tempfile::TempDir {
        let root = tempfile::tempdir().expect("tempdir");
        let base = root.path();
        fs::write(base.join("Rechnung.pdf"), b"%PDF").expect("pdf");
        fs::write(base.join("Notiz.TXT"), b"Hans").expect("txt");
        fs::write(base.join("programm.exe"), b"MZ").expect("exe");
        fs::write(base.join(".DS_Store"), b"x").expect("hidden");
        fs::create_dir(base.join("Unterordner")).expect("dir");
        fs::write(base.join("Unterordner").join("Scan.jpeg"), b"jpg").expect("jpeg");
        fs::create_dir(base.join(".git")).expect("hidden dir");
        fs::write(base.join(".git").join("config.txt"), b"x").expect("hidden file");
        root
    }

    fn names(collected: &Collected) -> Vec<String> {
        collected
            .files
            .iter()
            .map(|path| display_name(path))
            .collect()
    }

    #[test]
    fn folder_yields_only_matching_visible_files_in_fixed_order() {
        let root = tree();

        let collected = collect(&[root.path().to_path_buf()]);

        assert_eq!(
            names(&collected),
            ["Notiz.TXT", "Rechnung.pdf", "Scan.jpeg"]
        );
        // The program is skipped and counted; hidden items do not count.
        assert_eq!(collected.skipped, 1);
        assert!(!collected.truncated);
    }

    #[test]
    fn single_chosen_file_passes_in_unknown_format() {
        let root = tree();
        let exe = root.path().join("programm.exe");

        let collected = collect(std::slice::from_ref(&exe));

        // The sidecar then rejects them with a reason, rather than letting them
        // disappear silently.
        assert_eq!(collected.files, vec![exe]);
        assert_eq!(collected.skipped, 0);
    }

    #[test]
    fn missing_path_is_counted_not_read() {
        let collected = collect(&[PathBuf::from("/gibt/es/nicht.pdf")]);
        assert!(collected.files.is_empty());
        assert_eq!(collected.skipped, 1);
    }

    #[cfg(unix)]
    #[test]
    fn folder_scan_follows_no_symlink() {
        let root = tree();
        let outside = tempfile::tempdir().expect("outside");
        fs::write(outside.path().join("fremd.pdf"), b"%PDF").expect("fremd");
        std::os::unix::fs::symlink(outside.path(), root.path().join("Link")).expect("link");
        std::os::unix::fs::symlink(
            outside.path().join("fremd.pdf"),
            root.path().join("verlinkt.pdf"),
        )
        .expect("file link");

        let collected = collect(&[root.path().to_path_buf()]);

        assert!(!names(&collected).contains(&"fremd.pdf".to_string()));
        assert!(!names(&collected).contains(&"verlinkt.pdf".to_string()));
        assert_eq!(collected.skipped, 3);
    }

    #[test]
    fn large_folder_is_truncated_and_reported() {
        let root = tempfile::tempdir().expect("tempdir");
        for index in 0..(MAX_FILES + 5) {
            fs::write(root.path().join(format!("{index:04}.txt")), b"x").expect("file");
        }

        let collected = collect(&[root.path().to_path_buf()]);

        assert_eq!(collected.files.len(), MAX_FILES);
        assert!(collected.truncated);
        assert_eq!(display_name(&collected.files[0]), "0000.txt");
    }

    #[test]
    fn selection_can_be_submitted_exactly_once() {
        let selections = InputSelections::default();
        let id = selections.remember(vec![PathBuf::from("/a.pdf")]);

        assert_eq!(id.len(), 32);
        assert_eq!(selections.take(&id), Some(vec![PathBuf::from("/a.pdf")]));
        assert_eq!(selections.take(&id), None);
        assert_eq!(selections.take("erfunden"), None);
    }

    #[test]
    fn ids_are_distinct_and_old_selections_expire() {
        let selections = InputSelections::with_ttl(Duration::ZERO);
        let first = selections.remember(vec![PathBuf::from("/a.pdf")]);
        let second = selections.remember(vec![PathBuf::from("/b.pdf")]);

        assert_ne!(first, second);
        assert_eq!(selections.take(&second), None);
    }

    #[test]
    fn keeps_at_most_newest_selections() {
        let selections = InputSelections::default();
        let ids: Vec<_> = (0..MAX_SELECTIONS + 2)
            .map(|index| selections.remember(vec![PathBuf::from(format!("/{index}.pdf"))]))
            .collect();

        assert_eq!(selections.take(&ids[0]), None);
        assert_eq!(selections.take(&ids[1]), None);
        assert!(selections.take(ids.last().expect("last")).is_some());
    }

    #[test]
    fn multipart_keeps_content_and_umlauts_unchanged() {
        let parts = [
            Part {
                name: "Kündigung \"neu\".txt".to_string(),
                content: b"Hans Meier\r\n--nicht-die-grenze".to_vec(),
                path: None,
            },
            Part {
                name: "leer.txt".to_string(),
                content: Vec::new(),
                path: None,
            },
        ];

        let (boundary, body) = multipart(&parts);
        let text = String::from_utf8(body).expect("utf8");

        assert!(boundary.starts_with("unname-"));
        assert!(text.starts_with(&format!("--{boundary}\r\n")));
        assert!(text.ends_with(&format!("--{boundary}--\r\n")));
        assert!(text.contains("filename=\"Kündigung %22neu%22.txt\""));
        assert!(text.contains("\r\n\r\nHans Meier\r\n--nicht-die-grenze\r\n"));
        assert_eq!(text.matches(&format!("--{boundary}\r\n")).count(), 2);
    }

    #[test]
    fn newline_in_name_does_not_break_header() {
        assert_eq!(escape_filename("a\r\nb.txt"), "a%0D%0Ab.txt");
    }

    #[test]
    fn oversized_file_is_rejected_before_reading() {
        let root = tempfile::tempdir().expect("tempdir");
        let path = root.path().join("gross.pdf");
        File::create(&path)
            .and_then(|file| file.set_len(MAX_FILE_BYTES + 1))
            .expect("sparse");

        let rejected = read_input(&path).expect_err("too large");

        assert_eq!(rejected["code"], "file_too_large");
        assert_eq!(rejected["name"], "gross.pdf");
    }

    #[test]
    fn responses_of_partial_requests_are_merged() {
        let mut outcome = Outcome::default();
        outcome
            .rejected
            .push(rejection("gross.pdf", "file_too_large", ""));
        outcome.absorb(json!({
            "entry": { "id": "e1", "files": [1] },
            "accepted": [{ "id": "j1" }],
            "rejected": [{ "name": "x.exe", "code": "unsupported_file_format" }],
        }));
        outcome.absorb(json!({
            "entry": { "id": "e1", "files": [1, 2] },
            "accepted": [{ "id": "j2" }],
            "rejected": [],
        }));
        outcome.not_submitted(&[Part {
            name: "c.pdf".to_string(),
            content: Vec::new(),
            path: None,
        }]);

        let value = outcome.into_value();

        assert_eq!(value["entry"]["files"], json!([1, 2]));
        assert_eq!(value["accepted"].as_array().map(Vec::len), Some(2));
        let codes: Vec<_> = value["rejected"]
            .as_array()
            .expect("rejected")
            .iter()
            .map(|item| item["code"].as_str().unwrap_or_default().to_string())
            .collect();
        assert_eq!(
            codes,
            ["file_too_large", "unsupported_file_format", "not_submitted"]
        );
    }

    #[test]
    fn accepted_files_are_matched_to_originals_by_hash() {
        let origins = EntryOrigins::default();
        let parts = [
            Part {
                name: "a.txt".to_string(),
                content: b"erste".to_vec(),
                path: Some(PathBuf::from("/Kunden/eins/a.txt")),
            },
            Part {
                name: "a.txt".to_string(),
                content: b"zweite".to_vec(),
                path: Some(PathBuf::from("/Kunden/zwei/a.txt")),
            },
        ];
        // The sidecar does not necessarily report accepted files in submission
        // order; a rejected one is missing entirely.
        let response = json!({
            "accepted": [
                { "id": "j2", "name": "a.txt", "source_sha256": parts[1].sha256() },
                { "id": "j1", "name": "a.txt", "source_sha256": parts[0].sha256() },
                { "id": "fremd", "name": "x.txt", "source_sha256": "0".repeat(64) },
            ]
        });

        remember_origins(&origins, "e1", &parts, &response);

        assert_eq!(
            origins.original("e1", "j1"),
            Some(PathBuf::from("/Kunden/eins/a.txt"))
        );
        assert_eq!(
            origins.original("e1", "j2"),
            Some(PathBuf::from("/Kunden/zwei/a.txt"))
        );
        assert_eq!(origins.original("e1", "fremd"), None);
    }

    #[test]
    fn opened_documents_collect_without_duplicates_until_taken() {
        let opened = OpenedInputs::default();
        opened.add([PathBuf::from("/a.pdf"), PathBuf::from("/b.pdf")]);
        opened.add([PathBuf::from("/b.pdf"), PathBuf::from("/c.pdf")]);
        assert_eq!(
            opened.take(),
            vec![
                PathBuf::from("/a.pdf"),
                PathBuf::from("/b.pdf"),
                PathBuf::from("/c.pdf")
            ]
        );
        assert!(opened.take().is_empty());
        opened.add((0..MAX_FILES + 5).map(|n| PathBuf::from(format!("/{n}.pdf"))));
        assert_eq!(opened.take().len(), MAX_FILES);
    }

    #[test]
    fn entry_id_contains_no_path() {
        assert!(validate_entry_id("3f2a-b_9").is_ok());
        assert!(validate_entry_id("../jobs").is_err());
        assert!(validate_entry_id("").is_err());
    }
}
