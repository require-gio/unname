"""Sidecar diagnostic report.

Purpose: Every automatic fallback — GPU to CPU, MPS to CPU, a restart —
is recorded and **never silently counted as success** (Spec. 2). The
dangerous state is the one that goes unnoticed.

The report includes only operational data. Never original values, text
snippets, or filenames from documents (Spec. 10).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from .handshake import emit

Severity = Literal["info", "warning", "error"]

#: Fixed categories instead of free strings so the UI can group them
#: and typos do not create new categories.
Category = Literal[
    "startup",
    "shutdown",
    "gpu_fallback",
    "mps_fallback",
    "device_selection",
    "model_load",
    "master_data_reload",
    "sidecar_restart",
    "job_failed",
    "ocr",
]


@dataclass(frozen=True)
class DiagnosticEntry:
    timestamp: str
    category: Category
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
            "source": "sidecar",
        }


class DiagnosticsRecorder:
    """Keeps entries in memory and reports them to the harness.

    The ring buffer is limited: the report should describe one session,
    not grow indefinitely.
    """

    def __init__(self, capacity: int = 500) -> None:
        self._entries: deque[DiagnosticEntry] = deque(maxlen=capacity)

    def record(
        self,
        category: Category,
        message: str,
        *,
        severity: Severity = "info",
        **details: Any,
    ) -> DiagnosticEntry:
        entry = DiagnosticEntry(
            timestamp=datetime.now(UTC).isoformat(timespec="milliseconds"),
            category=category,
            severity=severity,
            message=message,
            details=details,
        )
        self._entries.append(entry)
        # The harness maintains the overall report: it also sees events the
        # sidecar itself can no longer report (e.g., its crash).
        emit(
            "diagnostic",
            category=category,
            severity=severity,
            message=message,
            details=details,
            timestamp=entry.timestamp,
        )
        return entry

    def entries(self) -> list[DiagnosticEntry]:
        return list(self._entries)

    def as_dicts(self) -> list[dict[str, Any]]:
        return [entry.as_dict() for entry in self._entries]

    def clear(self) -> None:
        self._entries.clear()
