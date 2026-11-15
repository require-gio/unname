//! Wait for encrypted view writes and the review queue before closing or quitting.

use std::sync::Mutex;

use tauri::{AppHandle, Emitter, Manager, Window};

const FLUSH_EVENT: &str = "draft://flush";

#[derive(Clone, Debug, PartialEq)]
enum Request {
    Close(String),
    Exit(Option<i32>),
}

#[derive(Default)]
struct Guard {
    ready: bool,
    allowed: bool,
    pending: Option<Request>,
}

impl Guard {
    fn begin(&mut self, request: Request) -> Option<bool> {
        if !self.ready || self.allowed {
            return None;
        }
        let first = self.pending.is_none();
        // Quitting takes precedence over a window close already being flushed.
        if first || matches!(request, Request::Exit(_)) {
            self.pending = Some(request);
        }
        Some(first)
    }

    fn finish(&mut self, success: bool) -> Option<Request> {
        let request = self.pending.take();
        self.allowed = success;
        request.filter(|_| success)
    }
}

#[derive(Default)]
pub struct EntryLifecycle(Mutex<Guard>);

fn begin(app: &AppHandle, request: Request) -> bool {
    let state = app.state::<EntryLifecycle>();
    let action = state.0.lock().expect("entry lifecycle lock").begin(request);
    match action {
        None => false,
        Some(false) => true,
        Some(true) => {
            if app.emit_to("main", FLUSH_EVENT, ()).is_ok() {
                true
            } else {
                state.0.lock().expect("entry lifecycle lock").finish(false);
                false
            }
        }
    }
}

pub fn request_close(window: &Window) -> bool {
    begin(
        window.app_handle(),
        Request::Close(window.label().to_owned()),
    )
}

pub fn request_exit(app: &AppHandle, code: Option<i32>) -> bool {
    begin(app, Request::Exit(code))
}

#[tauri::command]
pub fn set_entry_close_guard(state: tauri::State<'_, EntryLifecycle>, enabled: bool) {
    let mut guard = state.0.lock().expect("entry lifecycle lock");
    guard.ready = enabled;
    guard.allowed = false;
}

#[tauri::command]
pub fn finish_entry_flush(app: AppHandle, success: bool) -> Result<(), String> {
    let request = app
        .state::<EntryLifecycle>()
        .0
        .lock()
        .expect("entry lifecycle lock")
        .finish(success);
    match request {
        Some(Request::Close(label)) => {
            if let Some(window) = app.get_webview_window(&label) {
                window.destroy().map_err(|error| error.to_string())?;
            }
        }
        Some(Request::Exit(code)) => app.exit(code.unwrap_or(0)),
        None => {}
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exit_waits_for_flush_and_overrides_a_pending_window_close() {
        let mut guard = Guard {
            ready: true,
            ..Guard::default()
        };
        assert_eq!(guard.begin(Request::Close("main".into())), Some(true));
        assert_eq!(guard.begin(Request::Exit(Some(0))), Some(false));
        assert_eq!(guard.finish(true), Some(Request::Exit(Some(0))));
        assert_eq!(guard.begin(Request::Exit(Some(0))), None);
    }

    #[test]
    fn failed_flush_keeps_the_window_open_and_allows_retry() {
        let mut guard = Guard {
            ready: true,
            ..Guard::default()
        };
        assert_eq!(guard.begin(Request::Close("main".into())), Some(true));
        assert_eq!(guard.finish(false), None);
        assert_eq!(guard.begin(Request::Close("main".into())), Some(true));
    }
}
