//! Settings files via the shell.
//!
//! Like with file inputs, the UI gets no paths and the sidecar gets no paths:
//! the shell opens the system dialog, reads or writes the file, and
//! communicates with the sidecar itself.
//!
//! - **Table backup**: `POST /vault/backup` returns the password-encrypted
//!   backup; the shell writes it atomically to the chosen location. Reading
//!   sends the selected file to `/vault/import`.
//! - **Name lists**: The shell reads CSV, JSON, or text and forwards it as
//!   multipart to `/lists`. It only keeps the path in memory for „Jetzt
//!   aktualisieren"; after a restart, the dialog asks again (like „Nochmal
//!   bearbeiten"). List paths often contain customer names and must not be
//!   stored unencrypted on disk.
//! - **Templates**: A selected YAML file is sent as text to the UI, which
//!   forwards it to `/templates/import`. Export happens via `save_text_file`.

use std::collections::HashMap;
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::UNIX_EPOCH;

use base64::Engine as _;
use serde::Serialize;
use serde_json::{json, Value};
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

use crate::inputs::{self, Part};
use crate::saving;
use crate::sidecar::{SidecarConnection, SidecarState};

/// Sidecar limit for a backup (`vault_backup.MAX_BACKUP_BYTES`).
const MAX_BACKUP_BYTES: u64 = 64 * 1024 * 1024;
/// Sidecar limit for a name list (`MAX_LIST_BYTES`).
const MAX_LIST_BYTES: u64 = 16 * 1024 * 1024;
/// Sidecar limit for a template (`templates.MAX_TEMPLATE_BYTES`).
const MAX_TEMPLATE_BYTES: u64 = 256 * 1024;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
const MAX_REMEMBERED_LISTS: usize = 32;

/// What the UI learns about an error. The UI translates `code`; `message` is
/// for the diagnostic log only.
///
/// From the sidecar: `password_too_short`, `wrong_password`, `invalid_backup`,
/// `list_format`, `list_empty`, `list_unsupported`, `list_encoding`,
/// `too_large` … From the shell: `not_ready`, `unreadable`, `file_too_large`,
/// `encoding`, `write`, `invalid`.
#[derive(Debug, Serialize, PartialEq)]
pub struct SettingsFailure {
    pub code: String,
    pub message: String,
}

impl SettingsFailure {
    fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.to_string(),
            message: message.into(),
        }
    }
}

/// Source files for the name lists in this session — memory only.
#[derive(Default)]
pub struct NameListSources {
    paths: Mutex<HashMap<String, PathBuf>>,
}

impl NameListSources {
    fn remember(&self, list_id: &str, path: &Path) {
        let mut paths = self.paths.lock().expect("Listen-Sperre vergiftet");
        if !paths.contains_key(list_id) && paths.len() >= MAX_REMEMBERED_LISTS {
            if let Some(oldest) = paths.keys().next().cloned() {
                paths.remove(&oldest);
            }
        }
        paths.insert(list_id.to_string(), path.to_path_buf());
    }

    fn get(&self, list_id: &str) -> Option<PathBuf> {
        self.paths
            .lock()
            .expect("Listen-Sperre vergiftet")
            .get(list_id)
            .cloned()
    }
}

/// A selected template file.
#[derive(Debug, Serialize, PartialEq)]
pub struct TextFile {
    pub name: String,
    pub content: String,
}

fn ready(sidecar: &SidecarState) -> Result<SidecarConnection, SettingsFailure> {
    sidecar.connection().ok_or_else(|| {
        SettingsFailure::new("not_ready", "Die lokale Verarbeitung ist nicht bereit.")
    })
}

fn valid_list_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 && id.bytes().all(|byte| byte.is_ascii_alphanumeric())
}

/// A sidecar code (`detail.code`) is preserved; otherwise `not_ready`.
fn failure_from_sidecar(reason: String) -> SettingsFailure {
    let is_code = !reason.is_empty()
        && reason.len() <= 64
        && reason
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte == b'_');
    if is_code {
        SettingsFailure::new(&reason, "")
    } else {
        SettingsFailure::new("not_ready", reason)
    }
}

fn detail_code(body: &[u8]) -> Option<String> {
    let payload: Value = serde_json::from_slice(body).ok()?;
    payload
        .get("detail")
        .and_then(|detail| detail.get("code"))
        .and_then(Value::as_str)
        .map(str::to_string)
}

/// Reads at most `limit` bytes; larger means `file_too_large`.
fn read_limited(path: &Path, limit: u64) -> Result<Vec<u8>, SettingsFailure> {
    let unreadable =
        || SettingsFailure::new("unreadable", "Die Datei konnte nicht gelesen werden.");
    let metadata = fs::metadata(path).map_err(|_| unreadable())?;
    if !metadata.is_file() {
        return Err(unreadable());
    }
    if metadata.len() > limit {
        return Err(SettingsFailure::new(
            "file_too_large",
            "Die Datei ist zu groß.",
        ));
    }
    let mut content = Vec::with_capacity(metadata.len() as usize);
    File::open(path)
        .and_then(|file| file.take(limit + 1).read_to_end(&mut content))
        .map_err(|_| unreadable())?;
    if content.len() as u64 > limit {
        return Err(SettingsFailure::new(
            "file_too_large",
            "Die Datei ist zu groß.",
        ));
    }
    Ok(content)
}

/// Modification time in milliseconds — the „Stand" of a name list.
fn modified_millis(path: &Path) -> Option<u128> {
    fs::metadata(path)
        .and_then(|metadata| metadata.modified())
        .ok()
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_millis())
}

async fn pick_file(
    app: &AppHandle,
    title: String,
    filter: &str,
    extensions: &[&str],
) -> Result<Option<PathBuf>, SettingsFailure> {
    let (sender, receiver) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .set_title(title)
        .add_filter(filter, extensions)
        .pick_file(move |path| {
            let _ = sender.send(path);
        });
    let chosen = receiver.await.map_err(|_| {
        SettingsFailure::new("unreadable", "Der Auswahldialog wurde unerwartet beendet.")
    })?;
    Ok(chosen.and_then(|path| path.into_path().ok()))
}

async fn post_json(
    connection: &SidecarConnection,
    path: &str,
    body: &Value,
) -> Result<reqwest::Response, SettingsFailure> {
    let payload = serde_json::to_vec(body)
        .map_err(|error| SettingsFailure::new("invalid", format!("Anfrage: {error}")))?;
    connection
        .client()
        .post(format!("{}{path}", connection.base_url))
        .bearer_auth(&connection.token)
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .body(payload)
        .send()
        .await
        .map_err(|error| SettingsFailure::new("not_ready", format!("nicht erreichbar: {error}")))
}

async fn read_body(response: reqwest::Response, limit: u64) -> Result<Vec<u8>, SettingsFailure> {
    let status = response.status();
    if response.content_length().unwrap_or(0) > limit {
        return Err(SettingsFailure::new("invalid", "unerwartet große Antwort"));
    }
    let bytes = response.bytes().await.map_err(|error| {
        SettingsFailure::new("invalid", format!("Antwort nicht lesbar: {error}"))
    })?;
    if bytes.len() as u64 > limit {
        return Err(SettingsFailure::new("invalid", "unerwartet große Antwort"));
    }
    if !status.is_success() {
        return Err(match detail_code(&bytes) {
            Some(code) => SettingsFailure::new(&code, ""),
            None => SettingsFailure::new("not_ready", format!("Status {status}")),
        });
    }
    Ok(bytes.to_vec())
}

/// "Export backup": choose destination, fetch backup, write atomically.
/// `None` means cancelled.
pub async fn save_backup(
    app: &AppHandle,
    sidecar: &SidecarState,
    password: String,
    title: String,
    name: String,
) -> Result<Option<String>, SettingsFailure> {
    ready(sidecar)?;
    let suggested = Path::new(&name)
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .filter(|name| saving::plain_file_name(name))
        .unwrap_or_else(|| "unname-tabelle.unname-sicherung".to_string());
    let (sender, receiver) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .set_title(title)
        .set_file_name(suggested)
        .save_file(move |path| {
            let _ = sender.send(path);
        });
    let chosen = receiver.await.map_err(|_| {
        SettingsFailure::new("write", "Der Speicherdialog wurde unerwartet beendet.")
    })?;
    let Some(target) = chosen.and_then(|path| path.into_path().ok()) else {
        return Ok(None);
    };
    // Only after the dialog: Until then, the connection might have changed.
    let connection = ready(sidecar)?;
    let response = post_json(
        &connection,
        "/vault/backup",
        &json!({ "password": password }),
    )
    .await?;
    let bytes = read_body(response, MAX_BACKUP_BYTES).await?;
    saving::write_atomically(&target, &bytes)
        .map_err(|error| SettingsFailure::new("write", error))?;
    Ok(Some(target.to_string_lossy().into_owned()))
}

/// „Importieren": Choose and read the backup. The UI had the user confirm the
/// consequences; `None` means cancelled.
pub async fn import_backup(
    app: &AppHandle,
    sidecar: &SidecarState,
    password: String,
    title: String,
    filter: String,
) -> Result<Option<Value>, SettingsFailure> {
    ready(sidecar)?;
    let Some(path) = pick_file(app, title, &filter, &["unname-sicherung", "bin"]).await? else {
        return Ok(None);
    };
    let content =
        tauri::async_runtime::spawn_blocking(move || read_limited(&path, MAX_BACKUP_BYTES))
            .await
            .map_err(|_| {
                SettingsFailure::new("unreadable", "Die Datei konnte nicht gelesen werden.")
            })??;
    let connection = ready(sidecar)?;
    let body = json!({
        "password": password,
        "backup": base64::engine::general_purpose::STANDARD.encode(content),
        "confirm": true,
    });
    let response = post_json(&connection, "/vault/import", &body).await?;
    let bytes = read_body(response, MAX_RESPONSE_BYTES as u64).await?;
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(|error| SettingsFailure::new("invalid", format!("Antwort ungültig: {error}")))
}

fn list_part(path: &Path) -> Result<Part, SettingsFailure> {
    let content = read_limited(path, MAX_LIST_BYTES)?;
    Ok(Part {
        name: path
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_else(|| "liste.csv".to_string()),
        content,
        path: Some(path.to_path_buf()),
    })
}

async fn submit_list(
    sidecar: &SidecarState,
    sources: &NameListSources,
    path: PathBuf,
    url_path: String,
    known_id: Option<String>,
) -> Result<Value, SettingsFailure> {
    let modified = modified_millis(&path);
    let reading = path.clone();
    let part = tauri::async_runtime::spawn_blocking(move || list_part(&reading))
        .await
        .map_err(|_| {
            SettingsFailure::new("unreadable", "Die Datei konnte nicht gelesen werden.")
        })??;
    let connection = ready(sidecar)?;
    let separator = if url_path.contains('?') { '&' } else { '?' };
    let url = match modified {
        Some(millis) => format!(
            "{}{url_path}{separator}modified={millis}",
            connection.base_url
        ),
        None => format!("{}{url_path}", connection.base_url),
    };
    let client = connection.client();
    let response = inputs::post_parts(&client, &connection, &url, std::slice::from_ref(&part))
        .await
        .map_err(failure_from_sidecar)?;
    let list_id = known_id.or_else(|| {
        response
            .get("created")
            .and_then(Value::as_str)
            .filter(|id| valid_list_id(id))
            .map(str::to_string)
    });
    if let Some(list_id) = list_id {
        sources.remember(&list_id, &path);
    }
    Ok(response)
}

/// "Add list": `kind` is `persons` or `organizations`.
pub async fn add_list(
    app: &AppHandle,
    sidecar: &SidecarState,
    sources: &NameListSources,
    kind: String,
    title: String,
    filter: String,
) -> Result<Option<Value>, SettingsFailure> {
    if kind != "persons" && kind != "organizations" {
        return Err(SettingsFailure::new("invalid", "Unbekannte Art der Liste."));
    }
    ready(sidecar)?;
    let Some(path) = pick_file(app, title, &filter, &["csv", "json", "txt"]).await? else {
        return Ok(None);
    };
    submit_list(sidecar, sources, path, format!("/lists?kind={kind}"), None)
        .await
        .map(Some)
}

/// „Jetzt aktualisieren": re-read the remembered source file, or choose it in
/// the dialog if the shell no longer knows it.
pub async fn refresh_list(
    app: &AppHandle,
    sidecar: &SidecarState,
    sources: &NameListSources,
    list_id: String,
    title: String,
    filter: String,
) -> Result<Option<Value>, SettingsFailure> {
    if !valid_list_id(&list_id) {
        return Err(SettingsFailure::new("invalid", "Ungültige Kennung."));
    }
    ready(sidecar)?;
    let known = sources.get(&list_id).filter(|path| path.is_file());
    let path = match known {
        Some(path) => path,
        None => match pick_file(app, title, &filter, &["csv", "json", "txt"]).await? {
            Some(path) => path,
            None => return Ok(None),
        },
    };
    submit_list(
        sidecar,
        sources,
        path,
        format!("/lists/{list_id}/refresh"),
        Some(list_id),
    )
    .await
    .map(Some)
}

/// Choose a template and read it as text (UTF-8, at most 256 KiB).
pub async fn read_template(
    app: &AppHandle,
    title: String,
    filter: String,
) -> Result<Option<TextFile>, SettingsFailure> {
    let Some(path) = pick_file(app, title, &filter, &["yaml", "yml"]).await? else {
        return Ok(None);
    };
    let reading = path.clone();
    let content =
        tauri::async_runtime::spawn_blocking(move || read_limited(&reading, MAX_TEMPLATE_BYTES))
            .await
            .map_err(|_| {
                SettingsFailure::new("unreadable", "Die Datei konnte nicht gelesen werden.")
            })??;
    text_file(&path, content).map(Some)
}

fn text_file(path: &Path, content: Vec<u8>) -> Result<TextFile, SettingsFailure> {
    let content = content
        .strip_prefix(b"\xEF\xBB\xBF".as_slice())
        .map(<[u8]>::to_vec)
        .unwrap_or(content);
    let content = String::from_utf8(content)
        .map_err(|_| SettingsFailure::new("encoding", "Die Datei ist kein UTF-8-Text."))?;
    Ok(TextFile {
        name: path
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_default(),
        content,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn large_files_are_rejected_before_reading() {
        let root = tempfile::tempdir().expect("tempdir");
        let path = root.path().join("sicherung.unname-sicherung");
        fs::write(&path, vec![0u8; 2048]).expect("datei");
        assert_eq!(read_limited(&path, 4096).expect("lesbar").len(), 2048);
        assert_eq!(
            read_limited(&path, 1024).unwrap_err().code,
            "file_too_large".to_string()
        );
        assert_eq!(
            read_limited(root.path(), 1024).unwrap_err().code,
            "unreadable".to_string()
        );
    }

    #[test]
    fn template_without_bom_and_only_utf8() {
        let path = Path::new("/irgendwo/Team.unname-vorlage.yaml");
        let file = text_file(path, b"\xEF\xBB\xBFformat: unname-vorlage\n".to_vec()).expect("text");
        assert_eq!(file.name, "Team.unname-vorlage.yaml");
        assert_eq!(file.content, "format: unname-vorlage\n");
        assert_eq!(
            text_file(path, vec![0xff, 0xfe, 0x00]).unwrap_err().code,
            "encoding".to_string()
        );
    }

    #[test]
    fn list_paths_stay_bounded_and_overwritable() {
        let sources = NameListSources::default();
        for index in 0..(MAX_REMEMBERED_LISTS + 5) {
            sources.remember(&format!("liste{index}"), Path::new("/a/kunden.csv"));
        }
        assert_eq!(
            sources.paths.lock().expect("sperre").len(),
            MAX_REMEMBERED_LISTS
        );
        sources.remember("neu", Path::new("/a/alt.csv"));
        sources.remember("neu", Path::new("/a/neu.csv"));
        assert_eq!(sources.get("neu"), Some(PathBuf::from("/a/neu.csv")));
    }

    #[test]
    fn only_simple_ids_and_sidecar_codes() {
        assert!(valid_list_id("a1b2c3d4e5f60718"));
        assert!(!valid_list_id("../etc"));
        assert!(!valid_list_id(""));
        assert_eq!(failure_from_sidecar("list_empty".into()).code, "list_empty");
        assert_eq!(
            failure_from_sidecar("nicht erreichbar: x".into()).code,
            "not_ready"
        );
        assert_eq!(
            detail_code(br#"{"detail":{"code":"wrong_password","message":"x"}}"#),
            Some("wrong_password".to_string())
        );
        assert_eq!(detail_code(br#"{"detail":"Text"}"#), None);
    }
}
