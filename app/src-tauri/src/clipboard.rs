//! The shell's clipboard. Restored text contains real names. On macOS it stays
//! on this device: no Universal Clipboard, marked as hidden and ephemeral so
//! clipboard managers do not save it, and removed after [`RESTORED_LIFETIME`]
//! if no one else copied something else in the meantime. Masked text, by
//! contrast, goes to an AI program, even on iPhone, and goes into the clipboard
//! as before.

use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::AppHandle;
use tauri_plugin_clipboard_manager::ClipboardExt;

/// How long restored text stays in the clipboard.
pub const RESTORED_LIFETIME: Duration = Duration::from_secs(120);

/// What is copied; where the text may go depends on this.
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum CopyKind {
    /// Restored, with plain text.
    Restored,
    /// Masked, for the AI program.
    Masked,
    /// Content-free, e.g. the diagnostic report.
    Plain,
}

/// What happens to the copied text; the UI aligns its hint accordingly.
#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
pub struct CopyOutcome {
    /// Only on this device, not via Universal Clipboard.
    pub local_only: bool,
    /// The text is removed after this many seconds.
    pub clears_after_s: Option<u64>,
}

/// The clipboard, insofar as clearing it is needed. The rule remains testable
/// without AppKit behind the trait.
trait ChangeCounter {
    fn change_count(&self) -> isize;
    fn clear(&self);
}

/// Clears only if nothing new was copied since our own copy.
fn clear_if_unchanged(board: &impl ChangeCounter, expected: isize) -> bool {
    if board.change_count() != expected {
        return false;
    }
    board.clear();
    true
}

/// Writes `text` to the clipboard as required by `kind`.
pub fn copy(app: &AppHandle, text: &str, kind: CopyKind) -> Result<CopyOutcome, String> {
    #[cfg(target_os = "macos")]
    if kind == CopyKind::Restored {
        let count = mac::write_local_concealed(text)?;
        schedule_clear(app.clone(), count);
        return Ok(CopyOutcome {
            local_only: true,
            clears_after_s: Some(RESTORED_LIFETIME.as_secs()),
        });
    }
    let _ = kind;
    app.clipboard()
        .write_text(text)
        .map_err(|_| "Der Text ließ sich nicht in die Zwischenablage legen.".to_string())?;
    Ok(CopyOutcome {
        local_only: false,
        clears_after_s: None,
    })
}

#[cfg(target_os = "macos")]
fn schedule_clear(app: AppHandle, expected: isize) {
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(RESTORED_LIFETIME).await;
        // AppKit must be called on the main thread.
        let _ = app.run_on_main_thread(move || {
            clear_if_unchanged(&mac::General, expected);
        });
    });
}

#[cfg(target_os = "macos")]
mod mac {
    use objc2_app_kit::{NSPasteboard, NSPasteboardContentsOptions, NSPasteboardTypeString};
    use objc2_foundation::{NSData, NSString};

    use super::ChangeCounter;

    /// Markers for nspasteboard.org: concealed (password manager convention)
    /// and transient. Clipboard managers like Maccy do not save either.
    const MARKERS: [&str; 2] = [
        "org.nspasteboard.ConcealedType",
        "org.nspasteboard.TransientType",
    ];

    /// Writes the text only for this device and returns the `changeCount`
    /// afterwards.
    pub fn write_local_concealed(text: &str) -> Result<isize, String> {
        let board = NSPasteboard::generalPasteboard();
        board.prepareForNewContentsWithOptions(NSPasteboardContentsOptions::CurrentHostOnly);
        // SAFETY: AppKit constant valid for the duration of the process.
        let string_type = unsafe { NSPasteboardTypeString };
        if !board.setString_forType(&NSString::from_str(text), string_type) {
            return Err("Der Text ließ sich nicht in die Zwischenablage legen.".to_string());
        }
        let empty = NSData::new();
        for marker in MARKERS {
            board.setData_forType(Some(&empty), &NSString::from_str(marker));
        }
        Ok(board.changeCount())
    }

    pub struct General;

    impl ChangeCounter for General {
        fn change_count(&self) -> isize {
            NSPasteboard::generalPasteboard().changeCount()
        }

        fn clear(&self) {
            NSPasteboard::generalPasteboard().clearContents();
        }
    }
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;

    use super::*;

    struct FakeBoard {
        count: Cell<isize>,
        cleared: Cell<bool>,
    }

    impl FakeBoard {
        fn at(count: isize) -> Self {
            Self {
                count: Cell::new(count),
                cleared: Cell::new(false),
            }
        }
    }

    impl ChangeCounter for FakeBoard {
        fn change_count(&self) -> isize {
            self.count.get()
        }

        fn clear(&self) {
            self.cleared.set(true);
            self.count.set(self.count.get() + 1);
        }
    }

    #[test]
    fn unchanged_clipboard_is_cleared() {
        let board = FakeBoard::at(7);
        assert!(clear_if_unchanged(&board, 7));
        assert!(board.cleared.get());
    }

    #[test]
    fn text_copied_by_user_is_kept() {
        let board = FakeBoard::at(8);
        assert!(!clear_if_unchanged(&board, 7));
        assert!(!board.cleared.get());
    }

    #[test]
    fn kind_comes_lowercase_from_ui() {
        let kinds: Vec<CopyKind> =
            serde_json::from_str(r#"["restored","masked","plain"]"#).expect("kinds");
        assert_eq!(
            kinds,
            [CopyKind::Restored, CopyKind::Masked, CopyKind::Plain]
        );
        assert!(serde_json::from_str::<CopyKind>(r#""Restored""#).is_err());
    }
}
