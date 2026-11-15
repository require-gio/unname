//! Operating-system-specific file security for the shell.
//!
//! On macOS, targets confirmed by the system dialog are persisted as
//! Security-Scoped Bookmarks. Additionally, the sidecar's temp area resides in
//! a private cache folder explicitly excluded from backups; so does the
//! encrypted draft of entries. Other desktop platforms keep the same API but
//! need no special handling for these two macOS mechanisms.

use std::fs;
use std::path::{Path, PathBuf};

use tauri_plugin_shell::process::Command;

const BOOKMARK_FILE: &str = "security-scoped-bookmarks-v1.json";
/// The `.noindex` extension keeps Spotlight out of the job temp: plaintext and
/// page images reside there during a job.
const JOB_TEMP_DIRECTORY: &str = "job-temp.noindex";
/// Legacy without `.noindex`. It contains only TTL-based temp data and is
/// deleted on startup.
const LEGACY_JOB_TEMP_DIRECTORY: &str = "job-temp";
/// Encrypted draft of entries: originals, extraction, detection. Stored in the
/// app data folder, not the cache — macOS may empty caches when low on space.
/// `.noindex` keeps Spotlight away, and it is excluded from backups: until the
/// keys are in the keychain, the key would otherwise sit next to the backup.
const DRAFTS_DIRECTORY: &str = "drafts.noindex";

/// Persistent shares for targets provided by a real system dialog.
pub struct SecurityScopedBookmarks {
    inner: bookmarks::Store,
}

impl SecurityScopedBookmarks {
    pub fn new(app_data_directory: &Path) -> Result<Self, String> {
        create_private_directory(app_data_directory)?;
        Ok(Self {
            inner: bookmarks::Store::new(app_data_directory.join(BOOKMARK_FILE))?,
        })
    }

    /// Records the dialog target. The scope is opened only for actual access,
    /// so a mere path choice does not occupy a system resource until process
    /// end.
    pub fn remember_dialog_target(&self, path: &Path) -> Result<PathBuf, String> {
        self.inner.remember_dialog_target(path)
    }

    /// Resolves a saved share again and begins its access.
    ///
    /// The passed path is only a search key; the path resolved by the bookmark
    /// is returned. An arbitrary, never confirmed path is therefore not shared
    /// on macOS.
    pub fn restore(&self, path: &Path) -> Result<PathBuf, String> {
        self.inner.restore(path)
    }

    pub fn release(&self, path: &Path) -> Result<(), String> {
        self.inner.release(path)
    }
}

/// Private Laufzeitorte des Sidecars innerhalb des App-Containers.
#[derive(Debug)]
pub struct SidecarRuntimeDirectories {
    job_temp: PathBuf,
    state: PathBuf,
    drafts: PathBuf,
    /// Loaded and verified models (`models.rs`). They can be reloaded at any
    /// time, so they do not belong in the backup.
    models: PathBuf,
}

impl SidecarRuntimeDirectories {
    pub fn prepare(app_cache_directory: &Path, app_data_directory: &Path) -> Result<Self, String> {
        remove_legacy_job_temp(app_cache_directory);
        let job_temp = app_cache_directory.join(JOB_TEMP_DIRECTORY);
        let state = app_data_directory.join("sidecar-state");
        let drafts = app_data_directory.join(DRAFTS_DIRECTORY);
        let models = app_data_directory.join("models");
        create_private_directory(&job_temp)?;
        create_private_directory(&state)?;
        create_private_directory(&drafts)?;
        create_private_directory(&models)?;
        backup::exclude(&job_temp)?;
        backup::exclude(&drafts)?;
        backup::exclude(&models)?;
        Ok(Self {
            job_temp,
            state,
            drafts,
            models,
        })
    }

    #[cfg(test)]
    fn drafts(&self) -> &Path {
        &self.drafts
    }

    pub fn job_temp(&self) -> &Path {
        &self.job_temp
    }

    pub fn models(&self) -> &Path {
        &self.models
    }

    /// Python `tempfile` evaluates these variables before the first job. The
    /// existing JobManager continues to create a random folder per process
    /// underneath and cleans up orphaned legacy.
    pub fn configure(&self, command: Command) -> Command {
        let command = command.env("PRESIDIO_STATE_DIR", &self.state).args([
            "--models-dir".to_string(),
            self.models.to_string_lossy().into_owned(),
            "--drafts-dir".to_string(),
            self.drafts.to_string_lossy().into_owned(),
        ]);
        #[cfg(windows)]
        {
            command
                .env("TEMP", &self.job_temp)
                .env("TMP", &self.job_temp)
        }
        #[cfg(not(windows))]
        {
            command.env("TMPDIR", &self.job_temp)
        }
    }
}

fn remove_legacy_job_temp(app_cache_directory: &Path) {
    let legacy = app_cache_directory.join(LEGACY_JOB_TEMP_DIRECTORY);
    // Only follow real directories; do not follow symlinks at this point.
    let is_directory = fs::symlink_metadata(&legacy)
        .map(|metadata| metadata.is_dir())
        .unwrap_or(false);
    if is_directory {
        if let Err(error) = fs::remove_dir_all(&legacy) {
            log::warn!("Altes Job-Temp ließ sich nicht löschen: {error}");
        }
    }
}

fn create_private_directory(path: &Path) -> Result<(), String> {
    fs::create_dir_all(path).map_err(|error| {
        format!(
            "Privates Anwendungsverzeichnis {} konnte nicht angelegt werden: {error}",
            path.display()
        )
    })?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|error| {
            format!(
                "Rechte für das private Anwendungsverzeichnis {} konnten nicht gesetzt werden: {error}",
                path.display()
            )
        })?;
    }
    Ok(())
}

#[cfg(target_os = "macos")]
mod backup {
    use std::path::Path;

    use objc2_foundation::{NSNumber, NSURLIsExcludedFromBackupKey, NSURL};

    pub fn exclude(path: &Path) -> Result<(), String> {
        let url = NSURL::from_directory_path(path).ok_or_else(|| {
            format!(
                "Der private Pfad {} ist keine gültige Datei-URL.",
                path.display()
            )
        })?;
        let excluded = NSNumber::new_bool(true);

        // SAFETY: NSURLIsExcludedFromBackupKey expects a boolean NSNumber
        // according to Foundation. We pass exactly this type; NSURL and value
        // remain alive for the synchronous Objective-C message.
        unsafe { url.setResourceValue_forKey_error(Some(&excluded), NSURLIsExcludedFromBackupKey) }
            .map_err(|error| {
                format!(
                    "Private Daten ({}) konnten nicht von macOS-Backups ausgeschlossen werden: {error}",
                    path.display()
                )
            })
    }
}

#[cfg(not(target_os = "macos"))]
mod backup {
    use std::path::Path;

    /// Windows and Linux do not know the NSURL resource key. The private temp
    /// folder and its restrictive rights still apply there.
    pub fn exclude(_path: &Path) -> Result<(), String> {
        Ok(())
    }
}

#[cfg(target_os = "macos")]
mod bookmarks {
    use std::collections::BTreeMap;
    use std::fs::{self, OpenOptions};
    use std::io::Write;
    use std::path::{Path, PathBuf};
    use std::sync::Mutex;

    use objc2::rc::Retained;
    use objc2_foundation::{
        NSData, NSURLBookmarkCreationOptions, NSURLBookmarkResolutionOptions, NSURL,
    };
    use serde::{Deserialize, Serialize};

    const FORMAT_VERSION: u32 = 1;
    const MAX_BOOKMARKS: usize = 256;
    const MAX_STORE_BYTES: u64 = 16 * 1024 * 1024;

    #[derive(Clone, Debug, Deserialize, Serialize)]
    struct BookmarkEntry {
        data: Vec<u8>,
        /// `false` comes exclusively from unsandboxed debug builds. A release
        /// build requires the dialog again for such development data.
        #[serde(default)]
        security_scoped: bool,
        /// Save panels may return a target name that does not yet exist. In
        /// this case, the existing parent folder is the bookmarkable resource,
        /// and the confirmed file name is stored relative to it.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        relative_name: Option<String>,
        updated_at_ms: i64,
    }

    #[derive(Clone, Debug, Deserialize, Serialize)]
    struct BookmarkDocument {
        version: u32,
        entries: BTreeMap<String, BookmarkEntry>,
    }

    impl Default for BookmarkDocument {
        fn default() -> Self {
            Self {
                version: FORMAT_VERSION,
                entries: BTreeMap::new(),
            }
        }
    }

    struct ActiveScope {
        url: Retained<NSURL>,
        started: bool,
    }

    impl Drop for ActiveScope {
        fn drop(&mut self) {
            if self.started {
                // SAFETY: Every `true` return value from `startAccessing` is
                // balanced exactly once by this drop.
                unsafe { self.url.stopAccessingSecurityScopedResource() };
            }
        }
    }

    struct State {
        document: BookmarkDocument,
        active: BTreeMap<String, ActiveScope>,
    }

    pub struct Store {
        file: PathBuf,
        state: Mutex<State>,
    }

    impl Store {
        pub fn new(file: PathBuf) -> Result<Self, String> {
            let document = read_document(&file)?;
            Ok(Self {
                file,
                state: Mutex::new(State {
                    document,
                    active: BTreeMap::new(),
                }),
            })
        }

        pub fn remember_dialog_target(&self, path: &Path) -> Result<PathBuf, String> {
            let key = path_key(path)?;
            let resource = bookmark_resource(path)?;
            let bookmark = create_bookmark(&resource.url)?;
            let resolved = resolve_bookmark(&bookmark.data, bookmark.security_scoped)?;
            let _resolved_path =
                resolved_target_path(&resolved.url, resource.relative_name.as_deref())?;

            let mut state = self.state.lock().map_err(|_| bookmark_lock_error())?;
            let previous = state.document.clone();
            state.document.entries.insert(
                key.clone(),
                BookmarkEntry {
                    data: bookmark.data,
                    security_scoped: bookmark.security_scoped,
                    relative_name: resource.relative_name,
                    updated_at_ms: chrono::Utc::now().timestamp_millis(),
                },
            );
            let pruned = prune_old_entries(&mut state.document, &key);
            for pruned_key in pruned {
                state.active.remove(&pruned_key);
            }
            if let Err(error) = write_document(&self.file, &state.document) {
                state.document = previous;
                return Err(error);
            }

            // For the current session, the path provided by the dialog remains
            // the stable search key. Only a subsequent `restore` returns the
            // potentially newly resolved bookmark path after a move.
            Ok(path.to_path_buf())
        }

        pub fn restore(&self, path: &Path) -> Result<PathBuf, String> {
            let key = path_key(path)?;
            let mut state = self.state.lock().map_err(|_| bookmark_lock_error())?;
            if let Some(active) = state.active.get(&key) {
                let relative_name = state
                    .document
                    .entries
                    .get(&key)
                    .and_then(|entry| entry.relative_name.as_deref());
                return resolved_target_path(&active.url, relative_name);
            }

            let entry = state.document.entries.get(&key).cloned().ok_or_else(|| {
                "Für diesen Pfad liegt keine Bestätigung aus einem Systemdialog vor.".to_string()
            })?;
            if !entry.security_scoped && !cfg!(debug_assertions) {
                return Err(
                    "Die gespeicherte Entwicklungsfreigabe ist kein Security-Scoped Bookmark; Ziel bitte neu wählen."
                        .to_string(),
                );
            }
            let mut security_scoped = entry.security_scoped;
            let resolved = resolve_bookmark(&entry.data, security_scoped)?;
            let (resolved, resolved_path) =
                resolve_final_target(resolved, entry.relative_name.as_deref(), |stale| {
                    let refreshed = create_bookmark(&stale.url)?;
                    let previous = state.document.clone();
                    state.document.entries.insert(
                        key.clone(),
                        BookmarkEntry {
                            data: refreshed.data.clone(),
                            security_scoped: refreshed.security_scoped,
                            relative_name: entry.relative_name.clone(),
                            updated_at_ms: chrono::Utc::now().timestamp_millis(),
                        },
                    );
                    if let Err(error) = write_document(&self.file, &state.document) {
                        state.document = previous;
                        return Err(error);
                    }
                    security_scoped = refreshed.security_scoped;
                    resolve_bookmark(&refreshed.data, security_scoped)
                })?;
            let started = begin_security_scoped_access(security_scoped, || unsafe {
                resolved.url.startAccessingSecurityScopedResource()
            })?;
            state.active.insert(
                key,
                ActiveScope {
                    url: resolved.url,
                    started,
                },
            );
            Ok(resolved_path)
        }

        pub fn release(&self, path: &Path) -> Result<(), String> {
            let key = path_key(path)?;
            self.state
                .lock()
                .map_err(|_| bookmark_lock_error())?
                .active
                .remove(&key);
            Ok(())
        }
    }

    /// Updates a stale bookmark and derives the target path only from the final
    /// URL. A moved folder must never return the old path from the first
    /// resolution.
    fn resolve_final_target(
        mut resolved: ResolvedBookmark,
        relative_name: Option<&str>,
        refresh: impl FnOnce(&ResolvedBookmark) -> Result<ResolvedBookmark, String>,
    ) -> Result<(ResolvedBookmark, PathBuf), String> {
        if resolved.stale {
            resolved = refresh(&resolved)?;
        }
        let path = resolved_target_path(&resolved.url, relative_name)?;
        Ok((resolved, path))
    }

    /// Initiates a real security scope or denies access.
    ///
    /// Foundation does not report a missing scope as an NSError, but via the
    /// boolean return value. Therefore, `false` must never be treated as a
    /// successful access for a bookmark saved as security-scoped. The closure
    /// makes this contract testable without a real app sandbox process.
    fn begin_security_scoped_access(
        security_scoped: bool,
        start: impl FnOnce() -> bool,
    ) -> Result<bool, String> {
        if !security_scoped {
            return Ok(false);
        }
        if start() {
            Ok(true)
        } else {
            Err(
                "macOS hat den Zugriff auf das Security-Scoped Bookmark verweigert; Ziel bitte neu wählen."
                    .to_string(),
            )
        }
    }

    struct ResolvedBookmark {
        url: Retained<NSURL>,
        stale: bool,
    }

    struct CreatedBookmark {
        data: Vec<u8>,
        security_scoped: bool,
    }

    struct BookmarkResource {
        url: Retained<NSURL>,
        relative_name: Option<String>,
    }

    fn create_bookmark(url: &NSURL) -> Result<CreatedBookmark, String> {
        let security_scoped = url
            .bookmarkDataWithOptions_includingResourceValuesForKeys_relativeToURL_error(
                NSURLBookmarkCreationOptions::WithSecurityScope,
                None,
                None,
            );
        match security_scoped {
            Ok(data) => Ok(CreatedBookmark {
                data: data.to_vec(),
                security_scoped: true,
            }),
            Err(error) if cfg!(debug_assertions) => {
                // Cargo and Tauri dev processes carry no sandbox signature and
                // therefore cannot create a security scope. The standard debug
                // bookmark is marked and never accepted in releases.
                url.bookmarkDataWithOptions_includingResourceValuesForKeys_relativeToURL_error(
                    NSURLBookmarkCreationOptions::empty(),
                    None,
                    None,
                )
                .map(|data| CreatedBookmark {
                    data: data.to_vec(),
                    security_scoped: false,
                })
                .map_err(|fallback| {
                    format!(
                        "Bookmark konnte nicht erzeugt werden (Security-Scope: {error}; Debug-Fallback: {fallback})."
                    )
                })
            }
            Err(error) => Err(format!(
                "Security-Scoped Bookmark konnte nicht erzeugt werden: {error}"
            )),
        }
    }

    fn resolve_bookmark(data: &[u8], security_scoped: bool) -> Result<ResolvedBookmark, String> {
        let data = NSData::with_bytes(data);
        let mut stale = objc2::runtime::Bool::NO;
        let options = if security_scoped {
            NSURLBookmarkResolutionOptions::WithSecurityScope
                | NSURLBookmarkResolutionOptions::WithoutUI
        } else {
            NSURLBookmarkResolutionOptions::WithoutUI
        };
        // SAFETY: `stale` is a valid, writable bool pointer that lives beyond
        // the synchronous Foundation call.
        let url = unsafe {
            NSURL::URLByResolvingBookmarkData_options_relativeToURL_bookmarkDataIsStale_error(
                &data, options, None, &mut stale,
            )
        }
        .map_err(|error| {
            format!("Security-Scoped Bookmark konnte nicht aufgelöst werden: {error}")
        })?;
        Ok(ResolvedBookmark {
            url,
            stale: stale.as_bool(),
        })
    }

    fn file_url(path: &Path) -> Result<Retained<NSURL>, String> {
        NSURL::from_file_path(path)
            .ok_or_else(|| format!("{} ist keine gültige Datei-URL.", path.display()))
    }

    fn bookmark_resource(path: &Path) -> Result<BookmarkResource, String> {
        if path.exists() {
            return Ok(BookmarkResource {
                url: file_url(path)?,
                relative_name: None,
            });
        }

        let parent = path
            .parent()
            .filter(|parent| parent.is_dir())
            .ok_or_else(|| {
                "Der Elternordner des bestätigten Exportziels existiert nicht.".to_string()
            })?;
        let name = path
            .file_name()
            .and_then(|name| name.to_str())
            .filter(|name| !name.is_empty())
            .ok_or_else(|| {
                "Das bestätigte Exportziel hat keinen gültigen Dateinamen.".to_string()
            })?;
        let url = NSURL::from_directory_path(parent)
            .ok_or_else(|| format!("{} ist keine gültige Ordner-URL.", parent.display()))?;
        Ok(BookmarkResource {
            url,
            relative_name: Some(name.to_string()),
        })
    }

    fn resolved_target_path(url: &NSURL, relative_name: Option<&str>) -> Result<PathBuf, String> {
        let resolved = url
            .to_file_path()
            .ok_or_else(|| "Das macOS-Bookmark enthält keinen Dateipfad.".to_string())?;
        Ok(match relative_name {
            Some(name) => resolved.join(name),
            None => resolved,
        })
    }

    fn path_key(path: &Path) -> Result<String, String> {
        let absolute = if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir()
                .map_err(|error| format!("Arbeitsverzeichnis ist nicht lesbar: {error}"))?
                .join(path)
        };
        absolute
            .to_str()
            .map(str::to_owned)
            .ok_or_else(|| "macOS-Dateipfad ist nicht als Unicode darstellbar.".to_string())
    }

    fn read_document(file: &Path) -> Result<BookmarkDocument, String> {
        if !file.exists() {
            return Ok(BookmarkDocument::default());
        }
        let metadata = fs::metadata(file).map_err(|error| {
            format!(
                "Bookmark-Speicher {} ist nicht lesbar: {error}",
                file.display()
            )
        })?;
        if metadata.len() > MAX_STORE_BYTES {
            return Err(
                "Bookmark-Speicher ist unerwartet groß; Zugriff wird verweigert.".to_string(),
            );
        }
        let bytes = fs::read(file).map_err(|error| {
            format!(
                "Bookmark-Speicher {} ist nicht lesbar: {error}",
                file.display()
            )
        })?;
        let document: BookmarkDocument = serde_json::from_slice(&bytes)
            .map_err(|error| format!("Bookmark-Speicher ist beschädigt: {error}"))?;
        if document.version != FORMAT_VERSION {
            return Err(format!(
                "Bookmark-Speicher hat die unbekannte Version {}.",
                document.version
            ));
        }
        if document.entries.len() > MAX_BOOKMARKS {
            return Err("Bookmark-Speicher enthält unerwartet viele Einträge.".to_string());
        }
        Ok(document)
    }

    fn write_document(file: &Path, document: &BookmarkDocument) -> Result<(), String> {
        let parent = file
            .parent()
            .ok_or_else(|| "Bookmark-Speicher hat kein Elternverzeichnis.".to_string())?;
        super::create_private_directory(parent)?;
        let bytes = serde_json::to_vec(document)
            .map_err(|error| format!("Bookmark-Speicher ist nicht serialisierbar: {error}"))?;
        if bytes.len() as u64 > MAX_STORE_BYTES {
            return Err("Bookmark-Speicher überschreitet die zulässige Größe.".to_string());
        }

        let temporary = file.with_extension(format!("tmp-{}", std::process::id()));
        let mut options = OpenOptions::new();
        options.create(true).truncate(true).write(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut output = options.open(&temporary).map_err(|error| {
            format!("Temporärer Bookmark-Speicher konnte nicht angelegt werden: {error}")
        })?;
        output
            .write_all(&bytes)
            .and_then(|()| output.sync_all())
            .map_err(|error| {
                format!("Bookmark-Speicher konnte nicht geschrieben werden: {error}")
            })?;
        fs::rename(&temporary, file).map_err(|error| {
            format!("Bookmark-Speicher konnte nicht atomar ersetzt werden: {error}")
        })?;

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(file, fs::Permissions::from_mode(0o600)).map_err(|error| {
                format!("Rechte des Bookmark-Speichers konnten nicht gesetzt werden: {error}")
            })?;
        }
        Ok(())
    }

    fn prune_old_entries(document: &mut BookmarkDocument, current: &str) -> Vec<String> {
        let mut removed = Vec::new();
        while document.entries.len() > MAX_BOOKMARKS {
            let oldest = document
                .entries
                .iter()
                .filter(|(key, _)| key.as_str() != current)
                .min_by_key(|(_, entry)| entry.updated_at_ms)
                .map(|(key, _)| key.clone());
            let Some(oldest) = oldest else {
                break;
            };
            document.entries.remove(&oldest);
            removed.push(oldest);
        }
        removed
    }

    fn bookmark_lock_error() -> String {
        "Bookmark-Speicher ist nach einem internen Fehler gesperrt.".to_string()
    }

    #[cfg(test)]
    mod tests {
        use std::fs;
        use std::os::unix::fs::PermissionsExt;

        use super::*;

        #[test]
        fn new_save_panel_target_bookmarks_parent_folder() {
            let root = tempfile::tempdir().expect("tempdir");
            let target = root.path().join("noch-nicht-vorhanden.pdf");

            let resource = bookmark_resource(&target).expect("bookmark resource");

            assert_eq!(
                resource.url.to_file_path().expect("resource path"),
                root.path()
            );
            assert_eq!(
                resource.relative_name.as_deref(),
                Some("noch-nicht-vorhanden.pdf")
            );
            assert_eq!(
                resolved_target_path(&resource.url, resource.relative_name.as_deref())
                    .expect("resolved target"),
                target
            );
        }

        #[test]
        fn bookmark_document_is_persisted_atomically_with_0600() {
            let root = tempfile::tempdir().expect("tempdir");
            let file = root.path().join("bookmarks.json");
            let mut document = BookmarkDocument::default();
            document.entries.insert(
                "/tmp/ausgabe.txt".to_string(),
                BookmarkEntry {
                    data: vec![0, 1, 2, 255],
                    security_scoped: true,
                    relative_name: Some("ausgabe.txt".to_string()),
                    updated_at_ms: 42,
                },
            );

            write_document(&file, &document).expect("write document");
            let loaded = read_document(&file).expect("read document");

            assert_eq!(loaded.entries["/tmp/ausgabe.txt"].data, vec![0, 1, 2, 255]);
            assert_eq!(
                loaded.entries["/tmp/ausgabe.txt"].relative_name.as_deref(),
                Some("ausgabe.txt")
            );
            assert_eq!(
                fs::metadata(file).expect("metadata").permissions().mode() & 0o777,
                0o600
            );
        }

        #[test]
        fn unknown_store_version_is_rejected_fail_closed() {
            let root = tempfile::tempdir().expect("tempdir");
            let file = root.path().join("bookmarks.json");
            fs::write(&file, br#"{"version":99,"entries":{}}"#).expect("fixture");

            let error = read_document(&file).expect_err("unknown version");

            assert!(error.contains("unbekannte Version 99"));
        }

        #[test]
        fn store_limits_old_grants() {
            let mut document = BookmarkDocument::default();
            for index in 0..=MAX_BOOKMARKS {
                document.entries.insert(
                    format!("/tmp/{index:03}"),
                    BookmarkEntry {
                        data: vec![index as u8],
                        security_scoped: true,
                        relative_name: None,
                        updated_at_ms: index as i64,
                    },
                );
            }

            let removed = prune_old_entries(&mut document, "/tmp/256");

            assert_eq!(removed, vec!["/tmp/000"]);
            assert_eq!(document.entries.len(), MAX_BOOKMARKS);
            assert!(document.entries.contains_key("/tmp/256"));
        }

        #[test]
        fn security_scoped_start_must_be_confirmed() {
            let error = begin_security_scoped_access(true, || false)
                .expect_err("abgelehnter Security-Scope");

            assert!(error.contains("Zugriff"));
            assert!(error.contains("neu wählen"));
        }

        #[test]
        fn debug_bookmark_starts_no_security_scope() {
            let mut called = false;

            let started = begin_security_scoped_access(false, || {
                called = true;
                true
            })
            .expect("debug bookmark");

            assert!(!started);
            assert!(!called);
        }

        #[test]
        fn stale_refresh_returns_final_resolved_path() {
            let root = tempfile::tempdir().expect("tempdir");
            let old_directory = root.path().join("alt");
            let new_directory = root.path().join("neu");
            fs::create_dir_all(&old_directory).expect("old directory");
            fs::create_dir_all(&new_directory).expect("new directory");
            let initial = ResolvedBookmark {
                url: NSURL::from_directory_path(&old_directory).expect("old URL"),
                stale: true,
            };

            let (_resolved, target) =
                resolve_final_target(initial, Some("ausgabe.pdf"), |_stale| {
                    Ok(ResolvedBookmark {
                        url: NSURL::from_directory_path(&new_directory).expect("new URL"),
                        stale: false,
                    })
                })
                .expect("refreshed target");

            assert_eq!(target, new_directory.join("ausgabe.pdf"));
        }
    }
}

#[cfg(not(target_os = "macos"))]
mod bookmarks {
    use std::path::{Path, PathBuf};

    /// On non-macOS systems, the system dialog itself is the authorization;
    /// there are no NSURL scopes or bookmark files there.
    pub struct Store;

    impl Store {
        pub fn new(_file: PathBuf) -> Result<Self, String> {
            Ok(Self)
        }

        pub fn remember_dialog_target(&self, path: &Path) -> Result<PathBuf, String> {
            Ok(path.to_path_buf())
        }

        pub fn restore(&self, path: &Path) -> Result<PathBuf, String> {
            Ok(path.to_path_buf())
        }

        pub fn release(&self, _path: &Path) -> Result<(), String> {
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::*;

    #[test]
    fn job_temp_is_private_and_dedicated() {
        let root = tempfile::tempdir().expect("tempdir");
        let prepared =
            SidecarRuntimeDirectories::prepare(root.path(), root.path()).expect("job temp");

        assert_eq!(prepared.job_temp(), root.path().join(JOB_TEMP_DIRECTORY));
        assert!(prepared.job_temp().is_dir());

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(prepared.job_temp())
                    .expect("metadata")
                    .permissions()
                    .mode()
                    & 0o777,
                0o700
            );
        }
    }

    #[test]
    fn job_temp_is_excluded_from_spotlight_and_legacy_is_removed() {
        let root = tempfile::tempdir().expect("tempdir");
        let legacy = root.path().join(LEGACY_JOB_TEMP_DIRECTORY);
        fs::create_dir_all(legacy.join("presidio-jobs-1-abc")).expect("legacy");
        fs::write(legacy.join("presidio-jobs-1-abc/source.pdf"), b"%PDF").expect("legacy file");

        let prepared =
            SidecarRuntimeDirectories::prepare(root.path(), root.path()).expect("job temp");

        assert!(prepared
            .job_temp()
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.ends_with(".noindex")));
        assert!(!legacy.exists());
    }

    #[cfg(unix)]
    #[test]
    fn symlink_as_legacy_job_temp_is_not_followed() {
        let root = tempfile::tempdir().expect("tempdir");
        let elsewhere = tempfile::tempdir().expect("elsewhere");
        fs::write(elsewhere.path().join("bleibt.txt"), b"x").expect("file");
        std::os::unix::fs::symlink(
            elsewhere.path(),
            root.path().join(LEGACY_JOB_TEMP_DIRECTORY),
        )
        .expect("symlink");

        SidecarRuntimeDirectories::prepare(root.path(), root.path()).expect("job temp");

        assert!(elsewhere.path().join("bleibt.txt").exists());
    }

    #[test]
    fn drafts_are_private_in_app_data() {
        let cache = tempfile::tempdir().expect("cache");
        let data = tempfile::tempdir().expect("data");
        let prepared =
            SidecarRuntimeDirectories::prepare(cache.path(), data.path()).expect("drafts");

        // App data, not cache: macOS may clear caches when disk space is low.
        assert_eq!(prepared.drafts(), data.path().join(DRAFTS_DIRECTORY));
        assert!(prepared.drafts().is_dir());
        assert!(!cache.path().join(DRAFTS_DIRECTORY).exists());
        assert!(prepared
            .drafts()
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.ends_with(".noindex")));

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(prepared.drafts())
                    .expect("metadata")
                    .permissions()
                    .mode()
                    & 0o777,
                0o700
            );
        }
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn drafts_carry_backup_exclusion() {
        let root = tempfile::tempdir_in(env!("CARGO_MANIFEST_DIR")).expect("tempdir");
        let prepared =
            SidecarRuntimeDirectories::prepare(root.path(), root.path()).expect("drafts");
        let output = std::process::Command::new("/usr/bin/xattr")
            .args(["-px", "com.apple.metadata:com_apple_backup_excludeItem"])
            .arg(prepared.drafts())
            .output()
            .expect("xattr");

        assert!(output.status.success());
        assert!(String::from_utf8_lossy(&output.stdout).contains("62 70 6C 69 73 74"));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn job_temp_carries_backup_exclusion() {
        // The system-wide TMPDIR already lies outside the backup set and does
        // not persist this resource key depending on the volume. A directory on
        // the normal data volume tests the actual property.
        let root = tempfile::tempdir_in(env!("CARGO_MANIFEST_DIR")).expect("tempdir");
        let prepared =
            SidecarRuntimeDirectories::prepare(root.path(), root.path()).expect("job temp");
        let output = std::process::Command::new("/usr/bin/xattr")
            .args(["-px", "com.apple.metadata:com_apple_backup_excludeItem"])
            .arg(prepared.job_temp())
            .output()
            .expect("xattr");

        assert!(output.status.success());
        assert!(String::from_utf8_lossy(&output.stdout).contains("62 70 6C 69 73 74"));
    }

    #[cfg(all(target_os = "macos", debug_assertions))]
    #[test]
    fn debug_test_host_persists_actual_bookmark_kind() {
        let root = tempfile::tempdir().expect("tempdir");
        let target = root.path().join("noch-nicht-vorhanden.txt");
        let resolved_target = fs::canonicalize(root.path())
            .expect("canonical root")
            .join("noch-nicht-vorhanden.txt");
        let data = root.path().join("data");

        {
            let store = SecurityScopedBookmarks::new(&data).expect("bookmark store");
            assert_eq!(
                store
                    .remember_dialog_target(&target)
                    .expect("debug bookmark"),
                target
            );
        }

        let reopened = SecurityScopedBookmarks::new(&data).expect("reopen store");
        assert_eq!(
            reopened.restore(&target).expect("restore bookmark"),
            resolved_target
        );
        reopened.release(&target).expect("release scope");

        let document: serde_json::Value =
            serde_json::from_slice(&fs::read(data.join(BOOKMARK_FILE)).expect("bookmark file"))
                .expect("bookmark json");
        // Outside a signed app, macOS Security-Scoped Bookmarks normally reject
        // and the debug fallback is marked `false`. On explicitly unlocked test
        // hosts, Foundation may still allow creation. In both cases, the
        // actually used type must be persisted, not an assumption about the
        // test host.
        assert!(
            document["entries"][target.to_string_lossy().as_ref()]["security_scoped"].is_boolean()
        );
    }

    #[cfg(not(target_os = "macos"))]
    #[test]
    fn fallback_keeps_dialog_path_unchanged() {
        let root = tempfile::tempdir().expect("tempdir");
        let store = SecurityScopedBookmarks::new(root.path()).expect("bookmark facade");
        let target = root.path().join("ausgabe.txt");
        assert_eq!(
            store
                .remember_dialog_target(&target)
                .expect("fallback target"),
            target
        );
        assert_eq!(store.restore(&target).expect("fallback restore"), target);
        store.release(&target).expect("fallback release");
    }
}
