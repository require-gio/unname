//! The Tauri shell of unname.
//!
//! The shell does not process documents itself. It starts the sidecar, monitors
//! it, and decides whether the UI may operate. It reads files only to forward
//! them to the sidecar (`inputs.rs`, E8) and writes only what the sidecar has
//! provided as verified (`saving.rs`).

mod activity;
mod api_scheme;
mod app_window;
mod clipboard;
mod diagnostics;
#[cfg(target_os = "macos")]
mod download_service;
mod entry_lifecycle;
mod finder;
mod inputs;
mod menu;
mod models;
mod platform_security;
mod restoring;
mod saving;
mod settings_io;
mod sidecar;

use std::path::PathBuf;

use serde::Serialize;
use tauri::{Manager, RunEvent, WindowEvent};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_opener::OpenerExt;

use activity::BusyActivity;
use app_window::{WindowPreferences, WindowSettings};
use clipboard::{CopyKind, CopyOutcome};
use diagnostics::{DiagnosticEntry, DiagnosticsLog};
use inputs::{InputSelections, OpenedInputs, SelectionInfo};
use menu::MenuLabels;
use models::{ModelFailure, ModelStore, ModelsOverview, SourceChoice, VerifyReport};
use platform_security::{SecurityScopedBookmarks, SidecarRuntimeDirectories};
use restoring::{RestoreFailure, RestoredFiles};
use saving::{EntryOrigins, FileRef, SaveFailure, SaveOptions};
use settings_io::{NameListSources, SettingsFailure, TextFile};
use sidecar::{SidecarSnapshot, SidecarState, WebviewConnection};

/// State of the sidecar along with the derived locks.
#[tauri::command]
fn sidecar_status(state: tauri::State<'_, SidecarState>) -> SidecarSnapshot {
    state.snapshot()
}

/// Address for UI calls: the `unname-api` scheme, without tokens.
///
/// Returns `None` until the sidecar is ready — the UI cannot access it
/// otherwise.
#[tauri::command]
fn sidecar_connection(state: tauri::State<'_, SidecarState>) -> Option<WebviewConnection> {
    state.webview_connection()
}

/// New restart attempt after a final failure.
#[tauri::command]
fn restart_sidecar(state: tauri::State<'_, SidecarState>) {
    state.request_restart();
}

#[derive(Serialize)]
struct DiagnosticsReport {
    entries: Vec<DiagnosticEntry>,
    count: usize,
}

/// Diagnostic report from both sources — shell and sidecar.
#[tauri::command]
fn diagnostics_report(log: tauri::State<'_, DiagnosticsLog>) -> DiagnosticsReport {
    let entries = log.entries();
    DiagnosticsReport {
        count: entries.len(),
        entries,
    }
}

/// Open the system dialog to pick files or a folder (E8).
///
/// The UI learns the ID and filename, never the paths; submission is done via
/// `submit_inputs`. `None` means cancelled. The UI provides the title in the
/// app language.
#[tauri::command]
async fn pick_inputs(
    app: tauri::AppHandle,
    folder: bool,
    title: String,
) -> Result<Option<SelectionInfo>, String> {
    let Some(chosen) = inputs::pick(&app, folder, title).await? else {
        return Ok(None);
    };
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        inputs::remember(&handle.state::<InputSelections>(), &chosen)
    })
    .await
    .map(Some)
    .map_err(|_| "Die Auswahl konnte nicht gelesen werden.".to_string())
}

/// Process documents dropped on the dock or opened via “Open with” as a selection,
/// similar to a paste. Returns `None` when there are no pending documents.
#[tauri::command]
async fn take_opened_inputs(app: tauri::AppHandle) -> Result<Option<SelectionInfo>, String> {
    tauri::async_runtime::spawn_blocking(move || inputs::take_opened(&app))
        .await
        .map_err(|_| "Die geöffneten Dokumente konnten nicht gelesen werden.".to_string())
}

/// Submit a remembered selection as files of an entry to the sidecar.
#[tauri::command]
async fn submit_inputs(
    entry_id: String,
    selection: String,
    sidecar: tauri::State<'_, SidecarState>,
    selections: tauri::State<'_, InputSelections>,
    origins: tauri::State<'_, EntryOrigins>,
) -> Result<serde_json::Value, String> {
    inputs::submit(&sidecar, &selections, &origins, &entry_id, &selection).await
}

/// “Save all n files” (the sidecar prepares and commits; the
/// shell checks and publishes without collisions in the target folder.
///
/// `request` is the body for `POST /entries/{id}/save` (masking, review state
/// per file, confirmation). The shell determines the target folder: next to the
/// originals (`folder_name`), the last selected one, or — with `choose` or
/// without access — the system dialog. `None` means cancelled.
#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn save_entry(
    app: tauri::AppHandle,
    entry_id: String,
    request: serde_json::Value,
    folder_name: String,
    choose: bool,
    dialog_title: String,
    tag: String,
    last_directory: Option<String>,
    sidecar: tauri::State<'_, SidecarState>,
    directories: tauri::State<'_, SidecarRuntimeDirectories>,
    bookmarks: tauri::State<'_, SecurityScopedBookmarks>,
    origins: tauri::State<'_, EntryOrigins>,
) -> Result<Option<serde_json::Value>, SaveFailure> {
    saving::save(
        &app,
        &sidecar,
        &directories,
        &bookmarks,
        &origins,
        &entry_id,
        request,
        SaveOptions {
            folder_name,
            choose,
            dialog_title,
            tag,
            last_directory: last_directory.map(std::path::PathBuf::from),
        },
    )
    .await
}

/// “Edit again” after job TTL expiry: Re-read the original files.
#[tauri::command]
async fn reload_entry_files(
    app: tauri::AppHandle,
    entry_id: String,
    files: Vec<FileRef>,
    title: String,
    sidecar: tauri::State<'_, SidecarState>,
    origins: tauri::State<'_, EntryOrigins>,
) -> Result<serde_json::Value, String> {
    saving::reload(&app, &sidecar, &origins, &entry_id, files, title).await
}

/// “Save report”: Write content-free text to a chosen location.
#[tauri::command]
async fn save_text_file(
    app: tauri::AppHandle,
    title: String,
    name: String,
    content: String,
) -> Result<Option<String>, String> {
    saving::save_text(&app, title, name, content).await
}

/// “File from AI” restore: The first file of a selection from a dialog or
/// paste. The sidecar holds the new file in memory until `save_restored_file`
/// writes it.
#[tauri::command]
async fn restore_file(
    selection: String,
    entry_id: Option<String>,
    surrogates: bool,
    sidecar: tauri::State<'_, SidecarState>,
    selections: tauri::State<'_, InputSelections>,
    restored: tauri::State<'_, RestoredFiles>,
) -> Result<serde_json::Value, RestoreFailure> {
    restoring::restore(
        &sidecar,
        &selections,
        &restored,
        &selection,
        entry_id.as_deref(),
        surrogates,
    )
    .await
}

/// “Save as …” for a restored file. `None` means the operation was
/// cancelled. `confirm_cloud` confirms a cloud target that was previously held
/// back by `cloud_target`.
#[tauri::command]
async fn save_restored_file(
    app: tauri::AppHandle,
    id: String,
    title: String,
    confirm_cloud: Option<bool>,
    sidecar: tauri::State<'_, SidecarState>,
    restored: tauri::State<'_, RestoredFiles>,
) -> Result<Option<String>, RestoreFailure> {
    let confirm_cloud = confirm_cloud.unwrap_or(false);
    restoring::save(&app, &sidecar, &restored, &id, title, confirm_cloud).await
}

/// “Open”: only a file that `save_restored_file` just wrote.
#[tauri::command]
fn open_restored_file(
    app: tauri::AppHandle,
    path: String,
    restored: tauri::State<'_, RestoredFiles>,
) -> Result<(), String> {
    restoring::open(&app, &restored, &path)
}

/// Settings › Translation table: “Export backup” — encrypted with the
/// password, saved to a location chosen in the dialog. `None` means cancelled.
#[tauri::command]
async fn save_vault_backup(
    app: tauri::AppHandle,
    password: String,
    title: String,
    name: String,
    sidecar: tauri::State<'_, SidecarState>,
) -> Result<Option<String>, SettingsFailure> {
    settings_io::save_backup(&app, &sidecar, password, title, name).await
}

/// “Import”: choose and read the backup; replaces keys and table for this device (user confirms in UI).
#[tauri::command]
async fn import_vault_backup(
    app: tauri::AppHandle,
    password: String,
    title: String,
    filter: String,
    sidecar: tauri::State<'_, SidecarState>,
) -> Result<Option<serde_json::Value>, SettingsFailure> {
    settings_io::import_backup(&app, &sidecar, password, title, filter).await
}

/// Settings › Names & Terms: “Add list”.
#[tauri::command]
async fn add_name_list(
    app: tauri::AppHandle,
    kind: String,
    title: String,
    filter: String,
    sidecar: tauri::State<'_, SidecarState>,
    sources: tauri::State<'_, NameListSources>,
) -> Result<Option<serde_json::Value>, SettingsFailure> {
    settings_io::add_list(&app, &sidecar, &sources, kind, title, filter).await
}

/// “Refresh now”: re-read the source file (or select it via dialog).
#[tauri::command]
async fn refresh_name_list(
    app: tauri::AppHandle,
    id: String,
    title: String,
    filter: String,
    sidecar: tauri::State<'_, SidecarState>,
    sources: tauri::State<'_, NameListSources>,
) -> Result<Option<serde_json::Value>, SettingsFailure> {
    settings_io::refresh_list(&app, &sidecar, &sources, id, title, filter).await
}

/// Template “Import”: a YAML file as text.
#[tauri::command]
async fn read_template_file(
    app: tauri::AppHandle,
    title: String,
    filter: String,
) -> Result<Option<TextFile>, SettingsFailure> {
    settings_io::read_template(&app, title, filter).await
}

/// Text from the clipboard for “Paste from clipboard”.
///
/// Uses the shell instead of `navigator.clipboard.readText()`: the WebView prompts
/// on every read. The UI receives only this single read, without extra permissions.
/// Returns `None` when the clipboard is empty. Includes component, source, and state of
/// the loadable models.
#[tauri::command]
fn models_overview(store: tauri::State<'_, ModelStore>) -> ModelsOverview {
    store.overview()
}

/// “Where models are loaded from”. Recorded in the shell, not in the sidecar:
/// Connection policy is defined solely in `models.rs`.
#[tauri::command]
fn models_set_source(
    app: tauri::AppHandle,
    source: String,
    custom_url: Option<String>,
) -> Result<ModelsOverview, ModelFailure> {
    models::shell::set_source(&app, SourceChoice { source, custom_url })
}

/// Downloads the selected components in the background (`models://progress`).
#[tauri::command]
fn models_download(app: tauri::AppHandle, components: Vec<String>) -> Result<(), ModelFailure> {
    models::shell::start_download(app, components)
}

#[tauri::command]
fn models_cancel(app: tauri::AppHandle) {
    models::shell::cancel(&app);
}

#[tauri::command]
async fn models_import_package(
    app: tauri::AppHandle,
    title: String,
    filter: String,
) -> Result<Option<ModelsOverview>, ModelFailure> {
    models::shell::import_package(app, title, filter).await
}

#[tauri::command]
async fn models_verify(app: tauri::AppHandle) -> Result<VerifyReport, ModelFailure> {
    models::shell::verify(app).await
}

#[tauri::command]
fn models_remove(app: tauri::AppHandle, component: String) -> Result<ModelsOverview, ModelFailure> {
    models::shell::remove(&app, &component)
}

#[tauri::command]
fn read_clipboard_text(app: tauri::AppHandle) -> Option<String> {
    app.clipboard().read_text().ok()
}

/// Copy via the shell. `kind` decides whether the text stays on this device or
/// is cleared again (`clipboard.rs`).
///
/// Synchronous on purpose: Tauri runs the command on the main thread,
/// which is required by AppKit for clipboard operations.
#[tauri::command]
fn copy_text(app: tauri::AppHandle, text: String, kind: CopyKind) -> Result<CopyOutcome, String> {
    clipboard::copy(&app, &text, kind)
}

/// “n files done”, shown only when the window is not frontmost. Returns whether
/// a notification was sent.
#[tauri::command]
fn set_app_menu(app: tauri::AppHandle, labels: MenuLabels) -> Result<(), String> {
    if cfg!(target_os = "macos") {
        menu::apply(&app, &labels)
    } else {
        labels.validate()
    }
}

/// Progress of the ongoing detection on the dock icon; `None` = finished.
#[tauri::command]
fn set_job_progress(app: tauri::AppHandle, fraction: Option<f64>) -> Result<(), String> {
    app_window::set_progress(&app, fraction)
}

/// „n Dateien fertig", only if the window is not in front. Returns whether a
/// notification was sent.
#[tauri::command]
fn notify_finished(app: tauri::AppHandle, title: String, body: String) -> Result<bool, String> {
    app_window::notify_finished(&app, &title, &body)
}

/// Window settings, stored in the shell.
#[tauri::command]
fn window_settings(preferences: tauri::State<'_, WindowPreferences>) -> WindowSettings {
    preferences.get()
}

#[tauri::command]
fn set_window_settings(
    app: tauri::AppHandle,
    settings: WindowSettings,
    preferences: tauri::State<'_, WindowPreferences>,
) -> Result<WindowSettings, String> {
    app_window::apply(&app, &settings)?;
    preferences.set(settings)?;
    Ok(preferences.get())
}

/// Opens the folder of an exported file in the file manager.
#[tauri::command]
fn reveal_export(app: tauri::AppHandle, path: String) -> Result<(), String> {
    let target = PathBuf::from(&path);
    // Only existing targets: a fabricated path should stand out here and not
    // end up as an empty window for the user.
    let folder = if target.is_dir() {
        target.as_path()
    } else {
        target
            .parent()
            .filter(|parent| parent.is_dir())
            .ok_or_else(|| "Zu diesem Export gibt es keinen offenen Ordner.".to_string())?
    };
    app.opener()
        .open_path(folder.to_string_lossy().into_owned(), None::<&str>)
        .map_err(|error| format!("Der Ordner ließ sich nicht öffnen: {error}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_notification::init())
        .manage(SidecarState::default())
        .manage(DiagnosticsLog::new())
        .manage(InputSelections::default())
        .manage(OpenedInputs::default())
        .manage(EntryOrigins::default())
        .manage(RestoredFiles::default())
        .manage(NameListSources::default())
        .manage(BusyActivity::default())
        .manage(entry_lifecycle::EntryLifecycle::default())
        .register_asynchronous_uri_scheme_protocol(api_scheme::SCHEME, api_scheme::handle)
        .on_menu_event(|app, event| menu::handle_event(app, event.id().as_ref()))
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if entry_lifecycle::request_close(window) {
                    api.prevent_close();
                }
            }
            if let WindowEvent::DragDrop(drag) = event {
                inputs::handle_drag_drop(window.app_handle(), drag);
            }
        })
        .invoke_handler(tauri::generate_handler![
            sidecar_status,
            sidecar_connection,
            restart_sidecar,
            diagnostics_report,
            pick_inputs,
            take_opened_inputs,
            submit_inputs,
            save_entry,
            reload_entry_files,
            save_text_file,
            restore_file,
            save_restored_file,
            open_restored_file,
            save_vault_backup,
            import_vault_backup,
            add_name_list,
            refresh_name_list,
            read_template_file,
            models_overview,
            models_set_source,
            models_download,
            models_cancel,
            models_import_package,
            models_verify,
            models_remove,
            read_clipboard_text,
            copy_text,
            reveal_export,
            set_app_menu,
            set_job_progress,
            notify_finished,
            window_settings,
            set_window_settings,
            entry_lifecycle::set_entry_close_guard,
            entry_lifecycle::finish_entry_flush
        ])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            let app_data = app.path().app_data_dir()?;
            let app_cache = app.path().app_cache_dir()?;
            let bookmarks =
                SecurityScopedBookmarks::new(&app_data).map_err(std::io::Error::other)?;
            let directories = SidecarRuntimeDirectories::prepare(&app_cache, &app_data)
                .map_err(std::io::Error::other)?;
            let manifest = models::Manifest::for_this_platform().map_err(std::io::Error::other)?;
            let model_store = ModelStore::open(directories.models().to_path_buf(), manifest)
                .map_err(std::io::Error::other)?;
            let window_preferences = WindowPreferences::open(&app_data);
            if let Err(error) = app_window::apply(app.handle(), &window_preferences.get()) {
                log::warn!("Fenstereinstellungen nicht angewandt: {error}");
            }
            app.manage(window_preferences);
            app.manage(bookmarks);
            app.manage(directories);
            app.manage(model_store);
            sidecar::supervise(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Tauri-Anwendung ließ sich nicht bauen")
        .run(|app, event| {
            // On exit, no sidecar must remain: it would keep an open port and
            // loaded models without anyone controlling it.
            match event {
                RunEvent::ExitRequested { code, api, .. } => {
                    if entry_lifecycle::request_exit(app, code) {
                        api.prevent_exit();
                    }
                }
                RunEvent::Exit => {
                    app.state::<SidecarState>().shutdown();
                    app.state::<BusyActivity>().set(false);
                }
                #[cfg(any(target_os = "macos", target_os = "ios"))]
                RunEvent::Opened { urls } => inputs::handle_opened(app, &urls),
                _ => {}
            }
        });
}
