//! Prevent App Nap and system sleep while the sidecar processes documents.
//!
//! While the sidecar is reviewing documents, the shell reports a
//! `userInitiated` activity. This prevents macOS from throttling the app (App
//! Nap) and from entering sleep mode mid-job; `userInitiated` also enables
//! `idleSystemSleepDisabled`. The health watchdog sets the pace: it reports
//! every few seconds whether the sidecar is `busy`.

use std::sync::Mutex;

/// At most one active activity for the entire app.
#[derive(Default)]
pub struct BusyActivity {
    token: Mutex<Option<imp::Token>>,
}

impl BusyActivity {
    /// Starts or stops the activity. Repeated calls with the same value have no
    /// effect.
    pub fn set(&self, busy: bool) {
        let mut token = self
            .token
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        match (busy, token.is_some()) {
            (true, false) => *token = Some(imp::begin()),
            (false, true) => {
                if let Some(running) = token.take() {
                    imp::end(running);
                }
            }
            _ => {}
        }
    }

    #[cfg(test)]
    fn active(&self) -> bool {
        self.token
            .lock()
            .map(|token| token.is_some())
            .unwrap_or(false)
    }
}

#[cfg(target_os = "macos")]
mod imp {
    use objc2::rc::Retained;
    use objc2::runtime::{NSObjectProtocol, ProtocolObject};
    use objc2_foundation::{NSActivityOptions, NSProcessInfo, NSString};

    pub struct Token(Retained<ProtocolObject<dyn NSObjectProtocol>>);

    // SAFETY: The token is an opaque object from NSProcessInfo, whose activity
    // API is thread-safe. It is only held and returned to `endActivity`, never
    // used otherwise.
    unsafe impl Send for Token {}

    pub fn begin() -> Token {
        let reason = NSString::from_str("Dokumente werden geprüft");
        Token(
            NSProcessInfo::processInfo()
                .beginActivityWithOptions_reason(NSActivityOptions::UserInitiated, &reason),
        )
    }

    pub fn end(token: Token) {
        // SAFETY: The token originates from `beginActivityWithOptions_reason`.
        unsafe { NSProcessInfo::processInfo().endActivity(&token.0) };
    }
}

#[cfg(not(target_os = "macos"))]
mod imp {
    pub struct Token;

    pub fn begin() -> Token {
        Token
    }

    pub fn end(_token: Token) {}
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn activity_follows_busy_state() {
        let activity = BusyActivity::default();
        activity.set(true);
        activity.set(true);
        assert!(activity.active());
        activity.set(false);
        assert!(!activity.active());
        activity.set(false);
        assert!(!activity.active());
    }
}
