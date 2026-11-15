//! The macOS menu bar.
//!
//! Texts come from the UI dictionary (`set_app_menu`) so every visible text is
//! in one place and the menu follows the app language. Until the UI loads,
//! Tauri's default menu applies.
//!
//! Custom menu items do nothing themselves: they send their ID as
//! `menu://action` to the UI, which triggers the same action as the button in
//! the window. Edit, Window, and Services are AppKit system entries.

use serde::Deserialize;
use tauri::menu::{
    AboutMetadata, Menu, MenuItem, PredefinedMenuItem, Submenu, HELP_SUBMENU_ID, WINDOW_SUBMENU_ID,
};
use tauri::{AppHandle, Emitter, Runtime};

/// A custom menu item was chosen (`{ "action": … }`).
pub const MENU_EVENT: &str = "menu://action";

/// The custom menu items; each ID is also the `action` in the event.
pub const ACTIONS: [&str; 4] = ["settings", "open_files", "open_folder", "help"];

/// The menu texts in the app language.
#[derive(Debug, Deserialize)]
pub struct MenuLabels {
    pub about: String,
    pub settings: String,
    pub services: String,
    pub hide: String,
    pub hide_others: String,
    pub show_all: String,
    pub quit: String,
    pub file: String,
    pub open_files: String,
    pub open_folder: String,
    pub close_window: String,
    pub edit: String,
    pub undo: String,
    pub redo: String,
    pub cut: String,
    pub copy: String,
    pub paste: String,
    pub select_all: String,
    pub window: String,
    pub minimize: String,
    pub zoom: String,
    pub fullscreen: String,
    pub help: String,
    pub help_item: String,
}

impl MenuLabels {
    fn all(&self) -> [&str; 24] {
        [
            &self.about,
            &self.settings,
            &self.services,
            &self.hide,
            &self.hide_others,
            &self.show_all,
            &self.quit,
            &self.file,
            &self.open_files,
            &self.open_folder,
            &self.close_window,
            &self.edit,
            &self.undo,
            &self.redo,
            &self.cut,
            &self.copy,
            &self.paste,
            &self.select_all,
            &self.window,
            &self.minimize,
            &self.zoom,
            &self.fullscreen,
            &self.help,
            &self.help_item,
        ]
    }

    /// Short, single-line texts; more is a UI error.
    pub fn validate(&self) -> Result<(), String> {
        let valid = |text: &str| {
            let length = text.chars().count();
            (1..=80).contains(&length) && !text.chars().any(char::is_control)
        };
        if self.all().iter().all(|text| valid(text)) {
            Ok(())
        } else {
            Err("Ungültiger Menütext.".to_string())
        }
    }
}

fn build<R: Runtime>(app: &AppHandle<R>, labels: &MenuLabels) -> tauri::Result<Menu<R>> {
    let item = |id: &str, text: &str, accelerator: Option<&str>| {
        MenuItem::with_id(app, id, text, true, accelerator)
    };
    let separator = || PredefinedMenuItem::separator(app);
    let about = AboutMetadata {
        copyright: Some("© unname contributors".to_string()),
        ..AboutMetadata::default()
    };

    let application = Submenu::with_items(
        app,
        "unname",
        true,
        &[
            &PredefinedMenuItem::about(app, Some(&labels.about), Some(about))?,
            &separator()?,
            &item("settings", &labels.settings, Some("CmdOrCtrl+,"))?,
            &separator()?,
            &PredefinedMenuItem::services(app, Some(&labels.services))?,
            &separator()?,
            &PredefinedMenuItem::hide(app, Some(&labels.hide))?,
            &PredefinedMenuItem::hide_others(app, Some(&labels.hide_others))?,
            &PredefinedMenuItem::show_all(app, Some(&labels.show_all))?,
            &separator()?,
            &PredefinedMenuItem::quit(app, Some(&labels.quit))?,
        ],
    )?;
    let file = Submenu::with_items(
        app,
        &labels.file,
        true,
        &[
            &item("open_files", &labels.open_files, Some("CmdOrCtrl+O"))?,
            &item(
                "open_folder",
                &labels.open_folder,
                Some("CmdOrCtrl+Shift+O"),
            )?,
            &separator()?,
            &PredefinedMenuItem::close_window(app, Some(&labels.close_window))?,
        ],
    )?;
    let edit = Submenu::with_items(
        app,
        &labels.edit,
        true,
        &[
            &PredefinedMenuItem::undo(app, Some(&labels.undo))?,
            &PredefinedMenuItem::redo(app, Some(&labels.redo))?,
            &separator()?,
            &PredefinedMenuItem::cut(app, Some(&labels.cut))?,
            &PredefinedMenuItem::copy(app, Some(&labels.copy))?,
            &PredefinedMenuItem::paste(app, Some(&labels.paste))?,
            &PredefinedMenuItem::select_all(app, Some(&labels.select_all))?,
        ],
    )?;
    // AppKit attaches these identifiers to the open windows and to the help
    // search itself.
    let window = Submenu::with_id_and_items(
        app,
        WINDOW_SUBMENU_ID,
        &labels.window,
        true,
        &[
            &PredefinedMenuItem::minimize(app, Some(&labels.minimize))?,
            &PredefinedMenuItem::maximize(app, Some(&labels.zoom))?,
            &separator()?,
            &PredefinedMenuItem::fullscreen(app, Some(&labels.fullscreen))?,
        ],
    )?;
    let help = Submenu::with_id_and_items(
        app,
        HELP_SUBMENU_ID,
        &labels.help,
        true,
        &[&item("help", &labels.help_item, None)?],
    )?;
    Menu::with_items(app, &[&application, &file, &edit, &window, &help])
}

/// Sets the menu in the language of these texts. A second call (after a
/// language change) replaces it.
pub fn apply<R: Runtime>(app: &AppHandle<R>, labels: &MenuLabels) -> Result<(), String> {
    labels.validate()?;
    let menu = build(app, labels).map_err(|error| error.to_string())?;
    app.set_menu(menu).map_err(|error| error.to_string())?;
    Ok(())
}

/// Forwards custom menu items to the UI; system entries are handled by AppKit
/// and do not reach here.
pub fn handle_event<R: Runtime>(app: &AppHandle<R>, id: &str) {
    if let Some(action) = ACTIONS.iter().find(|action| **action == id) {
        let _ = app.emit(MENU_EVENT, serde_json::json!({ "action": action }));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn labels(text: &str) -> MenuLabels {
        let s = || text.to_string();
        MenuLabels {
            about: s(),
            settings: s(),
            services: s(),
            hide: s(),
            hide_others: s(),
            show_all: s(),
            quit: s(),
            file: s(),
            open_files: s(),
            open_folder: s(),
            close_window: s(),
            edit: s(),
            undo: s(),
            redo: s(),
            cut: s(),
            copy: s(),
            paste: s(),
            select_all: s(),
            window: s(),
            minimize: s(),
            zoom: s(),
            fullscreen: s(),
            help: s(),
            help_item: s(),
        }
    }

    #[test]
    fn menu_texts_are_short_and_single_line() {
        assert!(labels("Einstellungen …").validate().is_ok());
        assert!(labels("").validate().is_err());
        assert!(labels("a\nb").validate().is_err());
        assert!(labels(&"x".repeat(81)).validate().is_err());
    }
}
