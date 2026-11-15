//! The shell's diagnostic report.
//!
//! Both sources converge here: entries reported by the sidecar via stdout (GPU
//! fallback, MPS fallback, model loading) and those only the shell can see —
//! foremost a sidecar crash, about which the sidecar itself can no longer
//! speak.
//!
//! Every automatic fallback is logged and **never silently treated as success**
//! (Spec 2). The report contains only operational data, never document content
//! (Spec 10).

use std::collections::VecDeque;
use std::sync::Mutex;

use serde::Serialize;
use serde_json::{json, Value};

/// The report keeps this many entries. It describes a session and must not grow
/// indefinitely.
const CAPACITY: usize = 500;

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Info,
    Warning,
    Error,
}

#[derive(Clone, Debug, Serialize)]
pub struct DiagnosticEntry {
    pub timestamp: String,
    pub category: String,
    pub severity: Severity,
    pub message: String,
    pub details: Value,
    /// `huelle` or `sidecar` — for the review report, it must remain traceable
    /// who observed what.
    pub source: String,
}

#[derive(Default)]
pub struct DiagnosticsLog {
    entries: Mutex<VecDeque<DiagnosticEntry>>,
}

impl DiagnosticsLog {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn record(
        &self,
        category: &str,
        severity: Severity,
        message: impl Into<String>,
        details: Value,
    ) {
        self.push(DiagnosticEntry {
            timestamp: now_iso8601(),
            category: category.to_string(),
            severity,
            message: message.into(),
            details,
            source: "huelle".to_string(),
        });
    }

    /// Takes a `diagnostic` line from the sidecar unchanged.
    pub fn record_from_sidecar(&self, payload: &Value) {
        let severity = match payload.get("severity").and_then(Value::as_str) {
            Some("error") => Severity::Error,
            Some("warning") => Severity::Warning,
            _ => Severity::Info,
        };
        self.push(DiagnosticEntry {
            timestamp: payload
                .get("timestamp")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(now_iso8601),
            category: payload
                .get("category")
                .and_then(Value::as_str)
                .unwrap_or("unbekannt")
                .to_string(),
            severity,
            message: payload
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            details: payload.get("details").cloned().unwrap_or_else(|| json!({})),
            source: "sidecar".to_string(),
        });
    }

    pub fn entries(&self) -> Vec<DiagnosticEntry> {
        self.entries
            .lock()
            .expect("Diagnose-Sperre vergiftet")
            .iter()
            .cloned()
            .collect()
    }

    fn push(&self, entry: DiagnosticEntry) {
        // The log entry carries the same urgency as the report: a warning
        // logged as "info" will be overlooked when reviewing.
        match entry.severity {
            Severity::Error => log::error!("[{}] {}", entry.category, entry.message),
            Severity::Warning => log::warn!("[{}] {}", entry.category, entry.message),
            Severity::Info => log::info!("[{}] {}", entry.category, entry.message),
        }
        let mut entries = self.entries.lock().expect("Diagnose-Sperre vergiftet");
        if entries.len() == CAPACITY {
            entries.pop_front();
        }
        entries.push_back(entry);
    }
}

fn now_iso8601() -> String {
    chrono::Utc::now()
        .to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
        .to_string()
}
