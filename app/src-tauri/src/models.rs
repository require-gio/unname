//! Load and verify models.
//!
//! The detection models are too large for the app download. The shell loads
//! them once – only on click, only over HTTPS, only to the hosts of the
//! selected source – and verifies each file against `models.lock.json`. This
//! manifest is part of the signed app via `include_str!`: anything not matching
//! size and SHA-256 in it is not installed, and a mismatched file is discarded
//! (Fail-Closed). Outside a download, there is no network traffic; the HTTP
//! client lives only as long as the download.
//!
//! Storage under `<app-data>/models`:
//!
//! - `<bestandteil>/` – installed. `.installed.json` is created last, and the
//!   folder is renamed as a whole from `.staging/`. The sidecar
//!   (`--models-dir`) accepts only folders with this marker.
//! - `.partial/<bestandteil>/` – started downloads; a new attempt continues
//!   with `Range`.
//! - `.import/<bestandteil>/` – extracted from a model package, until
//!   everything is verified.
//! - `source.json` – selected source (Hugging Face, GitHub, own server, local
//!   file).
//!
//! A language model comes as a Wheel (Zip). Only the data folder is extracted,
//! and only the files that are individually hashed in the manifest – Python
//! code from the Wheel never lands on disk.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use reqwest::Url;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// The manifest of the signed app.
const MANIFEST_JSON: &str = include_str!("../models.lock.json");
/// Event for progress, completion, and errors.
pub const MODELS_EVENT: &str = "models://progress";

const INSTALL_MARKER: &str = ".installed.json";
const PARTIAL_DIR: &str = ".partial";
const IMPORT_DIR: &str = ".import";
const STAGING_DIR: &str = ".staging";
const SOURCE_FILE: &str = "source.json";
/// Never installed: executable code and Pickle.
const FORBIDDEN_SUFFIXES: [&str; 10] = [
    ".py", ".pyc", ".so", ".dylib", ".dll", ".exe", ".pkl", ".pickle", ".pt", ".pth",
];
/// `classifier` is the second opinion: optional, the sidecar finds it in the
/// next job without restart.
const KINDS: [&str; 4] = ["ner", "language", "ocr", "classifier"];
/// Platforms a component may specify (`platforms`).
const PLATFORMS: [&str; 3] = ["windows", "macos", "linux"];
/// The platform of this shell, as `platforms` names it.
pub const CURRENT_PLATFORM: &str = if cfg!(target_os = "windows") {
    "windows"
} else if cfg!(target_os = "macos") {
    "macos"
} else {
    "linux"
};
const MAX_REDIRECTS: usize = 5;
/// At most one progress event per second.
const PROGRESS_INTERVAL: Duration = Duration::from_millis(200);

// ---------------------------------------------------------------- Manifest

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub schema: u32,
    pub revision: String,
    pub sources: BTreeMap<String, SourceDef>,
    pub components: Vec<ComponentDef>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SourceDef {
    /// Allowed hosts, including for redirects. `*.beispiel.de` allows
    /// subdomains, not `beispiel.de` itself.
    pub hosts: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ComponentDef {
    pub id: String,
    /// `ner` (Named Entity Recognition), `language` (Language), `ocr` or
    /// `classifier` (second opinion).
    pub kind: String,
    pub origin: String,
    pub license: String,
    pub files: Vec<FileDef>,
    /// Only on these platforms; everywhere if unspecified. Since `ocr`
    /// (tessdata) applies only to Windows – on the Mac, Apple Vision, which is
    /// part of the system, performs recognition.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub platforms: Option<Vec<String>>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct FileDef {
    pub path: String,
    pub size: u64,
    pub sha256: String,
    pub urls: BTreeMap<String, String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unpack: Option<Unpack>,
}

/// Zip container (Wheel): only `files` under `prefix` are extracted.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Unpack {
    pub prefix: String,
    pub files: Vec<PlainFile>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PlainFile {
    pub path: String,
    pub size: u64,
    pub sha256: String,
}

impl ComponentDef {
    /// The total download size in bytes.
    pub fn download_size(&self) -> u64 {
        self.files.iter().map(|file| file.size).sum()
    }

    /// The total size in bytes after installation on disk.
    pub fn installed_size(&self) -> u64 {
        self.files
            .iter()
            .map(|file| match &file.unpack {
                Some(unpack) => unpack.files.iter().map(|inner| inner.size).sum(),
                None => file.size,
            })
            .sum()
    }

    /// Whether this component applies to the given `platform`.
    pub fn on_platform(&self, platform: &str) -> bool {
        self.platforms.as_ref().map_or(true, |platforms| {
            platforms.iter().any(|item| item == platform)
        })
    }

    /// Whether the sidecar needs a restart to load the updated configuration.
    pub fn needs_sidecar_restart(&self) -> bool {
        self.kind == "ner" || self.kind == "language"
    }

    /// The files as they are installed on disk.
    fn installed_files(&self) -> Vec<PlainFile> {
        self.files
            .iter()
            .flat_map(|file| match &file.unpack {
                Some(unpack) => unpack.files.clone(),
                None => vec![PlainFile {
                    path: file.path.clone(),
                    size: file.size,
                    sha256: file.sha256.clone(),
                }],
            })
            .collect()
    }
}

impl Manifest {
    pub fn bundled() -> Result<Self, String> {
        Self::parse(MANIFEST_JSON)
    }

    /// The app manifest filtered for the current platform, excluding components
    /// for other platforms. This ensures they are missing in the overview,
    /// selection, and when importing a model package.
    pub fn for_this_platform() -> Result<Self, String> {
        Ok(Self::bundled()?.for_platform(CURRENT_PLATFORM))
    }

    pub fn for_platform(mut self, platform: &str) -> Self {
        self.components
            .retain(|component| component.on_platform(platform));
        self
    }

    pub fn parse(json: &str) -> Result<Self, String> {
        Self::parse_checked(json, false)
    }

    /// Same as `parse`, but uses `http://127.0.0.1` for download tests.
    #[cfg(test)]
    fn parse_local(json: &str) -> Result<Self, String> {
        Self::parse_checked(json, true)
    }

    fn parse_checked(json: &str, allow_http: bool) -> Result<Self, String> {
        let manifest: Manifest = serde_json::from_str(json)
            .map_err(|error| format!("Modell-Manifest nicht lesbar: {error}"))?;
        manifest.validate(allow_http)?;
        Ok(manifest)
    }

    pub fn component(&self, id: &str) -> Option<&ComponentDef> {
        self.components.iter().find(|component| component.id == id)
    }

    fn validate(&self, allow_http: bool) -> Result<(), String> {
        if self.schema != 1 {
            return Err("Modell-Manifest mit unbekanntem Schema.".into());
        }
        if self.revision.trim().is_empty() || self.components.is_empty() {
            return Err("Modell-Manifest ohne Revision oder Bestandteile.".into());
        }
        for (name, source) in &self.sources {
            if !valid_id(name) || source.hosts.is_empty() {
                return Err(format!("Quelle {name} ohne Hosts."));
            }
            if source.hosts.iter().any(|host| !valid_host_pattern(host)) {
                return Err(format!("Quelle {name} mit ungültigem Host."));
            }
        }
        let mut ids = BTreeSet::new();
        for component in &self.components {
            if !valid_id(&component.id) || !ids.insert(component.id.as_str()) {
                return Err(format!("Ungültiger Bestandteil {}.", component.id));
            }
            if !KINDS.contains(&component.kind.as_str()) || component.files.is_empty() {
                return Err(format!(
                    "Bestandteil {} ohne Art oder Dateien.",
                    component.id
                ));
            }
            if let Some(platforms) = &component.platforms {
                if platforms.is_empty()
                    || platforms
                        .iter()
                        .any(|item| !PLATFORMS.contains(&item.as_str()))
                {
                    return Err(format!(
                        "Bestandteil {} mit ungültiger Plattform.",
                        component.id
                    ));
                }
            }
            let mut paths = BTreeSet::new();
            for file in &component.files {
                check_plain(&file.path, file.size, &file.sha256)?;
                if !paths.insert(file.path.as_str()) {
                    return Err(format!("Doppelte Datei {}.", file.path));
                }
                for (source, url) in &file.urls {
                    let hosts = self
                        .sources
                        .get(source)
                        .ok_or_else(|| format!("Unbekannte Quelle {source}."))?;
                    check_url(url, &hosts.hosts, allow_http)
                        .map_err(|_| format!("Adresse für {} nicht erlaubt.", file.path))?;
                }
                if let Some(unpack) = &file.unpack {
                    if unpack.files.is_empty() || !unpack.prefix.ends_with('/') {
                        return Err(format!("Entpacken von {} ohne Inhalt.", file.path));
                    }
                    let mut inner = BTreeSet::new();
                    for entry in &unpack.files {
                        check_plain(&entry.path, entry.size, &entry.sha256)?;
                        if !inner.insert(entry.path.as_str()) {
                            return Err(format!("Doppelte Datei {}.", entry.path));
                        }
                    }
                }
            }
        }
        if !self.components.iter().any(|item| item.kind == "ner")
            || !self.components.iter().any(|item| item.kind == "language")
        {
            return Err("Modell-Manifest ohne Namenserkennung oder Sprache.".into());
        }
        Ok(())
    }
}

fn check_plain(path: &str, size: u64, sha256: &str) -> Result<(), String> {
    if !safe_relative_path(path) {
        return Err(format!("Unzulässiger Pfad {path:?}."));
    }
    if size == 0 || !valid_sha256(sha256) {
        return Err(format!("Größe oder SHA-256 für {path} fehlt."));
    }
    Ok(())
}

fn valid_id(id: &str) -> bool {
    let mut bytes = id.bytes();
    matches!(bytes.next(), Some(b'a'..=b'z'))
        && id.len() <= 32
        && bytes.all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_host_pattern(pattern: &str) -> bool {
    let host = pattern.strip_prefix("*.").unwrap_or(pattern);
    !host.is_empty()
        && host.contains('.')
        && host
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || b".-".contains(&byte))
}

/// Relative path without directory traversal, hidden parts, or executable
/// formats.
fn safe_relative_path(path: &str) -> bool {
    if path.is_empty() || path.len() > 200 || path.starts_with('/') {
        return false;
    }
    let lower = path.to_ascii_lowercase();
    if FORBIDDEN_SUFFIXES
        .iter()
        .any(|suffix| lower.ends_with(suffix))
    {
        return false;
    }
    path.split('/').all(|segment| {
        !segment.is_empty()
            && !segment.starts_with('.')
            && segment
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
    })
}

fn relative(root: &Path, path: &str) -> PathBuf {
    path.split('/')
        .fold(root.to_path_buf(), |joined, part| joined.join(part))
}

pub fn host_allowed(hosts: &[String], host: &str) -> bool {
    let host = host.to_ascii_lowercase();
    hosts
        .iter()
        .any(|pattern| match pattern.strip_prefix("*.") {
            Some(suffix) => host.ends_with(&format!(".{suffix}")),
            None => host == *pattern,
        })
}

/// HTTPS and allowed host; `allow_http` is only for tests against 127.0.0.1.
fn check_url(raw: &str, hosts: &[String], allow_http: bool) -> Result<Url, ModelFailure> {
    let url = Url::parse(raw).map_err(|_| failure("host_not_allowed", "Ungültige Adresse."))?;
    let scheme_ok = url.scheme() == "https" || (allow_http && url.scheme() == "http");
    let host_ok = url.host_str().is_some_and(|host| host_allowed(hosts, host));
    if !scheme_ok || !host_ok || !url.username().is_empty() || url.password().is_some() {
        return Err(failure(
            "host_not_allowed",
            format!("Adresse nicht erlaubt: {}", url.host_str().unwrap_or("")),
        ));
    }
    Ok(url)
}

// ---------------------------------------------------------------- Errors ///
// What the UI learns about an error; `code` translates it. /// /// `busy`,
// `unknown_component`, `source_file_only`, `invalid_custom_url`, ///
// `host_not_allowed`, `network`, `http_status`, `size_mismatch`, ///
// `hash_mismatch`, `cancelled`, `io`, `invalid_package`,

/// What the UI experiences on an error; `code` translates it.
///
/// `busy`, `unknown_component`, `source_file_only`, `invalid_custom_url`,
/// `host_not_allowed`, `network`, `http_status`, `size_mismatch`,
/// `hash_mismatch`, `cancelled`, `io`, `invalid_package`, `package_incomplete`,
/// `package_empty`, `not_installed`.
#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct ModelFailure {
    pub code: String,
    pub message: String,
}

pub(crate) fn failure(code: &str, message: impl Into<String>) -> ModelFailure {
    ModelFailure {
        code: code.to_string(),
        message: message.into(),
    }
}

fn io_failure(error: io::Error) -> ModelFailure {
    failure("io", error.to_string())
}

// ---------------------------------------------------------------- Source ///
// Chosen source: `huggingface`, `github`, `custom` or `file`.
// #[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
// pub struct SourceChoice { pub source: String,
//     #[serde(default)]

/// Selected source: `huggingface`, `github`, `custom`, or `file`.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct SourceChoice {
    pub source: String,
    #[serde(default)]
    pub custom_url: Option<String>,
}

impl Default for SourceChoice {
    fn default() -> Self {
        Self {
            source: "huggingface".into(),
            custom_url: None,
        }
    }
}

/// Where a download connection is allowed to go.
#[derive(Clone, Debug)]
pub struct DownloadPolicy {
    source: String,
    hosts: Vec<String>,
    /// Basis des eigenen Servers: `<basis>/<bestandteil>/<datei>`.
    custom_base: Option<Url>,
    allow_http: bool,
}

impl DownloadPolicy {
    pub fn for_choice(manifest: &Manifest, choice: &SourceChoice) -> Result<Self, ModelFailure> {
        match choice.source.as_str() {
            "file" => Err(failure(
                "source_file_only",
                "Laden aus dem Internet ist abgeschaltet.",
            )),
            "custom" => {
                let base = custom_base(choice.custom_url.as_deref().unwrap_or(""))?;
                let host = base.host_str().unwrap_or_default().to_ascii_lowercase();
                Ok(Self {
                    source: "custom".into(),
                    hosts: vec![host],
                    custom_base: Some(base),
                    allow_http: false,
                })
            }
            source => {
                let definition = manifest
                    .sources
                    .get(source)
                    .ok_or_else(|| failure("unknown_component", "Unbekannte Quelle."))?;
                Ok(Self {
                    source: source.to_string(),
                    hosts: definition.hosts.clone(),
                    custom_base: None,
                    allow_http: false,
                })
            }
        }
    }

    pub fn url_for(&self, component: &ComponentDef, file: &FileDef) -> Result<Url, ModelFailure> {
        let raw = match &self.custom_base {
            Some(base) => format!(
                "{}/{}/{}",
                base.as_str().trim_end_matches('/'),
                component.id,
                file.path
            ),
            None => file.urls.get(&self.source).cloned().ok_or_else(|| {
                failure(
                    "host_not_allowed",
                    format!("{} gibt es bei dieser Quelle nicht.", file.path),
                )
            })?,
        };
        check_url(&raw, &self.hosts, self.allow_http)
    }

    /// Who fetches the bytes. In the Mac package, only the download
    /// service is available: if it is missing, there is no fallback to the
    /// shell. In development outside a bundle, on Windows, and for tests
    /// against 127.0.0.1, the shell fetches itself.
    fn fetcher(&self) -> Result<Fetcher, ModelFailure> {
        #[cfg(target_os = "macos")]
        if !self.allow_http {
            if crate::download_service::bundled() {
                return Ok(Fetcher::Service {
                    hosts: self.hosts.clone(),
                });
            }
            if !cfg!(debug_assertions) {
                return Err(failure("network", "Der Download-Dienst fehlt im Paket."));
            }
        }
        self.client().map(Fetcher::Direct)
    }

    fn client(&self) -> Result<reqwest::Client, ModelFailure> {
        let hosts = self.hosts.clone();
        let allow_http = self.allow_http;
        let redirects = reqwest::redirect::Policy::custom(move |attempt| {
            if attempt.previous().len() >= MAX_REDIRECTS {
                return attempt.error("zu viele Weiterleitungen");
            }
            let url = attempt.url();
            let scheme_ok = url.scheme() == "https" || (allow_http && url.scheme() == "http");
            if scheme_ok
                && url
                    .host_str()
                    .is_some_and(|host| host_allowed(&hosts, host))
            {
                attempt.follow()
            } else {
                attempt.error("Weiterleitung zu einem nicht erlaubten Host")
            }
        });
        reqwest::Client::builder()
            .https_only(!self.allow_http)
            .redirect(redirects)
            .connect_timeout(Duration::from_secs(20))
            .read_timeout(Duration::from_secs(60))
            .user_agent(concat!("unname/", env!("CARGO_PKG_VERSION")))
            .build()
            .map_err(|error| failure("network", error.to_string()))
    }

    #[cfg(test)]
    fn local_test(source: &str, hosts: &[&str]) -> Self {
        Self {
            source: source.into(),
            hosts: hosts.iter().map(|host| host.to_string()).collect(),
            custom_base: None,
            allow_http: true,
        }
    }
}

/// Own server: HTTPS only, no credentials, no query parameters.
pub fn custom_base(raw: &str) -> Result<Url, ModelFailure> {
    let invalid = || {
        failure(
            "invalid_custom_url",
            "Die Adresse des Servers ist ungültig.",
        )
    };
    let url = Url::parse(raw.trim()).map_err(|_| invalid())?;
    if url.scheme() != "https"
        || url.host_str().map_or(true, str::is_empty)
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(invalid());
    }
    Ok(url)
}

// ---------------------------------------------------------------- State ///
// Progress as the UI receives it.
// #[derive(Clone, Debug, Serialize, PartialEq)]
// pub struct ModelsEvent { /// `None` for events concerning the whole task. pub
// component: Option<String>,

/// Progress as seen by the UI.
#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct ModelsEvent {
    /// `None` for events concerning the entire task.
    pub component: Option<String>,
    /// `queued`, `downloading`, `verifying`, `installing`, `installed`,
    /// `failed`, `cancelled`, `finished`.
    pub state: String,
    pub received: u64,
    pub total: u64,
    pub error: Option<ModelFailure>,
}

impl ModelsEvent {
    fn new(component: Option<&str>, state: &str, received: u64, total: u64) -> Self {
        Self {
            component: component.map(str::to_string),
            state: state.to_string(),
            received,
            total,
            error: None,
        }
    }
}

#[derive(Debug, Deserialize, Serialize)]
struct InstallMarker {
    component: String,
    revision: String,
    installed_at: String,
    #[serde(default)]
    checked_at: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct FileOverview {
    pub path: String,
    pub size: u64,
    pub sha256: String,
    pub url: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ComponentOverview {
    pub id: String,
    pub kind: String,
    pub origin: String,
    pub license: String,
    pub download_size: u64,
    pub installed_size: u64,
    /// `installed`, `missing`, `partial`, or an ongoing state from
    /// [`ModelsEvent::state`].
    pub state: String,
    pub received: u64,
    pub installed_at: Option<String>,
    pub checked_at: Option<String>,
    pub error: Option<ModelFailure>,
    pub files: Vec<FileOverview>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ModelsOverview {
    pub revision: String,
    pub sources: Vec<String>,
    pub choice: SourceChoice,
    pub busy: bool,
    pub components: Vec<ComponentOverview>,
}

#[derive(Clone, Debug, Serialize)]
pub struct VerifyReport {
    pub checked_at: String,
    /// Bestandteile, deren Dateien abwichen – sie wurden entfernt.
    pub damaged: Vec<String>,
    pub overview: ModelsOverview,
}

/// An ongoing task (download, import, check). There is at most one.
struct Task {
    cancel: AtomicBool,
}

type TaskSlot = Arc<Mutex<Option<Arc<Task>>>>;

pub struct ModelStore {
    root: PathBuf,
    manifest: Manifest,
    task: TaskSlot,
    /// Latest state per component during and after a task.
    live: Mutex<BTreeMap<String, ModelsEvent>>,
}

/// Releases the ongoing task, even if it ends with an error.
pub struct TaskGuard {
    slot: TaskSlot,
    task: Arc<Task>,
}

impl TaskGuard {
    fn cancelled(&self) -> bool {
        self.task.cancel.load(Ordering::SeqCst)
    }
}

impl Drop for TaskGuard {
    fn drop(&mut self) {
        *self.slot.lock().expect("Modell-Sperre vergiftet") = None;
    }
}

impl ModelStore {
    pub fn open(root: PathBuf, manifest: Manifest) -> Result<Self, String> {
        fs::create_dir_all(&root).map_err(|error| {
            format!(
                "Modellordner {} ließ sich nicht anlegen: {error}",
                root.display()
            )
        })?;
        let store = Self {
            root,
            manifest,
            task: Arc::new(Mutex::new(None)),
            live: Mutex::new(BTreeMap::new()),
        };
        // Remnants of a cancelled import or installation.
        let _ = fs::remove_dir_all(store.root.join(STAGING_DIR));
        let _ = fs::remove_dir_all(store.root.join(IMPORT_DIR));
        store.quick_check();
        Ok(store)
    }

    #[cfg(test)]
    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn manifest(&self) -> &Manifest {
        &self.manifest
    }

    /// Quick check at startup: if sizes do not match, the component is
    /// considered not installed (the marker is removed). The full hash is
    /// computed only during the "Jetzt prüfen" step.
    fn quick_check(&self) {
        for component in &self.manifest.components {
            if self.marker(&component.id).is_none() {
                continue;
            }
            let folder = self.root.join(&component.id);
            let intact = component.installed_files().iter().all(|file| {
                fs::metadata(relative(&folder, &file.path))
                    .map(|metadata| metadata.is_file() && metadata.len() == file.size)
                    .unwrap_or(false)
            });
            if !intact {
                log::warn!(
                    "Modell {} unvollständig – gilt als nicht installiert.",
                    component.id
                );
                let _ = fs::remove_file(folder.join(INSTALL_MARKER));
            }
        }
    }

    fn marker(&self, id: &str) -> Option<InstallMarker> {
        let raw = fs::read(self.root.join(id).join(INSTALL_MARKER)).ok()?;
        let marker: InstallMarker = serde_json::from_slice(&raw).ok()?;
        (marker.component == id).then_some(marker)
    }

    pub fn installed(&self, id: &str) -> bool {
        self.marker(id).is_some()
    }

    pub fn choice(&self) -> SourceChoice {
        fs::read(self.root.join(SOURCE_FILE))
            .ok()
            .and_then(|raw| serde_json::from_slice::<SourceChoice>(&raw).ok())
            .filter(|choice| self.valid_choice(choice).is_ok())
            .unwrap_or_default()
    }

    fn valid_choice(&self, choice: &SourceChoice) -> Result<(), ModelFailure> {
        match choice.source.as_str() {
            "file" => Ok(()),
            "custom" => custom_base(choice.custom_url.as_deref().unwrap_or("")).map(|_| ()),
            source if self.manifest.sources.contains_key(source) => Ok(()),
            _ => Err(failure("unknown_component", "Unbekannte Quelle.")),
        }
    }

    pub fn set_choice(&self, choice: SourceChoice) -> Result<(), ModelFailure> {
        self.valid_choice(&choice)?;
        let content =
            serde_json::to_vec_pretty(&choice).map_err(|error| failure("io", error.to_string()))?;
        crate::saving::write_atomically(&self.root.join(SOURCE_FILE), &content)
            .map_err(|message| failure("io", message))
    }

    pub fn begin(&self) -> Result<TaskGuard, ModelFailure> {
        let mut slot = self.task.lock().expect("Modell-Sperre vergiftet");
        if slot.is_some() {
            return Err(failure("busy", "Es läuft bereits ein Download."));
        }
        let task = Arc::new(Task {
            cancel: AtomicBool::new(false),
        });
        *slot = Some(task.clone());
        Ok(TaskGuard {
            slot: self.task.clone(),
            task,
        })
    }

    pub fn cancel(&self) {
        if let Some(task) = self.task.lock().expect("Modell-Sperre vergiftet").as_ref() {
            task.cancel.store(true, Ordering::SeqCst);
        }
    }

    fn record(&self, event: &ModelsEvent) {
        if let Some(component) = &event.component {
            self.live
                .lock()
                .expect("Modell-Sperre vergiftet")
                .insert(component.clone(), event.clone());
        }
    }

    pub fn overview(&self) -> ModelsOverview {
        let choice = self.choice();
        let policy = DownloadPolicy::for_choice(&self.manifest, &choice).ok();
        let busy = self.task.lock().expect("Modell-Sperre vergiftet").is_some();
        let live = self.live.lock().expect("Modell-Sperre vergiftet").clone();
        let components = self
            .manifest
            .components
            .iter()
            .map(|component| {
                let marker = self.marker(&component.id);
                let partial = folder_size(&self.root.join(PARTIAL_DIR).join(&component.id));
                let event = live.get(&component.id);
                let running = busy
                    && event.is_some_and(|event| {
                        matches!(
                            event.state.as_str(),
                            "queued" | "downloading" | "verifying" | "installing"
                        )
                    });
                let (state, received) = match (&marker, running, event) {
                    (_, true, Some(event)) => (event.state.clone(), event.received),
                    (Some(_), _, _) => ("installed".to_string(), component.download_size()),
                    (None, _, _) if partial > 0 => ("partial".to_string(), partial),
                    _ => ("missing".to_string(), 0),
                };
                ComponentOverview {
                    id: component.id.clone(),
                    kind: component.kind.clone(),
                    origin: component.origin.clone(),
                    license: component.license.clone(),
                    download_size: component.download_size(),
                    installed_size: component.installed_size(),
                    state,
                    received,
                    installed_at: marker.as_ref().map(|item| item.installed_at.clone()),
                    checked_at: marker.as_ref().and_then(|item| item.checked_at.clone()),
                    error: event.and_then(|event| event.error.clone()),
                    files: component
                        .files
                        .iter()
                        .map(|file| FileOverview {
                            path: file.path.clone(),
                            size: file.size,
                            sha256: file.sha256.clone(),
                            url: policy
                                .as_ref()
                                .and_then(|policy| policy.url_for(component, file).ok())
                                .map(|url| url.to_string()),
                        })
                        .collect(),
                }
            })
            .collect();
        ModelsOverview {
            revision: self.manifest.revision.clone(),
            sources: self.manifest.sources.keys().cloned().collect(),
            choice,
            busy,
            components,
        }
    }

    /// Components in manifest order; unknown ones are an error.
    pub fn select(&self, ids: &[String]) -> Result<Vec<ComponentDef>, ModelFailure> {
        for id in ids {
            if self.manifest.component(id).is_none() {
                return Err(failure("unknown_component", format!("Unbekannt: {id}")));
            }
        }
        Ok(self
            .manifest
            .components
            .iter()
            .filter(|component| ids.contains(&component.id))
            .cloned()
            .collect())
    }

    // ------------------------------------------------------------ Download

    /// Loads the selected components sequentially, checks and installs them.
    /// `emit` receives every intermediate state, `installed` receives every
    /// completed component (the shell then restarts the sidecar). On the first
    /// error, the task ends; started files remain for a new attempt, a
    /// differing file does not.
    pub async fn download(
        &self,
        guard: &TaskGuard,
        components: &[ComponentDef],
        policy: &DownloadPolicy,
        emit: &(dyn Fn(ModelsEvent) + Send + Sync),
        installed: &(dyn Fn(&ComponentDef) + Send + Sync),
    ) -> Result<(), ModelFailure> {
        let pending: Vec<&ComponentDef> = components
            .iter()
            .filter(|component| !self.installed(&component.id))
            .collect();
        for component in &pending {
            let event =
                ModelsEvent::new(Some(&component.id), "queued", 0, component.download_size());
            self.record(&event);
            emit(event);
        }
        let client = policy.fetcher()?;
        for component in pending {
            let result = self
                .download_component(guard, &client, component, policy, emit)
                .await;
            match result {
                Ok(()) => installed(component),
                Err(error) => {
                    let state = if error.code == "cancelled" {
                        "cancelled"
                    } else {
                        "failed"
                    };
                    let mut event =
                        ModelsEvent::new(Some(&component.id), state, 0, component.download_size());
                    event.error = Some(error.clone());
                    self.record(&event);
                    emit(event);
                    return Err(error);
                }
            }
        }
        // The client and thus every connection end here.
        drop(client);
        Ok(())
    }

    async fn download_component(
        &self,
        guard: &TaskGuard,
        client: &Fetcher,
        component: &ComponentDef,
        policy: &DownloadPolicy,
        emit: &(dyn Fn(ModelsEvent) + Send + Sync),
    ) -> Result<(), ModelFailure> {
        let partial = self.root.join(PARTIAL_DIR).join(&component.id);
        let total = component.download_size();
        let mut done = 0u64;
        for file in &component.files {
            let url = policy.url_for(component, file)?;
            let target = relative(&partial, &file.path);
            let mut last = Instant::now()
                .checked_sub(PROGRESS_INTERVAL)
                .unwrap_or_else(Instant::now);
            let before = done;
            download_file(client, url, &target, file, &guard.task.cancel, |received| {
                if last.elapsed() >= PROGRESS_INTERVAL {
                    last = Instant::now();
                    let event = ModelsEvent::new(
                        Some(&component.id),
                        "downloading",
                        before + received,
                        total,
                    );
                    self.record(&event);
                    emit(event);
                }
            })
            .await?;
            done += file.size;
        }
        let event = ModelsEvent::new(Some(&component.id), "installing", total, total);
        self.record(&event);
        emit(event);
        self.install(component, &partial)?;
        let event = ModelsEvent::new(Some(&component.id), "installed", total, total);
        self.record(&event);
        emit(event);
        Ok(())
    }

    // ------------------------------------------------------------ Install ///
    // Assembles the component in `.staging` and renames it as a whole. ///
    // Files from `source` are already checked; unpacked content is verified ///
    // against the manifest when written. fn install(&self, component:
    // &ComponentDef, source: &Path) -> Result<(), ModelFailure> { let
    // staging_root = self.root.join(STAGING_DIR);

    /// Builds the component in `.staging` and renames it as a whole. Files from
    /// `source` are already checked; unpacked content is checked against the
    /// manifest when written.
    fn install(&self, component: &ComponentDef, source: &Path) -> Result<(), ModelFailure> {
        let staging_root = self.root.join(STAGING_DIR);
        fs::create_dir_all(&staging_root).map_err(io_failure)?;
        let staging = tempfile::Builder::new()
            .prefix(&format!("{}-", component.id))
            .tempdir_in(&staging_root)
            .map_err(io_failure)?;
        for file in &component.files {
            let from = relative(source, &file.path);
            match &file.unpack {
                Some(unpack) => unpack_zip(&from, unpack, staging.path())?,
                None => {
                    let to = relative(staging.path(), &file.path);
                    if let Some(parent) = to.parent() {
                        fs::create_dir_all(parent).map_err(io_failure)?;
                    }
                    fs::rename(&from, &to).map_err(io_failure)?;
                }
            }
        }
        let marker = InstallMarker {
            component: component.id.clone(),
            revision: self.manifest.revision.clone(),
            installed_at: now_iso8601(),
            checked_at: Some(now_iso8601()),
        };
        write_marker(staging.path(), &marker)?;

        let target = self.root.join(&component.id);
        let staged = staging.keep();
        if target.exists() {
            let old = staging_root.join(format!("{}-alt-{}", component.id, std::process::id()));
            let _ = fs::remove_dir_all(&old);
            fs::rename(&target, &old).map_err(io_failure)?;
            if let Err(error) = fs::rename(&staged, &target) {
                let _ = fs::rename(&old, &target);
                let _ = fs::remove_dir_all(&staged);
                return Err(io_failure(error));
            }
            let _ = fs::remove_dir_all(&old);
        } else if let Err(error) = fs::rename(&staged, &target) {
            let _ = fs::remove_dir_all(&staged);
            return Err(io_failure(error));
        }
        let _ = fs::remove_dir_all(source);
        Ok(())
    }

    pub fn remove(&self, id: &str) -> Result<ComponentDef, ModelFailure> {
        let component = self
            .manifest
            .component(id)
            .cloned()
            .ok_or_else(|| failure("unknown_component", format!("Unbekannt: {id}")))?;
        let folder = self.root.join(id);
        // First the marker, then the rest: a partially deleted folder is never
        // considered installed.
        let _ = fs::remove_file(folder.join(INSTALL_MARKER));
        if folder.exists() {
            fs::remove_dir_all(&folder).map_err(io_failure)?;
        }
        let _ = fs::remove_dir_all(self.root.join(PARTIAL_DIR).join(id));
        self.live
            .lock()
            .expect("Modell-Sperre vergiftet")
            .remove(id);
        Ok(component)
    }

    // ------------------------------------------------------------ Review

    /// Recalculates all hashes. Divergent components are removed.
    pub fn verify(&self, guard: &TaskGuard) -> Result<Vec<ComponentDef>, ModelFailure> {
        let mut damaged = Vec::new();
        for component in &self.manifest.components {
            let Some(mut marker) = self.marker(&component.id) else {
                continue;
            };
            let folder = self.root.join(&component.id);
            let mut intact = true;
            for file in component.installed_files() {
                if guard.cancelled() {
                    return Err(failure("cancelled", "Prüfung abgebrochen."));
                }
                if !file_matches(&relative(&folder, &file.path), file.size, &file.sha256) {
                    intact = false;
                    break;
                }
            }
            if intact {
                marker.checked_at = Some(now_iso8601());
                write_marker(&folder, &marker)?;
            } else {
                log::warn!(
                    "Modell {} weicht vom Manifest ab – wird entfernt.",
                    component.id
                );
                damaged.push(self.remove(&component.id)?);
            }
        }
        Ok(damaged)
    }

    // ------------------------------------------------------------ Package

    /// Installs a model package (`<bestandteil>/<datei>` in the Zip). All files
    /// are first unpacked and verified; installation occurs only if no file
    /// deviates.
    pub fn import_package(
        &self,
        guard: &TaskGuard,
        package: &Path,
        emit: &(dyn Fn(ModelsEvent) + Send + Sync),
    ) -> Result<Vec<ComponentDef>, ModelFailure> {
        let invalid = |message: String| failure("invalid_package", message);
        let file = File::open(package).map_err(io_failure)?;
        let mut archive = zip::ZipArchive::new(file).map_err(|error| invalid(error.to_string()))?;
        let import_root = self.root.join(IMPORT_DIR);
        let _ = fs::remove_dir_all(&import_root);

        let result = (|| {
            let mut found = Vec::new();
            for component in &self.manifest.components {
                let names: Vec<String> = component
                    .files
                    .iter()
                    .map(|file| format!("{}/{}", component.id, file.path))
                    .collect();
                let present = names
                    .iter()
                    .filter(|name| archive.index_for_name(name).is_some())
                    .count();
                if present == 0 {
                    continue;
                }
                if present != names.len() {
                    return Err(failure(
                        "package_incomplete",
                        format!("Im Paket fehlen Dateien von {}.", component.id),
                    ));
                }
                let total = component.download_size();
                let mut done = 0u64;
                let target = import_root.join(&component.id);
                for (file, name) in component.files.iter().zip(&names) {
                    if guard.cancelled() {
                        return Err(failure("cancelled", "Import abgebrochen."));
                    }
                    let event = ModelsEvent::new(Some(&component.id), "verifying", done, total);
                    self.record(&event);
                    emit(event);
                    let mut entry = archive
                        .by_name(name)
                        .map_err(|error| invalid(error.to_string()))?;
                    write_checked(
                        &mut entry,
                        &relative(&target, &file.path),
                        file.size,
                        &file.sha256,
                    )?;
                    done += file.size;
                }
                found.push(component.clone());
            }
            if found.is_empty() {
                return Err(failure(
                    "package_empty",
                    "Das Paket enthält keine bekannten Modelle.",
                ));
            }
            for component in &found {
                let total = component.download_size();
                let event = ModelsEvent::new(Some(&component.id), "installing", total, total);
                self.record(&event);
                emit(event);
                self.install(component, &import_root.join(&component.id))?;
                let event = ModelsEvent::new(Some(&component.id), "installed", total, total);
                self.record(&event);
                emit(event);
            }
            Ok(found)
        })();
        let _ = fs::remove_dir_all(&import_root);
        result
    }
}

// ---------------------------------------------------------------- Files

/// Fetches bytes either directly from the shell or via the download service.
enum Fetcher {
    Direct(reqwest::Client),
    #[cfg(target_os = "macos")]
    Service {
        hosts: Vec<String>,
    },
}

impl Fetcher {
    async fn get(&self, url: Url, offset: u64) -> Result<Body, ModelFailure> {
        match self {
            Self::Direct(client) => {
                let mut request = client.get(url);
                if offset > 0 {
                    request = request.header(reqwest::header::RANGE, format!("bytes={offset}-"));
                }
                Ok(Body::Direct(request.send().await.map_err(network_failure)?))
            }
            #[cfg(target_os = "macos")]
            Self::Service { hosts } => crate::download_service::get(
                url.as_str(),
                hosts,
                offset,
                concat!("unname/", env!("CARGO_PKG_VERSION")),
            )
            .await
            .map(Body::Service),
        }
    }
}

enum Body {
    Direct(reqwest::Response),
    #[cfg(target_os = "macos")]
    Service(crate::download_service::Response),
}

impl Body {
    fn status(&self) -> reqwest::StatusCode {
        match self {
            Self::Direct(response) => response.status(),
            #[cfg(target_os = "macos")]
            Self::Service(response) => reqwest::StatusCode::from_u16(response.status)
                .unwrap_or(reqwest::StatusCode::BAD_GATEWAY),
        }
    }

    async fn chunk(&mut self) -> Result<Option<Vec<u8>>, ModelFailure> {
        match self {
            Self::Direct(response) => Ok(response
                .chunk()
                .await
                .map_err(network_failure)?
                .map(|chunk| chunk.to_vec())),
            #[cfg(target_os = "macos")]
            Self::Service(response) => response.chunk().await,
        }
    }
}

/// Downloads a file to `target`, resumes partial downloads, and verifies size
/// and SHA-256 at the end. Deletes files that do not match.
async fn download_file(
    client: &Fetcher,
    url: Url,
    target: &Path,
    file: &FileDef,
    cancel: &AtomicBool,
    mut progress: impl FnMut(u64),
) -> Result<(), ModelFailure> {
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent).map_err(io_failure)?;
    }
    let mut have = fs::metadata(target).map(|meta| meta.len()).unwrap_or(0);
    if have > file.size {
        fs::remove_file(target).map_err(io_failure)?;
        have = 0;
    }
    let mut hasher = Sha256::new();
    if have > 0 {
        let prefix = target.to_path_buf();
        let (state, length) = tauri::async_runtime::spawn_blocking(move || hash_file(&prefix))
            .await
            .map_err(|error| failure("io", error.to_string()))?
            .map_err(io_failure)?;
        hasher = state;
        have = length;
    }
    if have < file.size {
        let mut response = client.get(url, have).await?;
        let status = response.status();
        let mut output = if have > 0 && status == reqwest::StatusCode::PARTIAL_CONTENT {
            OpenOptions::new()
                .append(true)
                .open(target)
                .map_err(io_failure)?
        } else if status.is_success() {
            // No resumption possible: start from the beginning.
            have = 0;
            hasher = Sha256::new();
            File::create(target).map_err(io_failure)?
        } else {
            return Err(failure(
                "http_status",
                format!("Die Quelle antwortet mit {status}."),
            ));
        };
        progress(have);
        while let Some(chunk) = response.chunk().await? {
            have += chunk.len() as u64;
            if have > file.size {
                drop(output);
                let _ = fs::remove_file(target);
                return Err(failure(
                    "size_mismatch",
                    format!("{} ist größer als erwartet.", file.path),
                ));
            }
            output.write_all(&chunk).map_err(io_failure)?;
            hasher.update(&chunk);
            progress(have);
            if cancel.load(Ordering::SeqCst) {
                let _ = output.sync_all();
                return Err(failure("cancelled", "Download abgebrochen."));
            }
        }
        output.sync_all().map_err(io_failure)?;
        if have < file.size {
            return Err(failure(
                "network",
                "Die Verbindung brach ab; ein neuer Versuch setzt fort.",
            ));
        }
    }
    if hex(&hasher.finalize()) != file.sha256 {
        let _ = fs::remove_file(target);
        return Err(failure(
            "hash_mismatch",
            format!(
                "{} weicht von der Prüfsumme ab und wurde verworfen.",
                file.path
            ),
        ));
    }
    Ok(())
}

fn network_failure(error: reqwest::Error) -> ModelFailure {
    let mut message = error.to_string();
    let mut source = std::error::Error::source(&error);
    while let Some(cause) = source {
        message.push_str(&format!(": {cause}"));
        source = cause.source();
    }
    if error.is_redirect() && message.contains("nicht erlaubten Host") {
        return failure("host_not_allowed", message);
    }
    failure("network", message)
}

/// Extracts exactly the files listed in `unpack` and verifies each one while
/// writing.
fn unpack_zip(container: &Path, unpack: &Unpack, target: &Path) -> Result<(), ModelFailure> {
    let file = File::open(container).map_err(io_failure)?;
    let mut archive = zip::ZipArchive::new(file)
        .map_err(|error| failure("invalid_package", error.to_string()))?;
    for inner in &unpack.files {
        let name = format!("{}{}", unpack.prefix, inner.path);
        let mut entry = archive
            .by_name(&name)
            .map_err(|_| failure("hash_mismatch", format!("{name} fehlt im Archiv.")))?;
        write_checked(
            &mut entry,
            &relative(target, &inner.path),
            inner.size,
            &inner.sha256,
        )?;
    }
    Ok(())
}

/// Writes at most `size` bytes and verifies the hash; deletes the file on
/// mismatch.
fn write_checked(
    reader: &mut dyn Read,
    target: &Path,
    size: u64,
    sha256: &str,
) -> Result<(), ModelFailure> {
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent).map_err(io_failure)?;
    }
    let mut output = File::create(target).map_err(io_failure)?;
    let mut hasher = Sha256::new();
    let mut written = 0u64;
    let mut buffer = vec![0u8; 1024 * 1024];
    let mut limited = reader.take(size + 1);
    loop {
        let read = limited.read(&mut buffer).map_err(io_failure)?;
        if read == 0 {
            break;
        }
        written += read as u64;
        hasher.update(&buffer[..read]);
        output.write_all(&buffer[..read]).map_err(io_failure)?;
    }
    output.sync_all().map_err(io_failure)?;
    drop(output);
    if written != size || hex(&hasher.finalize()) != sha256 {
        let _ = fs::remove_file(target);
        return Err(failure(
            "hash_mismatch",
            format!(
                "{} weicht von der Prüfsumme ab und wurde verworfen.",
                target
                    .file_name()
                    .map(|name| name.to_string_lossy().into_owned())
                    .unwrap_or_default()
            ),
        ));
    }
    Ok(())
}

fn write_marker(folder: &Path, marker: &InstallMarker) -> Result<(), ModelFailure> {
    let content =
        serde_json::to_vec_pretty(marker).map_err(|error| failure("io", error.to_string()))?;
    crate::saving::write_atomically(&folder.join(INSTALL_MARKER), &content)
        .map_err(|message| failure("io", message))
}

fn hash_file(path: &Path) -> io::Result<(Sha256, u64)> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1024 * 1024];
    let mut length = 0u64;
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        length += read as u64;
        hasher.update(&buffer[..read]);
    }
    Ok((hasher, length))
}

fn file_matches(path: &Path, size: u64, sha256: &str) -> bool {
    match fs::metadata(path) {
        Ok(metadata) if metadata.is_file() && metadata.len() == size => hash_file(path)
            .map(|(hasher, _)| hex(&hasher.finalize()) == sha256)
            .unwrap_or(false),
        _ => false,
    }
}

fn folder_size(path: &Path) -> u64 {
    let Ok(entries) = fs::read_dir(path) else {
        return 0;
    };
    entries
        .filter_map(Result::ok)
        .map(|entry| match entry.file_type() {
            Ok(kind) if kind.is_dir() => folder_size(&entry.path()),
            Ok(kind) if kind.is_file() => entry.metadata().map(|meta| meta.len()).unwrap_or(0),
            _ => 0,
        })
        .sum()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn now_iso8601() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
}

// ---------------------------------------------------------------- Shell

/// UI commands. The UI never sees paths; the model package selects them via
/// system dialogs and reads them here.
pub mod shell {
    use serde_json::json;
    use tauri::{AppHandle, Emitter, Manager};
    use tauri_plugin_dialog::DialogExt;

    use super::*;
    use crate::diagnostics::{DiagnosticsLog, Severity};
    use crate::sidecar::SidecarState;

    fn emit(app: &AppHandle, event: ModelsEvent) {
        if let Err(error) = app.emit(MODELS_EVENT, &event) {
            log::error!("Modell-Ereignis nicht zustellbar: {error}");
        }
    }

    fn finished(app: &AppHandle, result: &Result<(), ModelFailure>) {
        let mut event = ModelsEvent::new(None, "finished", 0, 0);
        if let Err(error) = result {
            event.state = if error.code == "cancelled" {
                "cancelled".into()
            } else {
                "failed".into()
            };
            event.error = Some(error.clone());
        }
        emit(app, event);
    }

    /// After name detection or language changes, the sidecar restarts: it
    /// builds its detection model once per process. Text detection runs on
    /// every call, so no restart is needed.
    fn installed(app: &AppHandle, component: &ComponentDef) {
        app.state::<DiagnosticsLog>().record(
            "model_install",
            Severity::Info,
            format!("Modell {} geprüft und installiert.", component.id),
            json!({ "bestandteil": component.id, "bytes": component.download_size() }),
        );
        if component.needs_sidecar_restart() {
            app.state::<SidecarState>().request_restart();
        }
    }

    fn rejected(app: &AppHandle, error: &ModelFailure) {
        if matches!(
            error.code.as_str(),
            "hash_mismatch" | "size_mismatch" | "host_not_allowed"
        ) {
            app.state::<DiagnosticsLog>().record(
                "model_rejected",
                Severity::Warning,
                "Modelldatei verworfen.",
                json!({ "grund": error.code }),
            );
        }
    }

    pub fn set_source(
        app: &AppHandle,
        choice: SourceChoice,
    ) -> Result<ModelsOverview, ModelFailure> {
        let store = app.state::<ModelStore>();
        store.set_choice(choice)?;
        Ok(store.overview())
    }

    /// Starts the download in the background; progress is emitted as an event.
    pub fn start_download(app: AppHandle, ids: Vec<String>) -> Result<(), ModelFailure> {
        let store = app.state::<ModelStore>();
        let components = store.select(&ids)?;
        let policy = DownloadPolicy::for_choice(store.manifest(), &store.choice())?;
        let guard = store.begin()?;
        tauri::async_runtime::spawn(async move {
            let store = app.state::<ModelStore>();
            let result = store
                .download(
                    &guard,
                    &components,
                    &policy,
                    &|event| emit(&app, event),
                    &|component| installed(&app, component),
                )
                .await;
            drop(guard);
            if let Err(error) = &result {
                rejected(&app, error);
            }
            finished(&app, &result);
        });
        Ok(())
    }

    pub fn cancel(app: &AppHandle) {
        app.state::<ModelStore>().cancel();
    }

    /// "Install model package from file …". `None` means cancelled.
    pub async fn import_package(
        app: AppHandle,
        title: String,
        filter: String,
    ) -> Result<Option<ModelsOverview>, ModelFailure> {
        let guard = app.state::<ModelStore>().begin()?;
        let (sender, receiver) = tokio::sync::oneshot::channel();
        app.dialog()
            .file()
            .set_title(title)
            .add_filter(filter, &["zip"])
            .pick_file(move |path| {
                let _ = sender.send(path);
            });
        let chosen = receiver
            .await
            .map_err(|_| failure("io", "Der Auswahldialog wurde unerwartet beendet."))?;
        let Some(path) = chosen.and_then(|path| path.into_path().ok()) else {
            return Ok(None);
        };
        let worker = app.clone();
        let result = tauri::async_runtime::spawn_blocking(move || {
            let store = worker.state::<ModelStore>();
            let installed_components =
                store.import_package(&guard, &path, &|event| emit(&worker, event))?;
            for component in &installed_components {
                installed(&worker, component);
            }
            Ok::<_, ModelFailure>(())
        })
        .await
        .map_err(|error| failure("io", error.to_string()))?;
        if let Err(error) = &result {
            rejected(&app, error);
        }
        finished(&app, &result);
        result.map(|()| Some(app.state::<ModelStore>().overview()))
    }

    /// “Jetzt prüfen”: recompute all hashes; remove any discrepancies.
    pub async fn verify(app: AppHandle) -> Result<VerifyReport, ModelFailure> {
        let guard = app.state::<ModelStore>().begin()?;
        let worker = app.clone();
        let damaged = tauri::async_runtime::spawn_blocking(move || {
            worker.state::<ModelStore>().verify(&guard)
        })
        .await
        .map_err(|error| failure("io", error.to_string()))??;
        if !damaged.is_empty() {
            app.state::<DiagnosticsLog>().record(
                "model_rejected",
                Severity::Warning,
                "Installierte Modelle wichen vom Manifest ab und wurden entfernt.",
                json!({ "bestandteile": damaged.iter().map(|item| item.id.clone()).collect::<Vec<_>>() }),
            );
        }
        if damaged.iter().any(ComponentDef::needs_sidecar_restart) {
            app.state::<SidecarState>().request_restart();
        }
        Ok(VerifyReport {
            checked_at: now_iso8601(),
            damaged: damaged.into_iter().map(|item| item.id).collect(),
            overview: app.state::<ModelStore>().overview(),
        })
    }

    pub fn remove(app: &AppHandle, id: &str) -> Result<ModelsOverview, ModelFailure> {
        let store = app.state::<ModelStore>();
        let _guard = store.begin()?;
        let component = store.remove(id)?;
        if component.needs_sidecar_restart() {
            app.state::<SidecarState>().request_restart();
        }
        Ok(store.overview())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader};
    use std::net::TcpListener;
    use std::sync::atomic::AtomicUsize;

    fn sha(content: &[u8]) -> String {
        hex(&Sha256::digest(content))
    }

    /// A small HTTP/1.1 server for 127.0.0.1 with `Range` support. `routes`:
    /// path → (status, headers, body).
    struct Server {
        base: String,
        requests: Arc<Mutex<Vec<String>>>,
    }

    /// Path, status, headers, and content.
    type Route = (&'static str, u16, Vec<(String, String)>, Vec<u8>);

    fn serve(routes: Vec<Route>) -> Server {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let base = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
        let requests = Arc::new(Mutex::new(Vec::new()));
        let seen = requests.clone();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { break };
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut line = String::new();
                if reader.read_line(&mut line).is_err() {
                    continue;
                }
                let path = line.split_whitespace().nth(1).unwrap_or("").to_string();
                let mut range: Option<u64> = None;
                loop {
                    let mut header = String::new();
                    if reader.read_line(&mut header).is_err() || header.trim().is_empty() {
                        break;
                    }
                    if let Some(value) = header.to_ascii_lowercase().strip_prefix("range: bytes=") {
                        range = value.trim().trim_end_matches('-').parse().ok();
                    }
                }
                seen.lock().unwrap().push(format!(
                    "{path}{}",
                    range.map(|r| format!(" ab {r}")).unwrap_or_default()
                ));
                let Some((_, status, headers, body)) =
                    routes.iter().find(|(route, ..)| *route == path)
                else {
                    let _ = stream.write_all(
                        b"HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\nconnection: close\r\n\r\n",
                    );
                    continue;
                };
                let (status, body) = match range {
                    Some(start) if *status == 200 && (start as usize) < body.len() => {
                        (206, body[start as usize..].to_vec())
                    }
                    _ => (*status, body.clone()),
                };
                let mut response = format!(
                    "HTTP/1.1 {status} X\r\ncontent-length: {}\r\nconnection: close\r\n",
                    body.len()
                );
                for (name, value) in headers {
                    response.push_str(&format!("{name}: {value}\r\n"));
                }
                response.push_str("\r\n");
                let _ = stream.write_all(response.as_bytes());
                let _ = stream.write_all(&body);
            }
        });
        Server { base, requests }
    }

    fn manifest_for(base: &str, files: &[(&str, &[u8])]) -> Manifest {
        let entries: Vec<serde_json::Value> = files
            .iter()
            .map(|(path, content)| {
                serde_json::json!({
                    "path": path,
                    "size": content.len(),
                    "sha256": sha(content),
                    "urls": {"test": format!("{base}/ner/{path}")},
                })
            })
            .collect();
        let json = serde_json::json!({
            "schema": 1,
            "revision": "test",
            "sources": {"test": {"hosts": ["127.0.0.1"]}},
            "components": [
                {"id": "ner", "kind": "ner", "origin": "Test", "license": "MIT", "files": entries},
                {"id": "de", "kind": "language", "origin": "Test", "license": "MIT", "files": [
                    {"path": "x.whl", "size": 1, "sha256": sha(b"x"), "urls": {}}
                ]}
            ]
        });
        Manifest::parse_local(&json.to_string()).expect("Testmanifest")
    }

    fn run_download(
        store: &ModelStore,
        policy: &DownloadPolicy,
        ids: &[&str],
    ) -> Result<Vec<String>, ModelFailure> {
        let guard = store.begin().expect("frei");
        let components = store
            .select(&ids.iter().map(|id| id.to_string()).collect::<Vec<_>>())
            .unwrap();
        let installed = Mutex::new(Vec::new());
        tauri::async_runtime::block_on(store.download(
            &guard,
            &components,
            policy,
            &|_| {},
            &|component| installed.lock().unwrap().push(component.id.clone()),
        ))?;
        Ok(installed.into_inner().unwrap())
    }

    #[test]
    fn bundled_manifest_is_valid_and_complete() {
        let manifest = Manifest::bundled().expect("models.lock.json");
        let ids: Vec<&str> = manifest
            .components
            .iter()
            .map(|item| item.id.as_str())
            .collect();
        assert_eq!(ids, ["ner", "de", "en", "ocr", "classifier"]);
        // tessdata is for Windows only; macOS uses Apple Vision.
        assert_eq!(
            manifest.component("ocr").unwrap().platforms,
            Some(vec!["windows".to_string()])
        );
        // Second opinion: optional, only ONNX and Tokenizer.
        let classifier = manifest.component("classifier").unwrap();
        assert_eq!(classifier.kind, "classifier");
        assert!(classifier.download_size() < 200_000_000);
        // Language packs: small spaCy models, data only.
        for id in ["de", "en"] {
            let language = manifest.component(id).unwrap();
            assert_eq!(language.kind, "language");
            assert!(language.download_size() < 20_000_000);
        }
        let ner = manifest.component("ner").unwrap();
        assert!(ner.files.iter().any(|file| file.path.ends_with(".onnx")));
        // No Pickle, no PyTorch weight, no Python code.
        let all: Vec<String> = manifest
            .components
            .iter()
            .flat_map(|item| item.installed_files())
            .map(|file| file.path)
            .collect();
        assert!(all.iter().all(|path| safe_relative_path(path)));
        assert!(!all
            .iter()
            .any(|path| path.ends_with(".bin") && path.contains("pytorch")));
        for component in &manifest.components {
            for file in &component.files {
                for source in manifest.sources.keys() {
                    assert!(
                        file.urls.contains_key(source),
                        "{} ohne {source}",
                        file.path
                    );
                }
            }
        }
    }

    #[test]
    fn paths_without_escape_or_executables() {
        for good in [
            "model.int8.onnx",
            "vocab/vectors",
            "ner/cfg",
            "deu.traineddata",
        ] {
            assert!(safe_relative_path(good), "{good}");
        }
        for bad in [
            "../x",
            "/etc/passwd",
            "a\\b",
            "a//b",
            ".installed.json",
            "x/./y",
            "__init__.py",
            "model.PT",
            "lib.so",
            "evil.exe",
            "a/../b",
            "",
        ] {
            assert!(!safe_relative_path(bad), "{bad}");
        }
    }

    #[test]
    fn only_https_and_only_allowed_hosts() {
        let hosts = vec!["huggingface.co".to_string(), "*.hf.co".to_string()];
        assert!(check_url("https://huggingface.co/a", &hosts, false).is_ok());
        assert!(check_url("https://cdn-lfs.hf.co/a", &hosts, false).is_ok());
        for bad in [
            "http://huggingface.co/a",
            "https://evilhuggingface.co/a",
            "https://hf.co.evil.net/a",
            "https://user:pw@huggingface.co/a",
            "https://example.org/a",
            "file:///etc/passwd",
        ] {
            assert_eq!(
                check_url(bad, &hosts, false).unwrap_err().code,
                "host_not_allowed",
                "{bad}"
            );
        }
        let json =
            MANIFEST_JSON.replace("https://raw.githubusercontent.com", "https://evil.example");
        assert!(Manifest::parse(&json).is_err(), "fremder Host im Manifest");
    }

    #[test]
    fn second_opinion_is_optional_and_needs_no_restart() {
        let file = serde_json::json!({"path": "model.int8.onnx", "size": 1, "sha256": "ab".repeat(32), "urls": {}});
        let component = |id: &str, kind: &str| serde_json::json!({"id": id, "kind": kind, "origin": "T", "license": "MIT", "files": [file]});
        let manifest = |components: Vec<serde_json::Value>| {
            serde_json::json!({
                "schema": 1, "revision": "t", "sources": {}, "components": components
            })
            .to_string()
        };
        let with_classifier = Manifest::parse(&manifest(vec![
            component("ner", "ner"),
            component("de", "language"),
            component("classifier", "classifier"),
        ]))
        .expect("Klassifikator ist eine bekannte Art");
        assert!(!with_classifier
            .component("classifier")
            .unwrap()
            .needs_sidecar_restart());
        // It alone handles neither named entity recognition nor language
        // detection.
        assert!(Manifest::parse(&manifest(vec![
            component("ner", "ner"),
            component("classifier", "classifier")
        ]))
        .is_err());
        assert!(Manifest::parse(&manifest(vec![
            component("ner", "ner"),
            component("de", "language"),
            component("x", "llm")
        ]))
        .is_err());
    }

    #[test]
    fn components_of_other_platforms_are_absent() {
        let ids = |platform: &str| -> Vec<String> {
            Manifest::bundled()
                .unwrap()
                .for_platform(platform)
                .components
                .into_iter()
                .map(|item| item.id)
                .collect()
        };
        assert_eq!(ids("macos"), ["ner", "de", "en", "classifier"]);
        assert_eq!(ids("windows"), ["ner", "de", "en", "ocr", "classifier"]);
        let here = Manifest::for_this_platform().unwrap();
        assert_eq!(
            here.component("ocr").is_some(),
            CURRENT_PLATFORM == "windows"
        );

        // Unknown or empty platforms invalidate the manifest.
        for invalid in [r#"["beos"]"#, "[]"] {
            let json = MANIFEST_JSON.replacen(
                "\"platforms\": [\n        \"windows\"\n      ]",
                &format!(r#""platforms": {invalid}"#),
                1,
            );
            assert_ne!(json, MANIFEST_JSON);
            assert!(Manifest::parse(&json).is_err(), "{invalid}");
        }
    }

    #[test]
    fn custom_server_only_https_without_credentials() {
        let manifest = Manifest::bundled().unwrap();
        let choice = SourceChoice {
            source: "custom".into(),
            custom_url: Some("https://modelle.firma.example/unname/".into()),
        };
        let policy = DownloadPolicy::for_choice(&manifest, &choice).unwrap();
        let ner = manifest.component("ner").unwrap();
        assert_eq!(
            policy.url_for(ner, &ner.files[0]).unwrap().as_str(),
            "https://modelle.firma.example/unname/ner/model.int8.onnx"
        );
        for bad in [
            "http://firma.example",
            "https://a:b@firma.example",
            "https://firma.example/?x=1",
            "kein url",
        ] {
            assert!(custom_base(bad).is_err(), "{bad}");
        }
        let file_only = SourceChoice {
            source: "file".into(),
            custom_url: None,
        };
        assert_eq!(
            DownloadPolicy::for_choice(&manifest, &file_only)
                .unwrap_err()
                .code,
            "source_file_only"
        );
    }

    #[test]
    fn download_verifies_and_installs_atomically() {
        let model = b"onnx-bytes".repeat(1000);
        let server = serve(vec![
            ("/ner/model.int8.onnx", 200, vec![], model.clone()),
            ("/ner/config.json", 200, vec![], b"{}".to_vec()),
        ]);
        let manifest = manifest_for(
            &server.base,
            &[("model.int8.onnx", &model), ("config.json", b"{}")],
        );
        let root = tempfile::tempdir().unwrap();
        let store = ModelStore::open(root.path().join("models"), manifest).unwrap();
        let policy = DownloadPolicy::local_test("test", &["127.0.0.1"]);

        let installed = run_download(&store, &policy, &["ner"]).expect("Download");
        assert_eq!(installed, ["ner"]);
        assert!(store.installed("ner"));
        let folder = store.root().join("ner");
        assert_eq!(fs::read(folder.join("model.int8.onnx")).unwrap(), model);
        assert!(folder.join(INSTALL_MARKER).is_file());
        assert!(!store.root().join(PARTIAL_DIR).join("ner").exists());
        assert_eq!(store.overview().components[0].state, "installed");
        assert!(!store.overview().busy, "Aufgabe freigegeben");
    }

    #[test]
    fn tampered_file_is_discarded() {
        let genuine = b"echtes modell".to_vec();
        let server = serve(vec![(
            "/ner/model.int8.onnx",
            200,
            vec![],
            b"falsches modell".to_vec()[..genuine.len()].to_vec(),
        )]);
        let manifest = manifest_for(&server.base, &[("model.int8.onnx", &genuine)]);
        let root = tempfile::tempdir().unwrap();
        let store = ModelStore::open(root.path().join("models"), manifest).unwrap();
        let policy = DownloadPolicy::local_test("test", &["127.0.0.1"]);

        let error = run_download(&store, &policy, &["ner"]).unwrap_err();
        assert_eq!(error.code, "hash_mismatch");
        assert!(!store.installed("ner"));
        assert!(!store.root().join("ner").exists());
        assert!(!store
            .root()
            .join(PARTIAL_DIR)
            .join("ner/model.int8.onnx")
            .exists());
        let overview = store.overview();
        assert_eq!(overview.components[0].state, "missing");
        assert_eq!(
            overview.components[0].error.as_ref().unwrap().code,
            "hash_mismatch"
        );
    }

    #[test]
    fn oversized_response_is_aborted() {
        let genuine = b"klein".to_vec();
        let server = serve(vec![(
            "/ner/model.int8.onnx",
            200,
            vec![],
            b"viel zu gross".to_vec(),
        )]);
        let manifest = manifest_for(&server.base, &[("model.int8.onnx", &genuine)]);
        let root = tempfile::tempdir().unwrap();
        let store = ModelStore::open(root.path().join("models"), manifest).unwrap();
        let policy = DownloadPolicy::local_test("test", &["127.0.0.1"]);
        assert_eq!(
            run_download(&store, &policy, &["ner"]).unwrap_err().code,
            "size_mismatch"
        );
        assert!(!store.installed("ner"));
    }

    #[test]
    fn partial_download_is_resumed() {
        let model: Vec<u8> = (0..50_000u32).map(|n| (n % 251) as u8).collect();
        let server = serve(vec![("/ner/model.int8.onnx", 200, vec![], model.clone())]);
        let manifest = manifest_for(&server.base, &[("model.int8.onnx", &model)]);
        let root = tempfile::tempdir().unwrap();
        let store = ModelStore::open(root.path().join("models"), manifest).unwrap();
        let partial = store.root().join(PARTIAL_DIR).join("ner");
        fs::create_dir_all(&partial).unwrap();
        fs::write(partial.join("model.int8.onnx"), &model[..20_000]).unwrap();
        assert_eq!(store.overview().components[0].state, "partial");

        let policy = DownloadPolicy::local_test("test", &["127.0.0.1"]);
        run_download(&store, &policy, &["ner"]).expect("Fortsetzen");
        assert!(store.installed("ner"));
        assert_eq!(
            server.requests.lock().unwrap().as_slice(),
            ["/ner/model.int8.onnx ab 20000"]
        );
    }

    #[test]
    fn redirect_to_foreign_host_is_rejected() {
        let model = b"modell".to_vec();
        let server = serve(vec![(
            "/ner/model.int8.onnx",
            302,
            vec![("location".into(), "http://localhost:9/boese".into())],
            vec![],
        )]);
        let manifest = manifest_for(&server.base, &[("model.int8.onnx", &model)]);
        let root = tempfile::tempdir().unwrap();
        let store = ModelStore::open(root.path().join("models"), manifest).unwrap();
        let policy = DownloadPolicy::local_test("test", &["127.0.0.1"]);
        let error = run_download(&store, &policy, &["ner"]).unwrap_err();
        assert_eq!(error.code, "host_not_allowed", "{error:?}");
        assert!(!store.installed("ner"));
    }

    #[test]
    fn cancel_keeps_partial_download() {
        let model = vec![7u8; 4 * 1024 * 1024];
        let server = serve(vec![("/ner/model.int8.onnx", 200, vec![], model.clone())]);
        let manifest = manifest_for(&server.base, &[("model.int8.onnx", &model)]);
        let root = tempfile::tempdir().unwrap();
        let store = ModelStore::open(root.path().join("models"), manifest).unwrap();
        let policy = DownloadPolicy::local_test("test", &["127.0.0.1"]);
        let guard = store.begin().unwrap();
        assert_eq!(store.begin().err().unwrap().code, "busy");
        let components = store.select(&["ner".to_string()]).unwrap();
        let events = AtomicUsize::new(0);
        let error = tauri::async_runtime::block_on(store.download(
            &guard,
            &components,
            &policy,
            &|event| {
                if event.state == "downloading" && events.fetch_add(1, Ordering::SeqCst) == 0 {
                    store.cancel();
                }
            },
            &|_| {},
        ))
        .unwrap_err();
        assert_eq!(error.code, "cancelled");
        drop(guard);
        assert!(!store.installed("ner"));
        assert_eq!(store.overview().components[0].state, "partial");
    }

    fn zip_bytes(entries: &[(&str, &[u8])]) -> Vec<u8> {
        let mut buffer = io::Cursor::new(Vec::new());
        {
            let mut writer = zip::ZipWriter::new(&mut buffer);
            let options = zip::write::SimpleFileOptions::default()
                .compression_method(zip::CompressionMethod::Stored);
            for (name, content) in entries {
                writer.start_file(*name, options).unwrap();
                writer.write_all(content).unwrap();
            }
            writer.finish().unwrap();
        }
        buffer.into_inner()
    }

    fn wheel_manifest(wheel: &[u8], inner: &[(&str, &[u8])]) -> Manifest {
        let files: Vec<serde_json::Value> = inner
            .iter()
            .map(|(path, content)| serde_json::json!({"path": path, "size": content.len(), "sha256": sha(content)}))
            .collect();
        let json = serde_json::json!({
            "schema": 1, "revision": "test",
            "sources": {"test": {"hosts": ["127.0.0.1"]}},
            "components": [
                {"id": "ner", "kind": "ner", "origin": "T", "license": "MIT", "files": [
                    {"path": "model.int8.onnx", "size": 5, "sha256": sha(b"onnx!"), "urls": {}}
                ]},
                {"id": "de", "kind": "language", "origin": "T", "license": "MIT", "files": [
                    {"path": "de.whl", "size": wheel.len(), "sha256": sha(wheel), "urls": {},
                     "unpack": {"prefix": "de/de-3.8.0/", "files": files}}
                ]}
            ]
        });
        Manifest::parse(&json.to_string()).unwrap()
    }

    #[test]
    fn wheel_is_unpacked_with_data_only() {
        let wheel = zip_bytes(&[
            ("de/__init__.py", b"import os; os.system('boese')"),
            ("de/de-3.8.0/config.cfg", b"[nlp]"),
            ("de/de-3.8.0/vocab/vectors", b"0123456789"),
        ]);
        let manifest = wheel_manifest(
            &wheel,
            &[("config.cfg", b"[nlp]"), ("vocab/vectors", b"0123456789")],
        );
        let package = zip_bytes(&[("de/de.whl", &wheel), ("ner/model.int8.onnx", b"onnx!")]);
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("paket.zip");
        fs::write(&path, &package).unwrap();
        let store = ModelStore::open(root.path().join("models"), manifest).unwrap();

        let guard = store.begin().unwrap();
        let installed = store
            .import_package(&guard, &path, &|_| {})
            .expect("Import");
        drop(guard);
        assert_eq!(
            installed
                .iter()
                .map(|item| item.id.as_str())
                .collect::<Vec<_>>(),
            ["ner", "de"]
        );
        let de = store.root().join("de");
        assert_eq!(fs::read(de.join("vocab/vectors")).unwrap(), b"0123456789");
        assert!(!de.join("__init__.py").exists());
        assert!(
            !de.join("de.whl").exists(),
            "Wheel nach dem Entpacken entfernt"
        );
        assert!(!store.root().join(IMPORT_DIR).exists());
    }

    #[test]
    fn tampered_package_installs_nothing() {
        let wheel = zip_bytes(&[("de/de-3.8.0/config.cfg", b"[nlp]")]);
        let manifest = wheel_manifest(&wheel, &[("config.cfg", b"[nlp]")]);
        let package = zip_bytes(&[
            ("ner/model.int8.onnx", b"onnx!"),
            (
                "de/de.whl",
                &zip_bytes(&[("de/de-3.8.0/config.cfg", b"[evil")]),
            ),
        ]);
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("paket.zip");
        fs::write(&path, &package).unwrap();
        let store = ModelStore::open(root.path().join("models"), manifest).unwrap();
        let guard = store.begin().unwrap();
        let error = store.import_package(&guard, &path, &|_| {}).unwrap_err();
        assert_eq!(error.code, "hash_mismatch");
        assert!(
            !store.installed("ner"),
            "auch der unversehrte Teil bleibt draußen"
        );
        assert!(!store.installed("de"));

        fs::write(&path, b"kein zip").unwrap();
        assert_eq!(
            store
                .import_package(&guard, &path, &|_| {})
                .unwrap_err()
                .code,
            "invalid_package"
        );
        fs::write(&path, zip_bytes(&[("andere/datei.txt", b"x")])).unwrap();
        assert_eq!(
            store
                .import_package(&guard, &path, &|_| {})
                .unwrap_err()
                .code,
            "package_empty"
        );
    }

    #[test]
    fn verify_removes_modified_models() {
        let wheel = zip_bytes(&[("de/de-3.8.0/config.cfg", b"[nlp]")]);
        let manifest = wheel_manifest(&wheel, &[("config.cfg", b"[nlp]")]);
        let package = zip_bytes(&[("ner/model.int8.onnx", b"onnx!"), ("de/de.whl", &wheel)]);
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("paket.zip");
        fs::write(&path, &package).unwrap();
        let models = root.path().join("models");
        let store = ModelStore::open(models.clone(), manifest.clone()).unwrap();
        {
            let guard = store.begin().unwrap();
            store.import_package(&guard, &path, &|_| {}).unwrap();
            assert!(store.verify(&guard).unwrap().is_empty());
        }
        assert!(store.overview().components[0].checked_at.is_some());

        // Same size but different content: only the hash fails.
        fs::write(models.join("ner/model.int8.onnx"), b"0nnx!").unwrap();
        let guard = store.begin().unwrap();
        let damaged = store.verify(&guard).unwrap();
        assert_eq!(
            damaged
                .iter()
                .map(|item| item.id.as_str())
                .collect::<Vec<_>>(),
            ["ner"]
        );
        assert!(!store.installed("ner"));
        assert!(store.installed("de"));
        drop(guard);

        // A different size is detected immediately at startup.
        fs::write(models.join("de/config.cfg"), b"[nlp] mehr").unwrap();
        let reopened = ModelStore::open(models, manifest).unwrap();
        assert!(!reopened.installed("de"));
    }

    #[test]
    fn source_is_remembered_and_validated() {
        let root = tempfile::tempdir().unwrap();
        let store =
            ModelStore::open(root.path().join("models"), Manifest::bundled().unwrap()).unwrap();
        assert_eq!(store.choice().source, "huggingface");
        store
            .set_choice(SourceChoice {
                source: "github".into(),
                custom_url: None,
            })
            .unwrap();
        assert_eq!(store.choice().source, "github");
        assert!(store
            .set_choice(SourceChoice {
                source: "custom".into(),
                custom_url: Some("http://x.example".into())
            })
            .is_err());
        assert!(store
            .set_choice(SourceChoice {
                source: "ftp".into(),
                custom_url: None
            })
            .is_err());
        store
            .set_choice(SourceChoice {
                source: "file".into(),
                custom_url: None,
            })
            .unwrap();
        assert!(store.overview().components[0].files[0].url.is_none());
    }

    /// Real download over the Internet (not in `check.mjs`):
    /// `UNNAME_MODELS_DIR=<ordner> cargo test real_download -- --ignored`.
    /// Downloads text recognition and German models from GitHub sources and
    /// validates them against the app manifest.
    #[test]
    #[ignore = "braucht Internet und lädt 0,6 GB"]
    fn real_download_from_github() {
        let root = std::env::var("UNNAME_MODELS_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| tempfile::tempdir().unwrap().keep().join("models"));
        let store = ModelStore::open(root, Manifest::bundled().unwrap()).unwrap();
        let choice = SourceChoice {
            source: "github".into(),
            custom_url: None,
        };
        let policy = DownloadPolicy::for_choice(store.manifest(), &choice).unwrap();
        let guard = store.begin().unwrap();
        let components = store.select(&["ocr".into(), "de".into()]).unwrap();
        tauri::async_runtime::block_on(store.download(
            &guard,
            &components,
            &policy,
            &|event| {
                if event.state != "downloading" {
                    eprintln!("{:?} {} {}", event.component, event.state, event.received);
                }
            },
            &|_| {},
        ))
        .expect("Download");
        assert!(store.installed("ocr") && store.installed("de"));
        assert!(store.verify(&guard).unwrap().is_empty());
    }

    /// A real model package from `scripts/models/modelle.py paket`:
    /// `UNNAME_MODEL_PACKAGE=<zip> UNNAME_MODELS_DIR=<ordner> cargo test
    /// real_model_package -- --ignored`.
    #[test]
    #[ignore = "braucht ein Modellpaket"]
    fn real_model_package() {
        let package =
            PathBuf::from(std::env::var("UNNAME_MODEL_PACKAGE").expect("UNNAME_MODEL_PACKAGE"));
        let root = PathBuf::from(std::env::var("UNNAME_MODELS_DIR").expect("UNNAME_MODELS_DIR"));
        let store = ModelStore::open(root, Manifest::bundled().unwrap()).unwrap();
        let guard = store.begin().unwrap();
        let installed = store
            .import_package(&guard, &package, &|event| {
                eprintln!("{:?} {} {}", event.component, event.state, event.received)
            })
            .expect("Import");
        assert!(!installed.is_empty());
        assert!(store.verify(&guard).unwrap().is_empty());
    }
}
