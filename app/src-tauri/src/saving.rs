//! Saving an entry via the shell.
//!
//! "Save all n files" runs in three steps:
//!
//! 1. The sidecar computes the final state, exports each file to a private
//!    staging folder in the shell's temp area, writes the mappings to the
//!    translation table and the audit log (`POST /entries/{id}/save`).
//! 2. The shell verifies each staged file (location, type, size, SHA-256) and
//!    publishes the same bytes to the target folder — never overwriting: if the
//!    name already exists, the copy is named `… (2)`.
//! 3. It reports the paths back (`POST /entries/{id}/outputs`); only then is
//!    the entry considered saved. If a step fails, the shell reverts what it
//!    published in this run — a batch lands entirely or not at all.
//!
//! The target folder is located by default next to the originals
//! (`…/protected/`). The UI only displays the folder name in the app language,
//! never a path. If the shell cannot write there (macOS sandbox: shares apply
//! to the placed files, not their folder) or does not know the originals (after
//! a restart), the system dialog asks. A folder chosen there is remembered as a
//! Security-Scoped Bookmark and remains valid for the entry.
//!
//! The shell knows the original paths only in memory: they often contain names
//! ("Angebot_Hofer.docx") and therefore must not be stored unencrypted on disk.

use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Component, Path, PathBuf};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;

use crate::diagnostics::{DiagnosticsLog, Severity};
use crate::finder;
use crate::inputs::{self, Part};
use crate::platform_security::{SecurityScopedBookmarks, SidecarRuntimeDirectories};
use crate::sidecar::{SidecarConnection, SidecarState};

/// The shell remembers this many entries; older ones fall out.
const MAX_ENTRIES: usize = 64;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
/// As large as the longest text the sidecar restores (`max_text_bytes`).
const MAX_TEXT_FILE_BYTES: usize = 12 * 1024 * 1024;
/// At most this many copies with the same name (`… (2)` to `… (999)`).
const MAX_DUPLICATES: u32 = 999;

/// Information shown to the UI when a save fails.
///
/// `code` comes from the sidecar (`irreversible_confirmation_required`,
/// `files_expired`, `pdf_check_failed` …) or from the shell (`not_ready`,
/// `target`, `publish`). The UI translates the code; `message` is only for the
/// diagnostic log.
#[derive(Debug, Serialize, PartialEq)]
pub struct SaveFailure {
    pub code: String,
    pub message: String,
    pub file_id: Option<String>,
}

impl SaveFailure {
    fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.to_string(),
            message: message.into(),
            file_id: None,
        }
    }
}

impl From<String> for SaveFailure {
    fn from(message: String) -> Self {
        Self::new("save_failed", message)
    }
}

// -- Originals and target folder per entry ------------------------------------

#[derive(Default)]
struct Origin {
    /// Map file IDs to their original paths, stored in the order they were
    /// submitted.
    files: Vec<(String, PathBuf)>,
    /// The target folder selected in the dialog (identified by a bookmark key).
    target: Option<PathBuf>,
}

#[derive(Default)]
struct Origins {
    order: Vec<String>,
    entries: HashMap<String, Origin>,
}

/// Store original paths and selected target folders for each entry; this data
/// exists only in memory.
#[derive(Default)]
pub struct EntryOrigins {
    inner: Mutex<Origins>,
}

impl EntryOrigins {
    fn with_entry<T>(&self, entry_id: &str, change: impl FnOnce(&mut Origin) -> T) -> T {
        let mut origins = self.inner.lock().expect("Ursprungs-Sperre vergiftet");
        if !origins.entries.contains_key(entry_id) {
            if origins.order.len() >= MAX_ENTRIES {
                let oldest = origins.order.remove(0);
                origins.entries.remove(&oldest);
            }
            origins.order.push(entry_id.to_string());
        }
        change(origins.entries.entry(entry_id.to_string()).or_default())
    }

    fn read<T>(&self, entry_id: &str, read: impl FnOnce(&Origin) -> T) -> Option<T> {
        let origins = self.inner.lock().expect("Ursprungs-Sperre vergiftet");
        origins.entries.get(entry_id).map(read)
    }

    pub fn remember_file(&self, entry_id: &str, file_id: &str, path: &Path) {
        self.with_entry(entry_id, |origin| {
            origin.files.retain(|(id, _)| id != file_id);
            origin.files.push((file_id.to_string(), path.to_path_buf()));
        });
    }

    /// A newly imported file gets a new ID at the old location.
    pub fn replace_file(&self, entry_id: &str, old_id: &str, new_id: &str, path: &Path) {
        self.with_entry(entry_id, |origin| {
            match origin.files.iter_mut().find(|(id, _)| id == old_id) {
                Some(slot) => *slot = (new_id.to_string(), path.to_path_buf()),
                None => origin.files.push((new_id.to_string(), path.to_path_buf())),
            }
        });
    }

    pub fn original(&self, entry_id: &str, file_id: &str) -> Option<PathBuf> {
        self.read(entry_id, |origin| {
            origin
                .files
                .iter()
                .find(|(id, _)| id == file_id)
                .map(|(_, path)| path.clone())
        })
        .flatten()
    }

    /// Return the folder containing the first file, which is where "neben den Originalen" is located.
    pub fn originals_folder(&self, entry_id: &str) -> Option<PathBuf> {
        self.read(entry_id, |origin| {
            origin
                .files
                .first()
                .and_then(|(_, path)| path.parent().map(Path::to_path_buf))
        })
        .flatten()
    }

    pub fn target(&self, entry_id: &str) -> Option<PathBuf> {
        self.read(entry_id, |origin| origin.target.clone())
            .flatten()
    }

    pub fn set_target(&self, entry_id: &str, path: &Path) {
        self.with_entry(entry_id, |origin| origin.target = Some(path.to_path_buf()));
    }

    /// When an entry is deleted, remove all traces here: neither original paths
    /// nor target folders remain.
    pub fn forget(&self, entry_id: &str) {
        let mut origins = self.inner.lock().expect("Ursprungs-Sperre vergiftet");
        origins.order.retain(|id| id != entry_id);
        origins.entries.remove(entry_id);
    }

    /// Clear all stored data so that "Verlauf leeren" is no longer retained.
    pub fn forget_all(&self) {
        let mut origins = self.inner.lock().expect("Ursprungs-Sperre vergiftet");
        origins.order.clear();
        origins.entries.clear();
    }
}

// -- Save -------------------------------------------------------------------

pub struct SaveOptions {
    /// Name of the subfolder alongside the originals, in the app language.
    pub folder_name: String,
    /// Choose the target folder in a dialog, rather than the remembered or
    /// default one.
    pub choose: bool,
    pub dialog_title: String,
    /// Finder tag for each copy, in the app language (X5): "geschützt" (protected).
    pub tag: String,
    /// The directory of the last save according to the history. It serves only
    /// as the starting point for the dialog, never as a target without a
    /// dialog: After a restart, the shell no longer knows the originals, yet
    /// "Erneut speichern" still starts there.
    pub last_directory: Option<PathBuf>,
}

/// Where to write. `bookmark` is the key of a release confirmed in the dialog,
/// which is released again after writing.
struct Target {
    directory: PathBuf,
    bookmark: Option<PathBuf>,
}

#[allow(clippy::too_many_arguments)]
pub async fn save(
    app: &AppHandle,
    sidecar: &SidecarState,
    directories: &SidecarRuntimeDirectories,
    bookmarks: &SecurityScopedBookmarks,
    origins: &EntryOrigins,
    entry_id: &str,
    request: Value,
    options: SaveOptions,
) -> Result<Option<Value>, SaveFailure> {
    inputs::validate_entry_id(entry_id).map_err(|error| SaveFailure::new("invalid", error))?;
    validate_folder_name(&options.folder_name)
        .map_err(|error| SaveFailure::new("invalid", error))?;
    validate_tag(&options.tag).map_err(|error| SaveFailure::new("invalid", error))?;
    if !request.is_object() {
        return Err(SaveFailure::new("invalid", "Die Anfrage ist kein Objekt."));
    }
    // Explicitly the writable connection: saving requires a verified healthy
    // service, not just an reachable one.
    let connection = sidecar.writable_connection().ok_or_else(|| {
        SaveFailure::new(
            "not_ready",
            "Die lokale Verarbeitung ist nicht bereit; Speichern bleibt gesperrt.",
        )
    })?;
    let Some(target) = resolve_target(app, bookmarks, origins, entry_id, &options).await? else {
        return Ok(None);
    };
    let result = save_into(
        &connection,
        directories,
        &app.state::<DiagnosticsLog>(),
        entry_id,
        &request,
        &target,
        &options.tag,
    )
    .await;
    if let Some(key) = &target.bookmark {
        if let Err(error) = bookmarks.release(key) {
            log::warn!("Freigabe des Zielordners blieb offen: {error}");
        }
    }
    result.map(Some)
}

async fn resolve_target(
    app: &AppHandle,
    bookmarks: &SecurityScopedBookmarks,
    origins: &EntryOrigins,
    entry_id: &str,
    options: &SaveOptions,
) -> Result<Option<Target>, SaveFailure> {
    let originals = origins.originals_folder(entry_id);
    if !options.choose {
        if let Some(key) = origins.target(entry_id) {
            if let Ok(directory) = bookmarks.restore(&key) {
                if directory.is_dir() {
                    return Ok(Some(Target {
                        directory,
                        bookmark: Some(key),
                    }));
                }
                let _ = bookmarks.release(&key);
            }
        }
        if let Some(folder) = &originals {
            let default = folder.join(&options.folder_name);
            match fs::create_dir_all(&default) {
                Ok(()) if default.is_dir() => {
                    return Ok(Some(Target {
                        directory: default,
                        bookmark: None,
                    }))
                }
                // No release for the originals folder (sandbox) or a read-only
                // location: then the dialog asks.
                Ok(()) => {}
                Err(error) if error.kind() == io::ErrorKind::PermissionDenied => {}
                Err(error) => {
                    return Err(SaveFailure::new(
                        "target",
                        format!("Der Zielordner ließ sich nicht anlegen: {error}"),
                    ))
                }
            }
        }
    }
    let start = origins.target(entry_id).or(originals).or_else(|| {
        options
            .last_directory
            .clone()
            .filter(|directory| directory.is_absolute())
    });
    let Some(chosen) = pick_directory(app, &options.dialog_title, start.as_deref()).await? else {
        return Ok(None);
    };
    bookmarks
        .remember_dialog_target(&chosen)
        .map_err(|error| SaveFailure::new("target", error))?;
    origins.set_target(entry_id, &chosen);
    let directory = bookmarks
        .restore(&chosen)
        .map_err(|error| SaveFailure::new("target", error))?;
    Ok(Some(Target {
        directory,
        bookmark: Some(chosen),
    }))
}

async fn pick_directory(
    app: &AppHandle,
    title: &str,
    start: Option<&Path>,
) -> Result<Option<PathBuf>, SaveFailure> {
    let (sender, receiver) = tokio::sync::oneshot::channel();
    let mut dialog = app.dialog().file().set_title(title);
    if let Some(start) = start.filter(|start| start.is_dir()) {
        dialog = dialog.set_directory(start);
    }
    dialog.pick_folder(move |path| {
        let _ = sender.send(path);
    });
    let chosen = receiver
        .await
        .map_err(|_| SaveFailure::new("target", "Der Ordnerdialog wurde unerwartet beendet."))?;
    Ok(chosen.and_then(|path| path.into_path().ok()))
}

async fn save_into(
    connection: &SidecarConnection,
    directories: &SidecarRuntimeDirectories,
    diagnostics: &DiagnosticsLog,
    entry_id: &str,
    request: &Value,
    target: &Target,
    tag: &str,
) -> Result<Value, SaveFailure> {
    let client = connection.client();
    let saved = post_json(
        &client,
        connection,
        &format!("/entries/{entry_id}/save"),
        request,
    )
    .await?;
    let staging = StagedSave::from_response(&saved, directories.job_temp())
        .map_err(|error| SaveFailure::new("staging", error))?;

    let mut published: Vec<(String, PathBuf)> = Vec::new();
    let outcome = async {
        for file in &staging.files {
            let path = publish_unique(
                &file.path,
                &target.directory,
                &file.name,
                &file.sha256,
                file.size,
            )
            .map_err(|error| SaveFailure {
                code: "publish".to_string(),
                message: error,
                file_id: Some(file.file_id.clone()),
            })?;
            // Without a tag the copy is still saved; the error names neither
            // path nor name.
            if let Err(error) = finder::set_tag(&path, tag) {
                diagnostics.record(
                    "save",
                    Severity::Warning,
                    "Finder-Tag ließ sich nicht setzen.",
                    json!({ "fehler": error }),
                );
            }
            published.push((file.file_id.clone(), path));
        }
        sync_directory(&target.directory).map_err(|error| SaveFailure::new("publish", error))?;
        post_json(
            &client,
            connection,
            &format!("/entries/{entry_id}/outputs"),
            &json!({
                "directory": target.directory.to_string_lossy(),
                "files": published
                    .iter()
                    .map(|(file_id, path)| json!({
                        "file_id": file_id,
                        "path": path.to_string_lossy(),
                    }))
                    .collect::<Vec<_>>(),
            }),
        )
        .await
    }
    .await;
    staging.remove(directories.job_temp());

    match outcome {
        Ok(entry) => Ok(json!({
            "entry": entry,
            "directory": target.directory.to_string_lossy(),
            "files": published
                .iter()
                .map(|(file_id, path)| json!({
                    "file_id": file_id,
                    "path": path.to_string_lossy(),
                    "name": path.file_name().map(|name| name.to_string_lossy()),
                }))
                .collect::<Vec<_>>(),
            "unchanged": saved.get("unchanged").cloned().unwrap_or(json!([])),
            "committed_mappings": saved.get("committed_mappings").cloned().unwrap_or(json!(0)),
            // The folder is synchronized; masked is allowed, the UI only
            // comments on it.
            "cloud": finder::is_cloud_location(&target.directory),
        })),
        Err(error) => {
            // All or nothing: what this run published goes away again. The
            // assignments in the table remain — a renewed attempt writes the
            // same short codes.
            for (_, path) in &published {
                if let Err(remove) = fs::remove_file(path) {
                    log::warn!("Teilweise gespeicherte Datei blieb liegen: {remove}");
                }
            }
            Err(error)
        }
    }
}

async fn post_json(
    client: &reqwest::Client,
    connection: &SidecarConnection,
    path: &str,
    body: &Value,
) -> Result<Value, SaveFailure> {
    let bytes = serde_json::to_vec(body)
        .map_err(|error| SaveFailure::new("invalid", format!("Anfrage ungültig: {error}")))?;
    let response = client
        .post(format!("{}{path}", connection.base_url))
        .bearer_auth(&connection.token)
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .body(bytes)
        .send()
        .await
        .map_err(|error| SaveFailure::new("not_ready", format!("nicht erreichbar: {error}")))?;
    let status = response.status();
    if response.content_length().unwrap_or(0) > MAX_RESPONSE_BYTES as u64 {
        return Err(SaveFailure::new("save_failed", "unerwartet große Antwort"));
    }
    let bytes = response.bytes().await.map_err(|error| {
        SaveFailure::new("save_failed", format!("Antwort nicht lesbar: {error}"))
    })?;
    if bytes.len() > MAX_RESPONSE_BYTES {
        return Err(SaveFailure::new("save_failed", "unerwartet große Antwort"));
    }
    if !status.is_success() {
        return Err(rejection(&bytes, status));
    }
    serde_json::from_slice(&bytes)
        .map_err(|error| SaveFailure::new("save_failed", format!("Antwort ungültig: {error}")))
}

/// Code, message and file of a sidecar rejection (`detail`).
fn rejection(body: &[u8], status: reqwest::StatusCode) -> SaveFailure {
    let fallback = || SaveFailure::new("save_failed", format!("Status {status}"));
    let Ok(payload) = serde_json::from_slice::<Value>(body) else {
        return fallback();
    };
    match payload.get("detail") {
        Some(Value::Object(detail)) => SaveFailure {
            code: detail
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("save_failed")
                .to_string(),
            message: detail
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            file_id: detail
                .get("file_id")
                .and_then(Value::as_str)
                .map(str::to_string),
        },
        Some(Value::String(message)) => SaveFailure::new("save_failed", message.clone()),
        _ => fallback(),
    }
}

// -- Check staging ---------------------------------------------------------

#[derive(Debug, PartialEq)]
struct StagedFile {
    file_id: String,
    name: String,
    path: PathBuf,
    sha256: String,
    size: u64,
}

#[derive(Debug)]
struct StagedSave {
    directory: PathBuf,
    files: Vec<StagedFile>,
}

impl StagedSave {
    /// Validates the sidecar's response before touching any files.
    ///
    /// The staging directory must reside in the shell's private temp area, with
    /// each file located exactly at `<Ordner>/<Datei-ID>/<Name>` using a clean
    /// filename. A sidecar pointing elsewhere receives no publication.
    fn from_response(saved: &Value, job_temp: &Path) -> Result<Self, String> {
        let directory = saved
            .get("staging_directory")
            .and_then(Value::as_str)
            .map(PathBuf::from)
            .ok_or_else(|| "Antwort ohne Bereitstellungsordner.".to_string())?;
        let root = fs::canonicalize(job_temp)
            .map_err(|error| format!("Temp-Bereich nicht lesbar: {error}"))?;
        let resolved = fs::canonicalize(&directory)
            .map_err(|error| format!("Bereitstellungsordner fehlt: {error}"))?;
        if resolved == root || !resolved.starts_with(&root) {
            return Err("Der Bereitstellungsordner liegt außerhalb des Temp-Bereichs.".to_string());
        }
        let staged = saved
            .get("staged")
            .and_then(Value::as_array)
            .ok_or_else(|| "Antwort ohne bereitgestellte Dateien.".to_string())?;
        let mut files = Vec::with_capacity(staged.len());
        for item in staged {
            let text = |key: &str| {
                item.get(key)
                    .and_then(Value::as_str)
                    .map(str::to_string)
                    .ok_or_else(|| format!("Bereitgestellte Datei ohne {key}."))
            };
            let file_id = text("file_id")?;
            let name = text("name")?;
            let path = PathBuf::from(text("path")?);
            let sha256 = text("sha256")?.to_ascii_lowercase();
            let size = item
                .get("size_bytes")
                .and_then(Value::as_u64)
                .ok_or_else(|| "Bereitgestellte Datei ohne Größe.".to_string())?;
            if !valid_id(&file_id) || !plain_file_name(&name) {
                return Err("Bereitgestellte Datei mit ungültiger Kennung oder Name.".to_string());
            }
            if path != directory.join(&file_id).join(&name) {
                return Err("Bereitgestellte Datei liegt nicht am vereinbarten Ort.".to_string());
            }
            if sha256.len() != 64 || !sha256.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                return Err("Bereitgestellte Datei ohne gültige Prüfsumme.".to_string());
            }
            files.push(StagedFile {
                file_id,
                name,
                path,
                sha256,
                size,
            });
        }
        Ok(Self { directory, files })
    }

    fn remove(&self, job_temp: &Path) {
        let inside = match (
            fs::canonicalize(job_temp),
            fs::canonicalize(&self.directory),
        ) {
            (Ok(root), Ok(resolved)) => resolved != root && resolved.starts_with(root),
            _ => false,
        };
        if inside {
            if let Err(error) = fs::remove_dir_all(&self.directory) {
                log::warn!("Bereitstellungsordner blieb liegen: {error}");
            }
        }
    }
}

fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 128
        && id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

/// A single, ordinary file name without path components.
pub(crate) fn plain_file_name(name: &str) -> bool {
    let mut components = Path::new(name).components();
    matches!(components.next(), Some(Component::Normal(part)) if part == name)
        && components.next().is_none()
        && !name.contains(['/', '\\', ':', '\0'])
        && name.len() <= 255
}

/// A finder tag: short, single-line, and non-empty.
fn validate_tag(name: &str) -> Result<(), String> {
    let length = name.chars().count();
    if (1..=64).contains(&length) && !name.chars().any(char::is_control) {
        Ok(())
    } else {
        Err("Ungültiger Name für das Finder-Tag.".to_string())
    }
}

fn validate_folder_name(name: &str) -> Result<(), String> {
    if plain_file_name(name) && !name.starts_with('.') && name.chars().count() <= 64 {
        Ok(())
    } else {
        Err("Ungültiger Name für den Zielordner.".to_string())
    }
}

// -- Publish ---------------------------------------------------------------

/// Example: "Invoice_protected.pdf" → "Invoice_protected (2).pdf".
fn numbered(name: &str, number: u32) -> String {
    if number <= 1 {
        return name.to_string();
    }
    match name.rfind('.').filter(|&dot| dot > 0) {
        Some(dot) => format!("{} ({number}){}", &name[..dot], &name[dot..]),
        None => format!("{name} ({number})"),
    }
}

/// Publishes a reviewed file under a free name in the target folder.
///
/// The same bytes reported by the sidecar: type, size, and SHA-256 are verified
/// before and after copying. An existing name is never overwritten; nor is a
/// file that appears between review and renaming (`persist_noclobber`).
fn publish_unique(
    staged: &Path,
    directory: &Path,
    name: &str,
    expected_hash: &str,
    expected_size: u64,
) -> Result<PathBuf, String> {
    let metadata = fs::symlink_metadata(staged)
        .map_err(|error| format!("Bereitgestellte Datei fehlt: {error}"))?;
    if !metadata.file_type().is_file() || metadata.len() != expected_size {
        return Err("Bereitgestellte Datei stimmt in Typ oder Größe nicht.".to_string());
    }
    if sha256_file(staged)? != expected_hash {
        return Err("SHA-256 der bereitgestellten Datei stimmt nicht.".to_string());
    }
    if !directory.is_dir() {
        return Err("Der Zielordner existiert nicht.".to_string());
    }
    let mut source = File::open(staged)
        .map_err(|error| format!("Bereitgestellte Datei nicht lesbar: {error}"))?;
    let mut temporary = tempfile::Builder::new()
        .prefix(".unname-")
        .tempfile_in(directory)
        .map_err(|error| format!("Im Zielordner ließ sich nichts anlegen: {error}"))?;
    let copied = io::copy(&mut source, &mut temporary)
        .map_err(|error| format!("Kopieren in den Zielordner scheiterte: {error}"))?;
    if copied != expected_size {
        return Err("Beim Kopieren kam eine andere Bytezahl an.".to_string());
    }
    temporary
        .as_file()
        .sync_all()
        .map_err(|error| format!("Die Datei ließ sich nicht dauerhaft schreiben: {error}"))?;
    if sha256_file(temporary.path())? != expected_hash {
        return Err("SHA-256 der Kopie im Zielordner stimmt nicht.".to_string());
    }
    for number in 1..=MAX_DUPLICATES {
        let candidate = directory.join(numbered(name, number));
        match temporary.persist_noclobber(&candidate) {
            Ok(_) => return Ok(candidate),
            Err(error) if error.error.kind() == io::ErrorKind::AlreadyExists => {
                temporary = error.file;
            }
            Err(error) => {
                return Err(format!(
                    "Die Datei ließ sich nicht veröffentlichen: {}",
                    error.error
                ))
            }
        }
    }
    Err("Im Zielordner gibt es zu viele gleichnamige Dateien.".to_string())
}

fn sync_directory(directory: &Path) -> Result<(), String> {
    #[cfg(unix)]
    File::open(directory)
        .and_then(|handle| handle.sync_all())
        .map_err(|error| format!("Zielordner ließ sich nicht synchronisieren: {error}"))?;
    #[cfg(not(unix))]
    let _ = directory;
    Ok(())
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let mut input =
        File::open(path).map_err(|error| format!("Datei für SHA-256 nicht lesbar: {error}"))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = input
            .read(&mut buffer)
            .map_err(|error| format!("Datei für SHA-256 nicht lesbar: {error}"))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

// -- Edit again ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct FileRef {
    pub id: String,
    pub name: String,
}

/// Re-reads the originals of expired files and replaces them in the entry.
///
/// The shell reads known originals itself. For the rest — e.g. after a restart
/// — it asks via a file dialog; the selected files are matched by name. The
/// sidecar reports whether an original has changed via the hash (`changed`).
pub async fn reload(
    app: &AppHandle,
    sidecar: &SidecarState,
    origins: &EntryOrigins,
    entry_id: &str,
    files: Vec<FileRef>,
    title: String,
) -> Result<Value, String> {
    inputs::validate_entry_id(entry_id)?;
    if files.iter().any(|file| !valid_id(&file.id)) {
        return Err("Ungültige Datei-ID.".to_string());
    }
    let connection = sidecar.upload_connection().ok_or_else(|| {
        "Die lokale Verarbeitung ist nicht bereit; neue Dokumente bleiben gesperrt.".to_string()
    })?;

    let mut found: Vec<(FileRef, Part)> = Vec::new();
    let mut missing: Vec<FileRef> = Vec::new();
    for file in files {
        let read = match origins.original(entry_id, &file.id) {
            Some(path) => read_blocking(path).await.ok(),
            None => None,
        };
        match read {
            Some(part) => found.push((file, part)),
            None => missing.push(file),
        }
    }
    if !missing.is_empty() {
        let chosen = inputs::pick(app, false, title).await?.unwrap_or_default();
        let mut unmatched = Vec::new();
        for file in missing {
            let wanted = Path::new(&file.name)
                .file_name()
                .map(|name| name.to_owned());
            let path = chosen
                .iter()
                .find(|path| path.file_name().map(|name| name.to_owned()) == wanted);
            match path {
                Some(path) => match read_blocking(path.clone()).await {
                    Ok(part) => found.push((file, part)),
                    Err(_) => unmatched.push(file),
                },
                None => unmatched.push(file),
            }
        }
        missing = unmatched;
    }

    let client = connection.client();
    let mut replaced = Vec::new();
    let mut failed: Vec<String> = Vec::new();
    let mut entry = Value::Null;
    for (file, part) in found {
        let url = format!(
            "{}/entries/{entry_id}/files/{}/replace",
            connection.base_url, file.id
        );
        match inputs::post_parts(&client, &connection, &url, std::slice::from_ref(&part)).await {
            Ok(response) => {
                let new_id = response
                    .get("file")
                    .and_then(|item| item.get("id"))
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();
                if let (true, Some(path)) = (valid_id(&new_id), &part.path) {
                    origins.replace_file(entry_id, &file.id, &new_id, path);
                }
                replaced.push(json!({
                    "file_id": file.id,
                    "new_id": new_id,
                    "changed": response.get("changed").cloned().unwrap_or(json!(false)),
                }));
                if let Some(value) = response.get("entry") {
                    entry = value.clone();
                }
            }
            Err(reason) => {
                log::warn!("Original wurde nicht neu eingelesen: {reason}");
                failed.push(file.name);
            }
        }
    }
    Ok(json!({
        "entry": entry,
        "replaced": replaced,
        "missing": missing.into_iter().map(|file| file.name).collect::<Vec<_>>(),
        "failed": failed,
    }))
}

async fn read_blocking(path: PathBuf) -> Result<Part, Value> {
    tauri::async_runtime::spawn_blocking(move || inputs::read_input(&path))
        .await
        .map_err(|_| json!({ "code": "unreadable" }))?
}

// -- Review report ------------------------------------------------------------

/// Saves text at a location chosen in the dialog: the review report
/// (content-free, counters) or a restored answer (with real names, phase G) —
/// both, because the user explicitly wants to save.
///
/// The system dialog has already confirmed any overwrite. `None` means
/// cancelled.
pub async fn save_text(
    app: &AppHandle,
    title: String,
    name: String,
    content: String,
) -> Result<Option<String>, String> {
    if content.len() > MAX_TEXT_FILE_BYTES {
        return Err("Der Text ist zu groß.".to_string());
    }
    let suggested = Path::new(&name)
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .filter(|name| plain_file_name(name))
        .unwrap_or_else(|| "Pruefbericht.txt".to_string());
    let (sender, receiver) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .set_title(title)
        .set_file_name(suggested)
        .save_file(move |path| {
            let _ = sender.send(path);
        });
    let chosen = receiver
        .await
        .map_err(|_| "Der Speicherdialog wurde unerwartet beendet.".to_string())?;
    let Some(path) = chosen.and_then(|path| path.into_path().ok()) else {
        return Ok(None);
    };
    write_atomically(&path, content.as_bytes())?;
    Ok(Some(path.to_string_lossy().into_owned()))
}

pub(crate) fn write_atomically(path: &Path, content: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .filter(|parent| parent.is_dir())
        .ok_or_else(|| "Der gewählte Ordner existiert nicht.".to_string())?;
    let mut temporary = tempfile::Builder::new()
        .prefix(".unname-")
        .tempfile_in(parent)
        .map_err(|error| format!("Die Datei ließ sich nicht anlegen: {error}"))?;
    io::Write::write_all(&mut temporary, content)
        .and_then(|()| temporary.as_file().sync_all())
        .map_err(|error| format!("Die Datei ließ sich nicht schreiben: {error}"))?;
    temporary
        .persist(path)
        .map_err(|error| format!("Die Datei ließ sich nicht speichern: {}", error.error))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stage(root: &Path, file_id: &str, name: &str, content: &[u8]) -> (PathBuf, Value) {
        let directory = root.join("presidio-sidecar-jobs-1-x").join("save-abc");
        fs::create_dir_all(directory.join(file_id)).expect("staging");
        let path = directory.join(file_id).join(name);
        fs::write(&path, content).expect("staged file");
        let response = json!({
            "staging_directory": directory.to_string_lossy(),
            "staged": [{
                "file_id": file_id,
                "name": name,
                "path": path.to_string_lossy(),
                "sha256": format!("{:x}", Sha256::digest(content)),
                "size_bytes": content.len(),
            }],
        });
        (path, response)
    }

    #[test]
    fn same_name_copies_are_numbered_not_overwritten() {
        let root = tempfile::tempdir().expect("tempdir");
        let target = root.path().join("geschützt");
        fs::create_dir(&target).expect("target");
        fs::write(target.join("Rechnung_geschützt.pdf"), b"alt").expect("existing");
        let (staged, response) = stage(root.path(), "j1", "Rechnung_geschützt.pdf", b"neu");
        let save = StagedSave::from_response(&response, root.path()).expect("staging");
        let file = &save.files[0];

        let first =
            publish_unique(&staged, &target, &file.name, &file.sha256, file.size).expect("publish");
        let second = publish_unique(&staged, &target, &file.name, &file.sha256, file.size)
            .expect("publish again");

        assert_eq!(first, target.join("Rechnung_geschützt (2).pdf"));
        assert_eq!(second, target.join("Rechnung_geschützt (3).pdf"));
        assert_eq!(
            fs::read(target.join("Rechnung_geschützt.pdf")).expect("old"),
            b"alt"
        );
        assert_eq!(fs::read(first).expect("new"), b"neu");
        // No remnants of the temporary copies in the target folder.
        let names: Vec<_> = fs::read_dir(&target)
            .expect("list")
            .map(|item| item.expect("item").file_name())
            .collect();
        assert_eq!(names.len(), 3);
    }

    #[test]
    fn wrong_hash_publishes_nothing() {
        let root = tempfile::tempdir().expect("tempdir");
        let target = root.path().join("ziel");
        fs::create_dir(&target).expect("target");
        let (staged, _) = stage(root.path(), "j1", "a.txt", b"daten");

        let error = publish_unique(&staged, &target, "a.txt", &"0".repeat(64), 5)
            .expect_err("hash mismatch");

        assert!(error.contains("SHA-256"));
        assert_eq!(fs::read_dir(&target).expect("list").count(), 0);
    }

    #[test]
    fn staging_outside_temp_area_is_rejected() {
        let root = tempfile::tempdir().expect("tempdir");
        let other = tempfile::tempdir().expect("other");
        let (_, response) = stage(other.path(), "j1", "a.txt", b"x");

        let error = StagedSave::from_response(&response, root.path()).expect_err("outside");

        assert!(error.contains("außerhalb"));
    }

    #[test]
    fn staged_file_must_be_at_agreed_location() {
        let root = tempfile::tempdir().expect("tempdir");
        let (_, mut response) = stage(root.path(), "j1", "a.txt", b"x");
        let directory = response["staging_directory"]
            .as_str()
            .expect("dir")
            .to_string();
        response["staged"][0]["path"] = json!(format!("{directory}/anders/a.txt"));
        assert!(StagedSave::from_response(&response, root.path()).is_err());

        let (_, mut response) = stage(root.path(), "j1", "a.txt", b"x");
        response["staged"][0]["name"] = json!("../a.txt");
        assert!(StagedSave::from_response(&response, root.path()).is_err());

        let (_, mut response) = stage(root.path(), "j1", "a.txt", b"x");
        response["staged"][0]["file_id"] = json!("../j1");
        assert!(StagedSave::from_response(&response, root.path()).is_err());
    }

    #[test]
    fn numbering_keeps_extension() {
        assert_eq!(numbered("Brief_geschützt.docx", 1), "Brief_geschützt.docx");
        assert_eq!(
            numbered("Brief_geschützt.docx", 2),
            "Brief_geschützt (2).docx"
        );
        assert_eq!(numbered("notiz", 3), "notiz (3)");
        assert_eq!(numbered(".profil", 2), ".profil (2)");
    }

    #[test]
    fn folder_name_is_single_path_segment() {
        assert!(validate_folder_name("geschützt").is_ok());
        assert!(validate_folder_name("protected").is_ok());
        for bad in ["", ".", "..", "../x", "a/b", "a\\b", ".versteckt", "C:"] {
            assert!(validate_folder_name(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn finder_tag_is_short_and_single_line() {
        assert!(validate_tag("geschützt").is_ok());
        assert!(validate_tag("protected").is_ok());
        for bad in ["", "a\nb", "\u{0}", &"x".repeat(65)] {
            assert!(validate_tag(bad).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn sidecar_rejection_keeps_code_and_file() {
        let body = serde_json::to_vec(&json!({
            "detail": {
                "code": "pdf_check_failed",
                "message": "Entfernter Text ist weiterhin extrahierbar.",
                "file_id": "j1"
            }
        }))
        .expect("json");

        let failure = rejection(&body, reqwest::StatusCode::SERVICE_UNAVAILABLE);

        assert_eq!(failure.code, "pdf_check_failed");
        assert_eq!(failure.file_id.as_deref(), Some("j1"));
        let plain = rejection(b"<html>", reqwest::StatusCode::BAD_GATEWAY);
        assert_eq!(plain.code, "save_failed");
        assert!(plain.message.contains("502"));
    }

    #[test]
    fn origins_remember_replace_and_forget_old_entries() {
        let origins = EntryOrigins::default();
        origins.remember_file("e1", "j1", Path::new("/Kunden/Rechnung.pdf"));
        origins.remember_file("e1", "j2", Path::new("/Kunden/Angebot.docx"));
        assert_eq!(
            origins.originals_folder("e1"),
            Some(PathBuf::from("/Kunden"))
        );

        origins.replace_file("e1", "j1", "j9", Path::new("/Kunden/Rechnung.pdf"));
        assert_eq!(origins.original("e1", "j1"), None);
        assert_eq!(
            origins.original("e1", "j9"),
            Some(PathBuf::from("/Kunden/Rechnung.pdf"))
        );
        assert_eq!(
            origins.originals_folder("e1"),
            Some(PathBuf::from("/Kunden"))
        );

        for index in 0..MAX_ENTRIES {
            origins.set_target(&format!("x{index}"), Path::new("/ziel"));
        }
        assert_eq!(origins.original("e1", "j9"), None);
    }

    #[test]
    fn deleted_entries_leave_no_paths() {
        let origins = EntryOrigins::default();
        origins.remember_file("e1", "j1", Path::new("/Kunden/Rechnung.pdf"));
        origins.set_target("e1", Path::new("/ziel"));
        origins.remember_file("e2", "j2", Path::new("/Kunden/Angebot.docx"));

        origins.forget("e1");
        assert_eq!(origins.original("e1", "j1"), None);
        assert_eq!(origins.target("e1"), None);
        assert_eq!(
            origins.original("e2", "j2"),
            Some(PathBuf::from("/Kunden/Angebot.docx"))
        );

        origins.forget_all();
        assert_eq!(origins.original("e2", "j2"), None);
    }

    #[test]
    fn review_report_is_written_atomically() {
        let root = tempfile::tempdir().expect("tempdir");
        let path = root.path().join("Prüfbericht.txt");
        fs::write(&path, b"alt").expect("existing");

        write_atomically(&path, "Prüfbericht\n".as_bytes()).expect("write");

        assert_eq!(fs::read_to_string(&path).expect("read"), "Prüfbericht\n");
        assert_eq!(fs::read_dir(root.path()).expect("list").count(), 1);
    }
}
