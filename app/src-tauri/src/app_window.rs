//! The system window: dock progress, notifications, and screen sharing.
//!
//! - **Progress:** The UI queries the job progress anyway and forwards the
//!   fraction; the dock icon displays it as a bar.
//! - **Notification:** „n Dateien fertig", only when the window is not in the
//!   foreground, and without the filename: notifications land in the
//!   notification center and on the lock screen.
//! - **Screen sharing:** On request, macOS hides the window in recordings
//!   (`NSWindowSharingNone`). Not every recording respects this; the settings
//!   text states that. The shell remembers the choice, not the sidecar: it must
//!   apply before the sidecar starts.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::window::{ProgressBarState, ProgressBarStatus};
use tauri::{AppHandle, Manager, Runtime, WebviewWindow};
use tauri_plugin_notification::NotificationExt;

const SETTINGS_FILE: &str = "window.json";
const MAIN_WINDOW: &str = "main";

/// Window settings, stored in the shell.
#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct WindowSettings {
    /// Hide window in screen shares and screenshots.
    #[serde(default)]
    pub hide_from_capture: bool,
}

pub struct WindowPreferences {
    path: PathBuf,
    current: Mutex<WindowSettings>,
}

impl WindowPreferences {
    /// Reads the stored choice. If it is missing or unreadable, defaults apply
    /// (all off).
    pub fn open(app_data: &Path) -> Self {
        let path = app_data.join(SETTINGS_FILE);
        let current = fs::read(&path)
            .ok()
            .and_then(|raw| serde_json::from_slice(&raw).ok())
            .unwrap_or_default();
        Self {
            path,
            current: Mutex::new(current),
        }
    }

    pub fn get(&self) -> WindowSettings {
        self.current
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
            .clone()
    }

    pub fn set(&self, settings: WindowSettings) -> Result<(), String> {
        let content = serde_json::to_vec_pretty(&settings).map_err(|error| error.to_string())?;
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        crate::saving::write_atomically(&self.path, &content)?;
        *self
            .current
            .lock()
            .unwrap_or_else(|poison| poison.into_inner()) = settings;
        Ok(())
    }
}

fn main_window<R: Runtime>(app: &AppHandle<R>) -> Option<WebviewWindow<R>> {
    app.get_webview_window(MAIN_WINDOW)
}

/// Applies the stored choice to the window (on startup and after every change).
pub fn apply<R: Runtime>(app: &AppHandle<R>, settings: &WindowSettings) -> Result<(), String> {
    let window = main_window(app).ok_or("Kein Hauptfenster.")?;
    window
        .set_content_protected(settings.hide_from_capture)
        .map_err(|error| error.to_string())
}

/// Fraction 0…1 as percentage for the dock; clamped outside the range.
fn percent(fraction: f64) -> u64 {
    if fraction.is_nan() {
        return 0;
    }
    (fraction.clamp(0.0, 1.0) * 100.0).round() as u64
}

/// Zeigt den Fortschritt am Dock-Symbol; `None` nimmt den Balken weg.
pub fn set_progress<R: Runtime>(app: &AppHandle<R>, fraction: Option<f64>) -> Result<(), String> {
    let window = main_window(app).ok_or("Kein Hauptfenster.")?;
    let state = match fraction {
        Some(fraction) => ProgressBarState {
            status: Some(ProgressBarStatus::Normal),
            progress: Some(percent(fraction)),
        },
        None => ProgressBarState {
            status: Some(ProgressBarStatus::None),
            progress: None,
        },
    };
    window
        .set_progress_bar(state)
        .map_err(|error| error.to_string())
}

/// Short, single-line, no control characters: the text comes from the
/// dictionary and must not be able to hold a filename that breaks a line.
fn valid_text(text: &str) -> bool {
    let length = text.chars().count();
    (1..=120).contains(&length) && !text.chars().any(char::is_control)
}

/// Sends a notification for „n Dateien fertig" only when the window is not in
/// front. Returns whether a notification was sent.
pub fn notify_finished<R: Runtime>(
    app: &AppHandle<R>,
    title: &str,
    body: &str,
) -> Result<bool, String> {
    if !valid_text(title) || !valid_text(body) {
        return Err("Ungültiger Mitteilungstext.".to_string());
    }
    let in_front = main_window(app)
        .map(|window| window.is_focused().unwrap_or(false) && window.is_visible().unwrap_or(false))
        .unwrap_or(false);
    if in_front {
        return Ok(false);
    }
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .map_err(|error| error.to_string())?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fraction_is_clamped_to_percent() {
        assert_eq!(percent(0.0), 0);
        assert_eq!(percent(0.504), 50);
        assert_eq!(percent(1.0), 100);
        assert_eq!(percent(-1.0), 0);
        assert_eq!(percent(7.0), 100);
        assert_eq!(percent(f64::NAN), 0);
    }

    #[test]
    fn notification_text_is_short_and_single_line() {
        assert!(valid_text("3 Dateien fertig"));
        assert!(!valid_text(""));
        assert!(!valid_text("a\nb"));
        assert!(!valid_text(&"x".repeat(121)));
    }

    #[test]
    fn choice_is_remembered_and_default_applies_without_file() {
        let directory = tempfile::tempdir().unwrap();
        let preferences = WindowPreferences::open(directory.path());
        assert_eq!(preferences.get(), WindowSettings::default());
        preferences
            .set(WindowSettings {
                hide_from_capture: true,
            })
            .unwrap();
        let reopened = WindowPreferences::open(directory.path());
        assert!(reopened.get().hide_from_capture);

        fs::write(directory.path().join(SETTINGS_FILE), b"kaputt").unwrap();
        assert_eq!(
            WindowPreferences::open(directory.path()).get(),
            WindowSettings::default()
        );
    }
}
