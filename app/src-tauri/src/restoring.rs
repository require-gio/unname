//! Restoring files via the shell (spec §6).
//!
//! A file from the AI arrives only via dialog or dropzone. The shell reads it
//! and passes the bytes to the sidecar (`POST /restore/file`). The sidecar
//! keeps the restored file only in memory — it contains real names.
//!
//! "Save as…" prompts for the target, fetches the bytes (`GET
//! /restore/file/{id}`), verifies size and SHA-256, and writes atomically. Never
//! overwrite the AI's file: the target must differ from the source ("new file
//! instead of overwriting"). "Open" launches only files that this shell saved
//! in the current session — a path known to the UI, otherwise no program starts.
//!
//! If the target is in iCloud Drive or a File Provider, the shell writes only
//! after confirmation: the file contains clear names and would otherwise be
//! unnoticed. It remembers the chosen target and reports `cloud_target`; the UI
//! confirms, the second call writes there without asking again.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

use crate::finder;
use crate::inputs::{self, InputSelections};
use crate::saving;
use crate::sidecar::SidecarState;

/// How long the sidecar keeps a restored file (`RestoredFileStore`).
const TTL: Duration = Duration::from_secs(30 * 60);
const MAX_ITEMS: usize = 8;
const MAX_SAVED: usize = 32;
/// Slightly more than the input limit: names are longer than their codes.
const MAX_FILE_BYTES: u64 = 64 * 1024 * 1024;

/// What the UI learns on error. `code` is translated by the UI; `message` is
/// for the diagnostic log only.
///
/// Codes from the sidecar: `unsupported_format`, `unsupported_content`,
/// `invalid_file`, `encoding`, `empty`, `too_large`. From the shell:
/// `not_ready`, `no_file`, `unreadable`, `file_too_large`, `expired`,
/// `same_as_source`, `verify`, `write`, `invalid`, `cloud_not_downloaded` and
/// `cloud_target` (target is syncing, confirmation needed).
#[derive(Debug, Serialize, PartialEq)]
pub struct RestoreFailure {
    pub code: String,
    pub message: String,
}

impl RestoreFailure {
    fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.to_string(),
            message: message.into(),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
struct Restored {
    /// The AI file — only to ensure it is never overwritten and to open the
    /// dialog in its folder.
    source: Option<PathBuf>,
    name: String,
    sha256: String,
    size: u64,
    created: Instant,
}

/// What the shell knows about restored files — in memory only.
#[derive(Default)]
pub struct RestoredFiles {
    items: Mutex<HashMap<String, Restored>>,
    saved: Mutex<Vec<PathBuf>>,
    /// The cloud target selected in the dialog, awaiting confirmation:
    /// `(Kennung, Ziel)`. At most one; a new dialog replaces it.
    cloud_target: Mutex<Option<(String, PathBuf)>>,
}

impl RestoredFiles {
    fn remember(&self, id: String, restored: Restored) {
        let mut items = self
            .items
            .lock()
            .expect("Rückübersetzungs-Sperre vergiftet");
        items.retain(|_, item| item.created.elapsed() < TTL);
        while items.len() >= MAX_ITEMS {
            let oldest = items
                .iter()
                .min_by_key(|(_, item)| item.created)
                .map(|(id, _)| id.clone());
            match oldest {
                Some(oldest) => items.remove(&oldest),
                None => break,
            };
        }
        items.insert(id, restored);
    }

    fn get(&self, id: &str) -> Option<Restored> {
        let items = self
            .items
            .lock()
            .expect("Rückübersetzungs-Sperre vergiftet");
        items
            .get(id)
            .filter(|item| item.created.elapsed() < TTL)
            .cloned()
    }

    fn remember_saved(&self, path: &Path) {
        let mut saved = self
            .saved
            .lock()
            .expect("Rückübersetzungs-Sperre vergiftet");
        saved.retain(|known| known != path);
        if saved.len() >= MAX_SAVED {
            saved.remove(0);
        }
        saved.push(path.to_path_buf());
    }

    fn hold_cloud_target(&self, id: &str, target: PathBuf) {
        *self
            .cloud_target
            .lock()
            .expect("Rückübersetzungs-Sperre vergiftet") = Some((id.to_string(), target));
    }

    fn take_cloud_target(&self, id: &str) -> Option<PathBuf> {
        let mut held = self
            .cloud_target
            .lock()
            .expect("Rückübersetzungs-Sperre vergiftet");
        match held.take() {
            Some((held_id, target)) if held_id == id => Some(target),
            _ => None,
        }
    }

    fn was_saved(&self, path: &Path) -> bool {
        self.saved
            .lock()
            .expect("Rückübersetzungs-Sperre vergiftet")
            .iter()
            .any(|known| known == path)
    }
}
/// `(id, name, sha256, size)` from the sidecar response's `output`; `None` if
/// no code was replaced or the details are incorrect.
fn output_of(response: &Value) -> Option<(String, String, String, u64)> {
    let output = response.get("output").filter(|output| output.is_object())?;
    let text = |key: &str| output.get(key).and_then(Value::as_str).map(str::to_string);
    let id = text("id").filter(|id| valid_id(id))?;
    let name = text("name").filter(|name| saving::plain_file_name(name))?;
    let sha256 = text("sha256")
        .map(|hash| hash.to_ascii_lowercase())
        .filter(|hash| hash.len() == 64 && hash.bytes().all(|byte| byte.is_ascii_hexdigit()))?;
    let size = output.get("size_bytes").and_then(Value::as_u64)?;
    Some((id, name, sha256, size))
}

fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 && id.bytes().all(|byte| byte.is_ascii_alphanumeric())
}

/// A sidecar code (`detail.code`) is preserved; everything else is not passed.
fn failure_from_sidecar(reason: String) -> RestoreFailure {
    let is_code = !reason.is_empty()
        && reason.len() <= 64
        && reason
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte == b'_');
    if is_code {
        RestoreFailure::new(&reason, "")
    } else {
        RestoreFailure::new("not_ready", reason)
    }
}

/// Reads the first file of a selection and restores it.
///
/// The result is the sidecar's response (`source`, `output`, `preview`,
/// `counts` …) plus `skipped`: how many other files in the selection were not
/// included — one file is restored at a time.
pub async fn restore(
    sidecar: &SidecarState,
    selections: &InputSelections,
    restored: &RestoredFiles,
    selection: &str,
    entry_id: Option<&str>,
    surrogates: bool,
) -> Result<Value, RestoreFailure> {
    if let Some(entry_id) = entry_id {
        inputs::validate_entry_id(entry_id)
            .map_err(|error| RestoreFailure::new("invalid", error))?;
    }
    let connection = sidecar.upload_connection().ok_or_else(|| {
        RestoreFailure::new("not_ready", "Die lokale Verarbeitung ist nicht bereit.")
    })?;
    let files = selections
        .take(selection)
        .ok_or_else(|| RestoreFailure::new("expired", "Die Auswahl ist nicht mehr gültig."))?;
    let skipped = files.len().saturating_sub(1);
    let path = files
        .into_iter()
        .next()
        .ok_or_else(|| RestoreFailure::new("no_file", "In der Auswahl war keine Datei."))?;
    let part = tauri::async_runtime::spawn_blocking(move || inputs::read_input(&path))
        .await
        .map_err(|_| RestoreFailure::new("unreadable", "Die Datei konnte nicht gelesen werden."))?
        .map_err(|rejection| {
            let code = rejection
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("unreadable");
            RestoreFailure::new(code, "")
        })?;

    let mut url = format!(
        "{}/restore/file?surrogates={surrogates}",
        connection.base_url
    );
    if let Some(entry_id) = entry_id {
        url.push_str("&entry_id=");
        url.push_str(entry_id);
    }
    let client = connection.client();
    let mut response = inputs::post_parts(&client, &connection, &url, std::slice::from_ref(&part))
        .await
        .map_err(failure_from_sidecar)?;
    if let Some((id, name, sha256, size)) = output_of(&response) {
        restored.remember(
            id,
            Restored {
                source: part.path.clone(),
                name,
                sha256,
                size,
                created: Instant::now(),
            },
        );
    }
    if let Value::Object(object) = &mut response {
        object.insert("skipped".to_string(), Value::from(skipped));
    }
    Ok(response)
}

/// „Speichern unter …": choose target, fetch bytes, check, write atomically.
/// `None` means cancelled.
///
/// With `confirm_cloud`, no dialog asks: writing goes to the cloud target that
/// the previous call held in `cloud_target`.
pub async fn save(
    app: &AppHandle,
    sidecar: &SidecarState,
    restored: &RestoredFiles,
    id: &str,
    title: String,
    confirm_cloud: bool,
) -> Result<Option<String>, RestoreFailure> {
    if !valid_id(id) {
        return Err(RestoreFailure::new("invalid", "Ungültige Kennung."));
    }
    let item = restored.get(id).ok_or_else(|| {
        RestoreFailure::new("expired", "Die zurückübersetzte Datei ist nicht mehr da.")
    })?;
    let connection = sidecar.connection().ok_or_else(|| {
        RestoreFailure::new("not_ready", "Die lokale Verarbeitung ist nicht bereit.")
    })?;

    let target = if confirm_cloud {
        restored.take_cloud_target(id).ok_or_else(|| {
            RestoreFailure::new("expired", "Das bestätigte Ziel ist nicht mehr bekannt.")
        })?
    } else {
        let Some(target) = choose_target(app, &item, title).await? else {
            return Ok(None);
        };
        if target.parent().is_some_and(finder::is_cloud_location) {
            restored.hold_cloud_target(id, target);
            return Err(RestoreFailure::new(
                "cloud_target",
                "Das Ziel wird synchronisiert; Bestätigung nötig.",
            ));
        }
        target
    };

    let client = connection.client();
    let response = client
        .get(format!("{}/restore/file/{id}", connection.base_url))
        .bearer_auth(&connection.token)
        .send()
        .await
        .map_err(|error| RestoreFailure::new("not_ready", format!("nicht erreichbar: {error}")))?;
    if response.status() == reqwest::StatusCode::NOT_FOUND {
        return Err(RestoreFailure::new(
            "expired",
            "Die Datei ist nicht mehr da.",
        ));
    }
    if !response.status().is_success() {
        return Err(RestoreFailure::new(
            "verify",
            format!("Status {}", response.status()),
        ));
    }
    if response.content_length().unwrap_or(0) > MAX_FILE_BYTES {
        return Err(RestoreFailure::new("verify", "unerwartet große Antwort"));
    }
    let bytes = response
        .bytes()
        .await
        .map_err(|error| RestoreFailure::new("verify", format!("Antwort nicht lesbar: {error}")))?;
    verify(&bytes, item.size, &item.sha256)?;
    saving::write_atomically(&target, &bytes)
        .map_err(|error| RestoreFailure::new("write", error))?;
    restored.remember_saved(&target);
    Ok(Some(target.to_string_lossy().into_owned()))
}

/// The target in the save dialog, next to the AI file. `None` means cancelled.
async fn choose_target(
    app: &AppHandle,
    item: &Restored,
    title: String,
) -> Result<Option<PathBuf>, RestoreFailure> {
    let (sender, receiver) = tokio::sync::oneshot::channel();
    let mut dialog = app
        .dialog()
        .file()
        .set_title(title)
        .set_file_name(&item.name);
    if let Some(folder) = item
        .source
        .as_deref()
        .and_then(Path::parent)
        .filter(|folder| folder.is_dir())
    {
        dialog = dialog.set_directory(folder);
    }
    dialog.save_file(move |path| {
        let _ = sender.send(path);
    });
    let chosen = receiver.await.map_err(|_| {
        RestoreFailure::new("write", "Der Speicherdialog wurde unerwartet beendet.")
    })?;
    let Some(target) = chosen.and_then(|path| path.into_path().ok()) else {
        return Ok(None);
    };
    if let Some(source) = &item.source {
        if same_target(&target, source) {
            return Err(RestoreFailure::new(
                "same_as_source",
                "Die Datei der KI wird nicht überschrieben.",
            ));
        }
    }
    Ok(Some(target))
}

fn verify(bytes: &[u8], size: u64, sha256: &str) -> Result<(), RestoreFailure> {
    if bytes.len() as u64 != size {
        return Err(RestoreFailure::new("verify", "Die Größe stimmt nicht."));
    }
    if format!("{:x}", Sha256::digest(bytes)) != sha256 {
        return Err(RestoreFailure::new("verify", "Der SHA-256 stimmt nicht."));
    }
    Ok(())
}

/// Returns true if the chosen target points to the source, even if reached via
/// a different path.
fn same_target(target: &Path, source: &Path) -> bool {
    let resolve = |path: &Path| -> Option<PathBuf> {
        if let Ok(resolved) = std::fs::canonicalize(path) {
            return Some(resolved);
        }
        let parent = std::fs::canonicalize(path.parent()?).ok()?;
        Some(parent.join(path.file_name()?))
    };
    match (resolve(target), resolve(source)) {
        (Some(target), Some(source)) => target == source,
        _ => target == source,
    }
}

/// "Open": a file that this shell previously saved, in the responsible
/// program.
pub fn open(app: &AppHandle, restored: &RestoredFiles, path: &str) -> Result<(), String> {
    let path = PathBuf::from(path);
    if !restored.was_saved(&path) || !path.is_file() {
        return Err("Geöffnet wird nur eine gerade gespeicherte Datei.".to_string());
    }
    app.opener()
        .open_path(path.to_string_lossy().into_owned(), None::<&str>)
        .map_err(|error| format!("Die Datei ließ sich nicht öffnen: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs;

    fn sha(content: &[u8]) -> String {
        format!("{:x}", Sha256::digest(content))
    }

    #[test]
    fn output_is_accepted_only_with_valid_details() {
        let good = json!({ "output": {
            "id": "a1b2c3", "name": "Brief_mit_Namen.docx",
            "sha256": "A".repeat(64), "size_bytes": 12,
        }});
        assert_eq!(
            output_of(&good),
            Some((
                "a1b2c3".to_string(),
                "Brief_mit_Namen.docx".to_string(),
                "a".repeat(64),
                12
            ))
        );
        assert_eq!(output_of(&json!({ "output": null })), None);
        for bad in [
            json!({ "output": { "id": "../x", "name": "a.txt", "sha256": "a".repeat(64), "size_bytes": 1 }}),
            json!({ "output": { "id": "a1", "name": "../a.txt", "sha256": "a".repeat(64), "size_bytes": 1 }}),
            json!({ "output": { "id": "a1", "name": "a.txt", "sha256": "xyz", "size_bytes": 1 }}),
            json!({ "output": { "id": "a1", "name": "a.txt", "sha256": "a".repeat(64) }}),
        ] {
            assert_eq!(output_of(&bad), None, "{bad}");
        }
    }

    #[test]
    fn wrong_size_or_checksum_is_not_written() {
        let content = b"Sehr geehrte Frau Hofer";
        assert!(verify(content, content.len() as u64, &sha(content)).is_ok());
        assert_eq!(
            verify(content, 3, &sha(content)).unwrap_err().code,
            "verify"
        );
        assert_eq!(
            verify(content, content.len() as u64, &"0".repeat(64))
                .unwrap_err()
                .code,
            "verify"
        );
    }

    #[test]
    fn cloud_target_applies_only_to_its_file_and_only_once() {
        let files = RestoredFiles::default();
        let target = PathBuf::from("/Users/a/Library/Mobile Documents/x.docx");
        files.hold_cloud_target("abc", target.clone());
        assert_eq!(files.take_cloud_target("andere"), None);
        files.hold_cloud_target("abc", target.clone());
        assert_eq!(files.take_cloud_target("abc"), Some(target));
        assert_eq!(files.take_cloud_target("abc"), None);
    }

    #[test]
    fn ai_file_is_never_the_target() {
        let root = tempfile::tempdir().expect("tempdir");
        let source = root.path().join("Antwort.docx");
        fs::write(&source, b"x").expect("source");
        fs::create_dir(root.path().join("sub")).expect("sub");

        assert!(same_target(&source, &source));
        assert!(same_target(
            &root.path().join("sub/../Antwort.docx"),
            &source
        ));
        assert!(!same_target(
            &root.path().join("Antwort_mit_Namen.docx"),
            &source
        ));
    }

    #[test]
    fn only_saved_files_are_opened() {
        let restored = RestoredFiles::default();
        let saved = PathBuf::from("/Users/test/Brief_mit_Namen.docx");
        assert!(!restored.was_saved(&saved));
        restored.remember_saved(&saved);
        assert!(restored.was_saved(&saved));
        assert!(!restored.was_saved(Path::new("/Applications/Rechner.app")));
        for index in 0..MAX_SAVED {
            restored.remember_saved(Path::new(&format!("/x/{index}.txt")));
        }
        assert!(!restored.was_saved(&saved));
    }

    #[test]
    fn keeps_at_most_newest_restorations() {
        let restored = RestoredFiles::default();
        let item = |name: &str| Restored {
            source: None,
            name: name.to_string(),
            sha256: "a".repeat(64),
            size: 1,
            created: Instant::now(),
        };
        for index in 0..MAX_ITEMS + 1 {
            restored.remember(format!("id{index}"), item(&format!("{index}.txt")));
        }
        assert!(restored.get("id0").is_none());
        assert_eq!(
            restored
                .get(&format!("id{MAX_ITEMS}"))
                .map(|item| item.name),
            Some(format!("{MAX_ITEMS}.txt"))
        );
    }

    #[test]
    fn sidecar_codes_are_kept() {
        assert_eq!(
            failure_from_sidecar("unsupported_format".to_string()).code,
            "unsupported_format"
        );
        let other = failure_from_sidecar("nicht erreichbar: connection refused".to_string());
        assert_eq!(other.code, "not_ready");
        assert!(other.message.contains("connection refused"));
    }
}
