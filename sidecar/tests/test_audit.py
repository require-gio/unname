"""Verify that the SQLite audit log stores only metadata."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from presidio_sidecar.audit import (
    AuditAction,
    AuditContext,
    AuditDecision,
    AuditLog,
    AuditValidationError,
)


def test_audit_log_saves_exactly_requested_metadata_and_export_hash(
    tmp_path: Path,
) -> None:
    export = tmp_path / "ausgabe.txt"
    secret = "Hans Meier, vertraulicher Inhalt"
    export.write_text(secret, encoding="utf-8")
    database = tmp_path / "audit.sqlite3"
    timestamp = datetime(2026, 7, 31, 8, 15, tzinfo=UTC)
    decisions = (
        AuditDecision(
            "person",
            AuditAction.ACCEPT,
            finding_id="f_0123456789abcdefabcd",
            mode="placeholder",
        ),
        AuditDecision("IBAN_CODE", AuditAction.KEEP_PLAIN),
    )

    with AuditLog(database, clock=lambda: timestamp) as audit:
        created = audit.record_export(
            export,
            profile_version="de-at-rechnung-v3@3.0",
            findings_by_entity={"PERSON": 2, "iban_code": 1},
            decisions=decisions,
        )
        loaded = audit.get(created.id)

    expected_hash = hashlib.sha256(export.read_bytes()).hexdigest()
    assert created.export_sha256 == expected_hash
    assert loaded.timestamp == timestamp
    assert loaded.profile_version == "de-at-rechnung-v3@3.0"
    assert loaded.findings_by_entity == {"IBAN_CODE": 1, "PERSON": 2}
    assert loaded.decisions == decisions
    assert database.stat().st_mode & 0o777 == (0o600 if os.name != "nt" else 0o666)


def test_audit_schema_has_no_plaintext_excerpt_or_file_path_columns(
    tmp_path: Path,
) -> None:
    export = tmp_path / "ausgabe.txt"
    secret = "Petra Novak, Aktenzeichen GEHEIM-4711"
    export.write_text(secret, encoding="utf-8")
    database = tmp_path / "audit.sqlite3"
    with AuditLog(database) as audit:
        audit.record_export(
            export,
            profile_version="kanzlei-v1",
            findings_by_entity={"PERSON": 1},
            decisions=(AuditDecision("PERSON", AuditAction.REJECT, origin="user"),),
            context=AuditContext(
                entry_id="0123456789abcdef01234567",
                method="reversible",
                style="codes",
                categories=("finance_ids", "names"),
                protection_level=1,
                decided_by_user=1,
            ),
        )
        audit.record_text(
            secret,
            profile_version="grundprofil@1.0.0",
            findings_by_entity={"PERSON": 1},
        )

    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            if not row[0].startswith("sqlite_")
        }
        columns = {
            table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for table in tables
        }
        serialized_rows = json.dumps(
            {
                "exports": connection.execute("SELECT * FROM audit_exports").fetchall(),
                "counts": connection.execute("SELECT * FROM audit_entity_counts").fetchall(),
                "decisions": connection.execute("SELECT * FROM audit_decisions").fetchall(),
                "masking": connection.execute("SELECT * FROM audit_masking").fetchall(),
            },
            ensure_ascii=False,
        )
    finally:
        connection.close()

    assert tables == {
        "audit_exports",
        "audit_entity_counts",
        "audit_decisions",
        "audit_masking",
    }
    forbidden = {
        "text",
        "context",
        "snippet",
        "original",
        "replacement",
        "export_path",
        "filename",
    }
    assert all(forbidden.isdisjoint(table_columns) for table_columns in columns.values())
    assert secret not in serialized_rows
    assert secret.encode() not in database.read_bytes()
    assert str(export) not in serialized_rows


@pytest.mark.parametrize(
    "decision",
    [
        AuditDecision("PERSON", AuditAction.ACCEPT),
        AuditDecision("PERSON", AuditAction.ADD_MANUAL, mode="remove"),
        AuditDecision("LOCATION", AuditAction.ADJUST_SPAN),
        AuditDecision(
            "IBAN_CODE",
            AuditAction.CHANGE_MODE,
            mode="inherit",
        ),
        AuditDecision("LOCATION", AuditAction.REMOVE_KEEP_PLAIN),
        AuditDecision("CUSTOMER_NUMBER", AuditAction.REMOVE_FORCE_HANDLE),
    ],
)
def test_allowed_user_decisions_remain_content_free(
    tmp_path: Path,
    decision: AuditDecision,
) -> None:
    export = tmp_path / f"{decision.action}.txt"
    export.write_text("bereinigt", encoding="utf-8")
    with AuditLog(tmp_path / f"{decision.action}.sqlite3") as audit:
        record = audit.record_export(
            export,
            profile_version="profil-v1",
            findings_by_entity={decision.entity_type: 1},
            decisions=(decision,),
        )
        assert audit.get(record.id).decisions == (decision,)


def test_audit_log_rejects_free_text_fields_and_invalid_metadata(
    tmp_path: Path,
) -> None:
    export = tmp_path / "ausgabe.txt"
    export.write_text("bereinigt", encoding="utf-8")
    with pytest.raises(AuditValidationError, match="finding_id"):
        AuditDecision("PERSON", AuditAction.ACCEPT, finding_id="Hans Meier")
    with pytest.raises(AuditValidationError, match="Verarbeitungsmodus"):
        AuditDecision("PERSON", AuditAction.ACCEPT, mode="Hans Meier")

    with AuditLog(tmp_path / "audit.sqlite3") as audit:
        with pytest.raises(AuditValidationError, match="Profilversion"):
            audit.record_export(
                export,
                profile_version="Hans Meier",
                findings_by_entity={"PERSON": 1},
            )
        with pytest.raises(AuditValidationError, match="AuditDecision"):
            audit.record_export(
                export,
                profile_version="profil-v1",
                findings_by_entity={"PERSON": 1},
                decisions=({"text": "Hans Meier"},),  # type: ignore[arg-type]
            )
        with pytest.raises(AuditValidationError, match="Trefferzahlen"):
            audit.record_export(
                export,
                profile_version="profil-v1",
                findings_by_entity={"PERSON": -1},
            )


def test_api_v2_request_and_origin_reach_audit_content_free(tmp_path: Path) -> None:
    """Verify audit handling of entry IDs and decisions by origin."""

    export = tmp_path / "ausgabe.txt"
    export.write_text("<<PERSON_ab12cd34>> schreibt.", encoding="utf-8")
    context = AuditContext(
        entry_id="0123456789abcdef01234567",
        method="irreversible",
        style="placeholder",
        categories=("finance_ids", "names", "organizations"),
        protection_level=1,
        decided_by_user=2,
        decided_by_ai=1,
        masked_without_decision=3,
    )
    decisions = (
        AuditDecision(
            "PERSON", AuditAction.ACCEPT, finding_id="f_0123456789abcdefabcd", origin="user"
        ),
        AuditDecision(
            "PERSON",
            AuditAction.REJECT,
            finding_id="f_abcdefabcdef01234567",
            origin="ai-suggestion",
        ),
    )
    with AuditLog(tmp_path / "audit.sqlite3") as audit:
        document = audit.record_export(
            export,
            profile_version="grundprofil@1.0.0",
            findings_by_entity={"PERSON": 2},
            decisions=decisions,
            context=context,
        )
        copied = audit.record_text(
            "[Name] schreibt.",
            profile_version="grundprofil@1.0.0",
            findings_by_entity={"PERSON": 1},
            context=context,
        )
        loaded = audit.get(document.id)
        loaded_copy = audit.get(copied.id)

    assert loaded.kind == "document"
    assert loaded.context == context
    assert loaded.decisions == decisions
    assert loaded_copy.kind == "text"
    assert loaded_copy.export_sha256 == hashlib.sha256(b"[Name] schreibt.").hexdigest()
    with pytest.raises(AuditValidationError, match="Eintrags-ID"):
        AuditContext(
            entry_id="Hans Meier",
            method="reversible",
            style="codes",
            categories=(),
            protection_level=1,
        )
    with pytest.raises(AuditValidationError, match="Herkunft"):
        AuditDecision("PERSON", AuditAction.ACCEPT, origin="Hans Meier")


def test_old_audit_log_gets_the_new_columns(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE audit_exports (
            id INTEGER PRIMARY KEY,
            timestamp_utc TEXT NOT NULL,
            profile_version TEXT NOT NULL,
            export_sha256 TEXT NOT NULL CHECK(length(export_sha256) = 64)
        );
        CREATE TABLE audit_decisions (
            export_id INTEGER NOT NULL, sequence_no INTEGER NOT NULL, finding_id TEXT,
            entity_type TEXT NOT NULL, action TEXT NOT NULL, mode TEXT,
            PRIMARY KEY(export_id, sequence_no)
        );
        INSERT INTO audit_exports VALUES (1, '2026-01-01T00:00:00Z', 'alt-v1', '"""
        + "a" * 64
        + """');
        INSERT INTO audit_decisions VALUES (1, 0, NULL, 'PERSON', 'reject', NULL);
        """
    )
    connection.commit()
    connection.close()

    with AuditLog(database) as audit:
        old = audit.get(1)

    assert old.kind == "document"
    assert old.context is None
    assert old.decisions == (AuditDecision("PERSON", AuditAction.REJECT),)
