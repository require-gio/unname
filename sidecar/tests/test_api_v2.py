"""API v2: Catalog, Masking job, entries, text mode, dry-run."""

from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from presidio_sidecar.app import create_app
from presidio_sidecar.catalog import (
    CATEGORIES,
    CATEGORY_BY_ENTITY,
    CATEGORY_IDS,
    PLACEHOLDERS,
    catalog_payload,
    placeholder_for,
)
from presidio_sidecar.config import Settings
from presidio_sidecar.detection import DetectionResult, Finding, FindingSource
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.entries import EntryError, EntryStore
from presidio_sidecar.extraction import DocumentExtractor
from presidio_sidecar.jobs import JobHooks, JobManager, JobNotFoundError, JobStatus, RestoredJob
from presidio_sidecar.masking import MaskingSpec
from presidio_sidecar.processing import ENTITY_MINIMUM_LEVEL, ProtectionLevel
from presidio_sidecar.profiles import Profile, load_profiles
from presidio_sidecar.services import PhaseOneServices
from presidio_sidecar.settings_store import SettingsError, SettingsStore, migrate
from presidio_sidecar.titles import TitleSource, documents_title, text_title
from presidio_sidecar.vault import TokenVault
from presidio_sidecar.workers import WorkerCapacity

TOKEN = "api-v2-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ALL_CATEGORIES = list(CATEGORY_IDS)
BLOCK_MARKER = "BITTE-WARTEN"


# -- Catalog -----------------------------------------------------------------


def test_catalog_covers_each_classified_entity_exactly_once() -> None:
    """Contract test: catalog ↔ ``ENTITY_MINIMUM_LEVEL``."""

    listed = [entity for category in CATEGORIES for entity in category.entity_types]
    assert len(listed) == len(set(listed)), "Ein Typ steht in zwei Kategorien."
    assert set(listed) == set(ENTITY_MINIMUM_LEVEL)
    for entity, level in ENTITY_MINIMUM_LEVEL.items():
        assert CATEGORY_BY_ENTITY[entity].level == int(level), entity


def test_catalog_has_placeholder_per_type_and_language() -> None:
    assert set(PLACEHOLDERS) == set(ENTITY_MINIMUM_LEVEL)
    for labels in PLACEHOLDERS.values():
        assert set(labels) == {"de", "en"}
    assert placeholder_for("PERSON", "de") == "[Name]"
    assert placeholder_for("LOCATION", "en") == "[Place]"
    assert placeholder_for("DATE_TIME") == "[Datum]"


def test_catalog_standard_matches_level_two_and_locks_account_data() -> None:
    payload = catalog_payload()
    defaults = payload["defaults"]
    assert defaults == {
        "method": "reversible",
        "style": "codes",
        "categories": ["finance_ids", "names", "addresses", "contact", "numbers"],
    }
    locked = [item["id"] for item in payload["categories"] if item["locked"]]
    assert locked == ["finance_ids"]
    assert payload["styles"] == {
        "reversible": ["codes", "surrogates"],
        "irreversible": ["placeholder", "mask", "remove"],
    }


def test_frontend_catalog_reflects_sidecar() -> None:
    """The frontend still renders from ``catalog.ts`` until it reads ``GET /catalog``."""

    source = (
        Path(__file__).resolve().parents[2] / "app" / "src" / "lib" / "masking" / "catalog.ts"
    ).read_text(encoding="utf-8")
    for category in CATEGORIES:
        assert f"id: '{category.id}'" in source
        for entity in category.entity_types:
            assert f"'{entity}'" in source, entity


# -- Masking job ------------------------------------------------------


def _spec(**overrides: Any) -> MaskingSpec:
    data: dict[str, Any] = {
        "method": "reversible",
        "style": "codes",
        "categories": ["names", "addresses", "contact", "numbers"],
        "anonymize": False,
    }
    data.update(overrides)
    return MaskingSpec.model_validate(data)


def test_masking_spec_always_includes_locked_category() -> None:
    spec = _spec(categories=["names"])
    assert spec.categories == ("finance_ids", "names")
    assert "IBAN_CODE" in spec.handled_entity_types
    assert "PERSON" in spec.handled_entity_types
    assert "LOCATION" in spec.kept_plain_entity_types


def test_masking_spec_validates_style_method_and_anonymize() -> None:
    with pytest.raises(ValidationError):
        _spec(style="placeholder")
    with pytest.raises(ValidationError):
        _spec(categories=["unbekannt"])
    with pytest.raises(ValidationError):
        # Claims “anonym” but does not select all categories.
        _spec(method="irreversible", style="placeholder", anonymize=True)
    with pytest.raises(ValidationError):
        # Satisfies the condition but hides it.
        _spec(method="irreversible", style="placeholder", categories=ALL_CATEGORIES)
    spec = _spec(method="irreversible", style="mask", categories=ALL_CATEGORIES, anonymize=True)
    assert spec.protection_level is ProtectionLevel.INDIRECT
    assert spec.kept_plain_entity_types == frozenset()


def test_masking_spec_derives_level_only_for_audit() -> None:
    assert _spec(categories=["names"]).protection_level is ProtectionLevel.CRITICAL
    assert _spec().protection_level is ProtectionLevel.DIRECT


# -- Settings -----------------------------------------------------------


def test_settings_migrate_and_reject_newer_version(tmp_path: Path) -> None:
    assert migrate({"history": {"retention_days": 7}})["schema_version"] == 3
    with pytest.raises(SettingsError):
        migrate({"schema_version": 99})

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"history": {"retention_days": 90}}), encoding="utf-8")
    store = SettingsStore(path)
    assert store.get().history.retention_days == 90
    assert store.get().schema_version == 3

    path.write_text(json.dumps({"schema_version": 4}), encoding="utf-8")
    with pytest.raises(SettingsError):
        SettingsStore(path).get()


def test_settings_change_only_named_fields_and_write_privately(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.update({"general": {"language": "en"}})
    updated = store.update({"masking": {"method": "irreversible", "style": "placeholder"}})
    assert updated.general.language == "en"
    assert updated.general.theme == "system"
    assert updated.masking.categories[0] == "finance_ids"
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "settings.json").stat().st_mode) == 0o600
    # Read again from disk; it is the same state.
    assert SettingsStore(tmp_path / "settings.json").get() == updated


# -- Entry store --------------------------------------------------------


def test_history_stored_encrypted_only_on_disk(tmp_path: Path) -> None:
    store = EntryStore(tmp_path / "entries.enc", tmp_path / "entries.key")
    entry = store.create(kind="documents", masking=MaskingSpec.default(), title="Angebot")
    store.update(
        entry.id,
        lambda current: current.model_copy(update={"title": "Angebot_Hofer.docx"}),
    )
    raw = (tmp_path / "entries.enc").read_bytes()
    assert b"Hofer" not in raw
    assert b"Angebot" not in raw
    reopened = EntryStore(tmp_path / "entries.enc", tmp_path / "entries.key")
    assert reopened.get(entry.id).title == "Angebot_Hofer.docx"

    tampered = bytearray(raw)
    tampered[-1] ^= 0x01
    (tmp_path / "entries.enc").write_bytes(bytes(tampered))
    with pytest.raises(EntryError):
        EntryStore(tmp_path / "entries.enc", tmp_path / "entries.key")


def test_retention_janitor_deletes_only_expired_entries(tmp_path: Path) -> None:
    now = datetime(2026, 10, 23, tzinfo=UTC)
    clock = [now - timedelta(days=40)]
    store = EntryStore(
        tmp_path / "entries.enc",
        tmp_path / "entries.key",
        clock=lambda: clock[0],
    )
    old = store.create(kind="text", masking=MaskingSpec.default(), title="Text")
    clock[0] = now - timedelta(days=2)
    fresh = store.create(kind="text", masking=MaskingSpec.default(), title="Text")
    clock[0] = now
    assert store.purge_expired(None) == []
    removed = store.purge_expired(30)
    assert [entry.id for entry in removed] == [old.id]
    assert [entry.id for entry in store.list()] == [fresh.id]


# -- Titles -------------------------------------------------------------------


def test_titles_never_contain_a_name_from_the_filename() -> None:
    assert documents_title([TitleSource("Angebot_Hofer.docx")]) == "Angebot"
    assert documents_title([TitleSource("Hofer_Maria_2026.pdf")]) == "Dokument"
    assert documents_title([TitleSource("Mietvertrag Hofer (Scan).pdf", ocr_used=True)]) == (
        "Mietvertrag (Scan)"
    )
    assert documents_title([TitleSource("scan_0012.pdf", text="RECHNUNG Nr. 4711")]) == ("Rechnung")
    assert (
        documents_title(
            [TitleSource("Handwerkerrechnung_Meier.pdf"), TitleSource("Brief_Meier.docx")]
        )
        == "Rechnung + 1 weitere"
    )
    assert documents_title([TitleSource("a.pdf"), TitleSource("b.pdf")]) == "2 Dokumente"
    # A detected company name is not a document type.
    assert (
        documents_title(
            [TitleSource("brief.pdf", text="Rechnung GmbH, Wien")],
            exclude_values=["Rechnung GmbH"],
        )
        == "Schreiben"
    )


def test_text_title_detects_email_draft() -> None:
    assert text_title("Sehr geehrte Frau Hofer,\n\nanbei …\n\nMit freundlichen Grüßen\nAnna") == (
        "E-Mail-Entwurf"
    )
    assert text_title("Hans Meier wohnt in Wien.") == "Text"
    assert text_title("Hallo Maria,\nbis morgen!\nLG", language="en") == "Email draft"


# -- Vault: Dry-Run and Commit ----------------------------------------------


def test_dry_run_computes_codes_without_writing_and_commit_records_origin(
    tmp_path: Path,
) -> None:
    vault = TokenVault(tmp_path / "state")
    try:
        pending = vault.prepare_token("PERSON", "Hans Meier")
        assert pending.token == vault.token_for("PERSON", "Hans Meier", reversible=False)
        assert "Hans" not in repr(pending)
        assert vault.status().mapping_count == 0
        assert vault.commit([pending], entry_id="eintrag-1") == 1
        assert vault.status().mapping_count == 1
        assert vault.deanonymize_tokens(pending.token).text == "Hans Meier"
        assert [origin for origin, _ in vault.token_origins(pending.token)] == ["eintrag-1"]

        surrogate = vault.prepare_surrogate("PERSON", "Petra Novak")
        assert surrogate.surrogate and surrogate.surrogate != "Petra Novak"
        assert vault.status().mapping_count == 1
        vault.commit([surrogate], entry_id="eintrag-2")
        assert vault.deanonymize_surrogates(surrogate.surrogate).text == "Petra Novak"
    finally:
        vault.close()


def test_vault_schema_one_migrated_on_load(tmp_path: Path) -> None:
    vault = TokenVault(tmp_path / "state")
    token = vault.token_for("PERSON", "Hans Meier", reversible=True)
    mapping = vault._mapping_store()
    mapping._connection.executescript(
        "DROP TABLE usage; UPDATE vault_metadata SET schema_version = 1;"
    )
    mapping._persist()
    vault.close()

    reopened = TokenVault(tmp_path / "state")
    try:
        assert reopened.deanonymize_tokens(token).text == "Hans Meier"
        reopened.commit([reopened.prepare_token("PERSON", "Hans Meier")], entry_id="e")
        assert [origin for origin, _ in reopened.token_origins(token)] == ["e"]
    finally:
        reopened.close()


# -- HTTP --------------------------------------------------------------------


class FakeAnalyzer:
    """Fixed matches: confirmed, uncertain and suspicious – without models."""

    CONFIRMED = {
        "Hans Meier": ("PERSON", 0.97, FindingSource.NER),
        "DE02120300000000202051": ("IBAN_CODE", 1.0, FindingSource.RULE),
        "Musterbau GmbH": ("ORGANIZATION", 0.6, FindingSource.NER),
    }
    SUSPICIOUS = {"Petra Novak": "PERSON"}

    def __init__(self) -> None:
        self.release = threading.Event()
        self.release.set()
        self.calls = 0

    def nlp_status(self) -> dict[str, Any]:
        return {"active": "fake", "loaded": True, "device": "cpu"}

    def analyze_for_profile(
        self,
        text: str,
        profile: Profile,
        *,
        progress: Callable[[str], None],
    ) -> DetectionResult:
        self.calls += 1
        for phase in ("master_data", "rules"):
            progress(phase)
        if BLOCK_MARKER in text:
            assert self.release.wait(5)
        confirmed = [
            Finding(entity, start, start + len(value), score, source, value, "fake")
            for value, (entity, score, source) in self.CONFIRMED.items()
            for start in _all(text, value)
        ]
        interim = getattr(progress, "interim", None)
        if callable(interim):
            interim(confirmed)
        progress("ner")
        progress("suspicion")
        suspicious = [
            Finding(entity, start, start + len(value), 0.3, FindingSource.SUSPICION, value, "fake")
            for value, entity in self.SUSPICIOUS.items()
            for start in _all(text, value)
        ]
        return DetectionResult(
            confirmed=tuple(sorted(confirmed, key=lambda item: item.start)),
            suspicious=tuple(suspicious),
        )


def _all(text: str, value: str) -> Iterator[int]:
    offset = 0
    while (start := text.find(value, offset)) >= 0:
        yield start
        offset = start + len(value)


@dataclass(frozen=True, slots=True)
class Harness:
    client: TestClient
    services: PhaseOneServices
    analyzer: FakeAnalyzer
    tmp_path: Path

    def mappings(self) -> int:
        return self.services.vault().status().mapping_count


@pytest.fixture
def api(tmp_path: Path) -> Iterator[Harness]:
    settings = Settings(
        token=TOKEN,
        state_directory=tmp_path / "state",
        job_ttl_seconds=60,
        max_text_bytes=1024 * 1024,
    )
    recorder = DiagnosticsRecorder()
    analyzer = FakeAnalyzer()
    jobs = JobManager(
        analyzer,  # type: ignore[arg-type]
        max_workers=2,
        ttl_seconds=60,
        max_text_bytes=settings.max_text_bytes,
        root=tmp_path / "jobs",
    )
    services = PhaseOneServices(
        settings,
        recorder,
        analyzer=analyzer,  # type: ignore[arg-type]
        jobs=jobs,
        extractor=DocumentExtractor(),
        profiles=load_profiles(),
        capacity=WorkerCapacity(1, 0, 2, 8 * 1024**3),
    )
    # No second opinion model: the training model must not alter questions or counts.
    # Tests that need a second opinion use their own model.
    services.second_opinion_model = lambda: None  # type: ignore[method-assign]
    with TestClient(create_app(settings, recorder, phase_one=services)) as client:
        yield Harness(client, services, analyzer, tmp_path)
    analyzer.release.set()


DOCUMENT = (
    "Rechnung\n\nKunde: Hans Meier\nIBAN DE02120300000000202051\n\n"
    "Ansprechpartnerin Petra Novak bei Musterbau GmbH."
)


def _entry_with_files(api: Harness, files: dict[str, str]) -> tuple[str, list[str]]:
    entry = api.client.post("/entries", headers=AUTH, json={"kind": "documents"})
    assert entry.status_code == 201, entry.text
    entry_id = entry.json()["id"]
    response = api.client.post(
        f"/entries/{entry_id}/files",
        headers=AUTH,
        files=[("file", (name, content.encode(), "text/plain")) for name, content in files.items()],
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert not body["rejected"]
    return entry_id, [item["id"] for item in body["accepted"]]


def _wait_ready(api: Harness, entry_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        progress = api.client.get(f"/entries/{entry_id}/progress", headers=AUTH)
        assert progress.status_code == 200, progress.text
        body = progress.json()
        if body["done"] == body["total"]:
            return body
        time.sleep(0.01)
    raise AssertionError("Eintrag wurde nicht fertig.")


def _apply(api: Harness, job_id: str, masking: dict[str, Any], **review: Any) -> dict[str, Any]:
    response = api.client.post(
        f"/jobs/{job_id}/apply",
        headers=AUTH,
        json={"masking": masking, "review": review},
    )
    assert response.status_code == 200, response.text
    return response.json()


REVERSIBLE = {
    "method": "reversible",
    "style": "codes",
    "categories": ["names", "addresses", "contact", "numbers"],
    "anonymize": False,
}
ANONYMIZE = {
    "method": "irreversible",
    "style": "placeholder",
    "categories": ALL_CATEGORIES,
    "anonymize": True,
}


def test_catalog_and_settings_endpoints(api: Harness) -> None:
    catalog = api.client.get("/catalog", headers=AUTH)
    assert catalog.status_code == 200
    assert [item["id"] for item in catalog.json()["categories"]] == ALL_CATEGORIES

    settings = api.client.get("/settings", headers=AUTH).json()
    assert settings["schema_version"] == 3
    assert settings["history"]["retention_days"] == 30
    updated = api.client.put("/settings", headers=AUTH, json={"general": {"language": "en"}})
    assert updated.status_code == 200
    assert updated.json()["general"]["language"] == "en"
    assert api.client.put("/settings", headers=AUTH, json={"nope": {}}).status_code == 422


def test_entry_with_multiple_files_shows_progress_and_findings(api: Harness) -> None:
    entry_id, file_ids = _entry_with_files(
        api,
        {"Rechnung_Hofer.txt": DOCUMENT, "notiz.txt": "Hans Meier ruft an."},
    )
    progress = _wait_ready(api, entry_id)
    assert progress["ready"] is True
    assert progress["status"] == "review"
    assert progress["totals"] == {"finance_ids": 1, "names": 3, "organizations": 1}
    assert {item["status"] for item in progress["files"]} == {"review"}
    # “Braucht Ihren Blick": The suspicion counts, the insecure company does not —
    # Companies remain in the default selection anyway.
    assert [item["questions"] for item in progress["files"]] == [1, 0]

    entry = api.client.get(f"/entries/{entry_id}", headers=AUTH).json()
    # Use the document type for the title, never the filename.
    assert entry["title"] == "Rechnung + 1 weitere"
    assert "Hofer" not in entry["title"]

    findings = api.client.get(f"/jobs/{file_ids[0]}/findings", headers=AUTH).json()
    by_text = {item["text"]: item for item in findings["findings"]}
    assert by_text["Hans Meier"]["category"] == "names"
    assert by_text["Hans Meier"]["question"] is None
    assert by_text["Musterbau GmbH"]["question"] == {"kind": "uncertain", "n": 2}
    assert by_text["Petra Novak"]["question"] == {"kind": "suspicion", "n": 1}
    assert findings["counts"]["questions"] == 2
    assert findings["counts"]["by_category"] == {
        "finance_ids": 1,
        "names": 2,
        "organizations": 1,
    }
    assert [block["index"] for block in findings["blocks"]] == [0, 1, 2]
    assert findings["blocks"][0]["start"] == 0


def test_toggling_ten_times_then_discarding_leaves_table_unchanged(
    api: Harness,
) -> None:
    entry_id, (job_id,) = _entry_with_files(api, {"rechnung.txt": DOCUMENT})
    _wait_ready(api, entry_id)
    before = api.mappings()
    for index in range(10):
        masking = (
            REVERSIBLE
            if index % 2 == 0
            else {**REVERSIBLE, "style": "surrogates", "categories": ALL_CATEGORIES}
        )
        result = _apply(api, job_id, masking)
        assert "Hans Meier" not in result["text"]
        assert result["labels"]["label"] == "reversible"
    assert api.mappings() == before == 0

    # Delete entry, keep table unchanged, clean up job.
    assert api.client.delete(f"/entries/{entry_id}", headers=AUTH).status_code == 204
    assert api.mappings() == before
    assert api.client.get(f"/jobs/{job_id}/findings", headers=AUTH).status_code == 404

    # Saving no longer creates a separate output; the old UI export was removed,
    # dry-run shortcuts now only use ``/entries/{id}/save``.
    entry_id, (job_id,) = _entry_with_files(api, {"rechnung.txt": DOCUMENT})
    _wait_ready(api, entry_id)
    _apply(api, job_id, REVERSIBLE)
    exported = api.client.post(
        f"/jobs/{job_id}/export",
        headers=AUTH,
        json={"path": str(api.tmp_path / "out.txt")},
    )
    assert exported.status_code in {404, 405}
    assert not (api.tmp_path / "out.txt").exists()
    assert api.mappings() == 0


def test_open_question_is_masked_on_save(api: Harness) -> None:
    entry_id, (job_id,) = _entry_with_files(api, {"rechnung.txt": DOCUMENT})
    _wait_ready(api, entry_id)
    masking = {**REVERSIBLE, "categories": [*REVERSIBLE["categories"], "organizations"]}

    masked = _apply(api, job_id, masking)
    assert "Petra Novak" not in masked["text"]
    assert "Musterbau GmbH" not in masked["text"]
    assert masked["labels"]["masked_without_decision"] == 2
    assert masked["report"]["unresolved_masked_count"] == 2

    preview = _apply(api, job_id, masking, unresolved="keep")
    assert "Petra Novak" in preview["text"]

    findings = api.client.get(f"/jobs/{job_id}/findings", headers=AUTH).json()
    petra = next(item for item in findings["findings"] if item["text"] == "Petra Novak")
    decided = _apply(
        api,
        job_id,
        masking,
        decisions=[{"finding_id": petra["id"], "action": "reject"}],
    )
    assert "Petra Novak" in decided["text"]
    assert decided["labels"]["masked_without_decision"] == 1
    assert decided["labels"]["kept_here_count"] == 1

    # The review state is stored empty on the entry.
    entry = api.client.get(f"/entries/{entry_id}", headers=AUTH).json()
    assert entry["review"][job_id]["decisions"] == [
        {"finding_id": petra["id"], "action": "reject", "by": "user"}
    ]
    assert entry["files"][0]["label"] == "reversible"

    # In text mode, copy acts as save – open questions stay masked even if preview left them
    # visible.
    committed = api.client.post(
        "/text/commit",
        headers=AUTH,
        json={"text": DOCUMENT, "masking": masking, "review": {"unresolved": "keep"}},
    )
    assert committed.status_code == 200, committed.text
    assert "Petra Novak" not in committed.json()["result"]["text"]


def test_anonymize_with_residual_risk_is_not_anonymous(api: Harness) -> None:
    risky = api.client.post(
        "/text/mask",
        headers=AUTH,
        json={
            "text": "Hans Meier wohnt in 1010 Wien und ist am 03.04.2026 geboren.",
            "masking": ANONYMIZE,
        },
    )
    assert risky.status_code == 200, risky.text
    labels = risky.json()["result"]["labels"]
    assert labels["label"] == "irreversible"
    assert labels["anonymous_blockers"] == ["residual_risk"]
    assert risky.json()["result"]["residual_risk"]["status"] == "warning"

    clean = api.client.post(
        "/text/mask",
        headers=AUTH,
        json={"text": "Hans Meier hat geschrieben.", "masking": ANONYMIZE},
    ).json()
    assert clean["result"]["labels"]["label"] == "anonymous"
    assert clean["result"]["text"] == "[Name] hat geschrieben."

    kept = api.client.post(
        "/text/mask",
        headers=AUTH,
        json={
            "text": "Hans Meier hat geschrieben.",
            "masking": ANONYMIZE,
            "review": {
                "decisions": [
                    {"finding_id": clean["findings"][0]["id"], "action": "reject"},
                ]
            },
        },
    ).json()
    assert kept["result"]["labels"]["label"] == "irreversible"
    assert "kept_here" in kept["result"]["labels"]["anonymous_blockers"]


def test_text_masking_saves_nothing_copy_writes_with_origin(api: Harness) -> None:
    body = {
        "text": "Sehr geehrter Herr Hans Meier,\n\nDanke.\n\nViele Grüße",
        "masking": REVERSIBLE,
    }
    masked = api.client.post("/text/mask", headers=AUTH, json=body)
    assert masked.status_code == 200
    assert api.mappings() == 0
    assert api.client.get("/entries", headers=AUTH).json()["count"] == 0

    calls = api.analyzer.calls
    committed = api.client.post("/text/commit", headers=AUTH, json=body).json()
    # Copy does not trigger a new detection; the preview result is reused.
    assert api.analyzer.calls == calls
    assert committed["result"]["text"] == masked.json()["result"]["text"]
    assert committed["committed_mappings"] == 1
    assert committed["entry"]["status"] == "copied"
    assert committed["entry"]["title"] == "E-Mail-Entwurf"
    assert api.mappings() == 1
    token = committed["result"]["text"].split("Herr ", 1)[1].split(",", 1)[0]
    origins = api.services.vault().token_origins(token)
    assert [origin for origin, _ in origins] == [committed["entry"]["id"]]
    listed = api.client.get("/entries", headers=AUTH).json()
    assert [item["id"] for item in listed["entries"]] == [committed["entry"]["id"]]


def test_occurrences_in_all_files_of_entry(api: Harness) -> None:
    entry_id, file_ids = _entry_with_files(
        api,
        {"a.txt": "Herr Hofer schreibt. Hoferstrasse 1.", "b.txt": "hofer und Hofer."},
    )
    _wait_ready(api, entry_id)
    response = api.client.post(
        f"/entries/{entry_id}/occurrences", headers=AUTH, json={"text": "Hofer"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2
    assert body["file_count"] == 2
    assert {item["file_id"] for item in body["matches"]} == set(file_ids)
    ignore_case = api.client.post(
        f"/entries/{entry_id}/occurrences",
        headers=AUTH,
        json={"text": "Hofer", "ignore_case": True},
    ).json()
    assert ignore_case["count"] == 3


def test_running_file_can_be_aborted(api: Harness) -> None:
    api.analyzer.release.clear()
    entry_id, (slow, fast) = _entry_with_files(
        api,
        {"lang.txt": f"{BLOCK_MARKER} Hans Meier", "kurz.txt": "Hans Meier"},
    )
    deadline = time.monotonic() + 5
    while api.services.jobs().get(slow).status_dict()["status"] != "running":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert api.client.delete(f"/jobs/{slow}", headers=AUTH).status_code == 204
    # Cancelled jobs stay cancelled (draft state): no result, but no 404.
    assert api.client.get(f"/jobs/{slow}/findings", headers=AUTH).status_code == 409
    api.analyzer.release.set()

    progress = _wait_ready(api, entry_id)
    statuses = {item["id"]: item["status"] for item in progress["files"]}
    assert statuses[fast] == "review"
    assert statuses[slow] == "cancelled"
    assert progress["status"] == "review"


def test_files_are_rejected_individually(api: Harness) -> None:
    entry_id = api.client.post("/entries", headers=AUTH, json={}).json()["id"]
    response = api.client.post(
        f"/entries/{entry_id}/files",
        headers=AUTH,
        files=[
            ("file", ("ok.txt", b"Hans Meier", "text/plain")),
            ("file", ("tabelle.exe", b"MZ", "application/octet-stream")),
            ("file", ("leer.txt", b"", "text/plain")),
        ],
    )
    assert response.status_code == 202
    body = response.json()
    assert [item["name"] for item in body["accepted"]] == ["ok.txt"]
    assert {item["name"]: item["code"] for item in body["rejected"]} == {
        "tabelle.exe": "unsupported_file_format",
        "leer.txt": "empty",
    }


def test_files_arrive_only_as_bytes_never_as_path(api: Harness) -> None:
    """The shell reads file contents; a path in the request body is not processed."""

    secret = api.tmp_path / "geheim.txt"
    secret.write_text("Hans Meier", encoding="utf-8")
    entry_id = api.client.post("/entries", headers=AUTH, json={}).json()["id"]

    response = api.client.post(
        f"/entries/{entry_id}/files", headers=AUTH, json={"paths": [str(secret)]}
    )

    assert response.status_code == 415
    assert api.client.get(f"/entries/{entry_id}", headers=AUTH).json()["files"] == []


def test_multipart_from_shell_keeps_umlauts_in_filename(api: Harness) -> None:
    """Shows how ``eingang.rs`` builds the body: raw UTF-8, quotes as ``%22``."""

    entry_id = api.client.post("/entries", headers=AUTH, json={}).json()["id"]
    boundary = "unname-1f2e3d4c5b6a"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="Kündigung Größe.txt"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
        "Hans Meier\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    response = api.client.post(
        f"/entries/{entry_id}/files",
        headers={**AUTH, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        content=body,
    )

    assert response.status_code == 202, response.text
    assert [item["name"] for item in response.json()["accepted"]] == ["Kündigung Größe.txt"]


def test_history_rename_clear_and_missing_confirmation(api: Harness) -> None:
    entry = api.client.post("/entries", headers=AUTH, json={"kind": "documents"}).json()
    assert entry["masking"]["categories"][0] == "finance_ids"
    renamed = api.client.patch(
        f"/entries/{entry['id']}", headers=AUTH, json={"title": "Projekt Süd"}
    ).json()
    assert renamed["title"] == "Projekt Süd"
    assert renamed["title_custom"] is True
    assert api.client.delete("/entries", headers=AUTH).status_code == 422
    assert api.client.delete("/entries?confirm=true", headers=AUTH).status_code == 204
    assert api.client.get("/entries", headers=AUTH).json()["count"] == 0
    assert api.client.get(f"/entries/{entry['id']}", headers=AUTH).status_code == 404


def test_rename_keeps_reference_and_draft_state(api: Harness) -> None:
    """History H1/H4: short reference is kept, read-only and survives everything."""

    entry_id, _ = _entry_with_files(api, {"rechnung.txt": DOCUMENT})
    _wait_ready(api, entry_id)
    ready = api.client.get(f"/entries/{entry_id}", headers=AUTH).json()
    reference = ready["display_ref"]
    assert re.fullmatch(r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}", reference)
    listed = api.client.get("/entries", headers=AUTH).json()["entries"]
    assert [item["display_ref"] for item in listed] == [reference]

    rejected = api.client.patch(
        f"/entries/{entry_id}", headers=AUTH, json={"display_ref": "ZZZZZZ"}
    )
    assert rejected.status_code == 422

    renamed = api.client.patch(
        f"/entries/{entry_id}", headers=AUTH, json={"title": "Projekt Nord"}
    ).json()
    assert renamed["title"] == "Projekt Nord"
    assert renamed["title_custom"] is True
    assert renamed["display_ref"] == reference
    assert renamed["draft_revision"] == ready["draft_revision"]
    assert renamed["saved_revision"] == ready["saved_revision"]

    # Additional files: the sidecar no longer changes its own title.
    more = api.client.post(
        f"/entries/{entry_id}/files",
        headers=AUTH,
        files=[("file", ("angebot.txt", DOCUMENT.encode(), "text/plain"))],
    )
    assert more.status_code == 202, more.text
    _wait_ready(api, entry_id)
    after = api.client.get(f"/entries/{entry_id}", headers=AUTH).json()
    assert after["title"] == "Projekt Nord"
    assert after["display_ref"] == reference

    # Restart: history is reloaded, reference stays.
    api.services._entries = None
    restarted = api.client.get(f"/entries/{entry_id}", headers=AUTH).json()
    assert restarted["display_ref"] == reference
    assert restarted["title"] == "Projekt Nord"


def test_apply_v2_rejects_contradictory_request(api: Harness) -> None:
    entry_id, (job_id,) = _entry_with_files(api, {"rechnung.txt": DOCUMENT})
    _wait_ready(api, entry_id)
    response = api.client.post(
        f"/jobs/{job_id}/apply",
        headers=AUTH,
        json={"masking": {**ANONYMIZE, "categories": ["names"]}},
    )
    assert response.status_code == 422
    assert "Hans" not in response.text


# -- JobManager --------------------------------------------------------------


def test_ttl_extension_and_intermediate_counting(tmp_path: Path) -> None:
    now = [datetime(2026, 10, 23, 12, tzinfo=UTC)]
    analyzer = FakeAnalyzer()

    class Runner:
        def analyze(self, text: str, *, progress: Callable[[str], None]) -> DetectionResult:
            return analyzer.analyze_for_profile(text, None, progress=progress)  # type: ignore[arg-type]

    manager = JobManager(
        Runner(),
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=1024,
        root=tmp_path / "jobs",
        clock=lambda: now[0],
    )
    try:
        record = manager.submit_deferred(
            b"Hans Meier",
            filename="a.txt",
            extractor=lambda content, name: DocumentExtractor().extract_bytes(
                content, filename=name
            ),
            profile_id="grundprofil",
            entry_id="e",
        )
        deadline = time.monotonic() + 3
        while manager.get(record.id).status is not JobStatus.COMPLETED:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert [phase["id"] for phase in record.status_dict()["phases"]] == [  # type: ignore[union-attr]
            "extraction",
            "master_data",
            "rules",
            "ner",
            "suspicion",
        ]
        assert record.interim_counts == {"PERSON": 1}
        expires = record.expires_at
        now[0] += timedelta(seconds=50)
        manager.touch([record.id])
        assert record.expires_at == now[0] + timedelta(seconds=60) > expires
        assert manager.jobs_for_entry("e") == (record,)
    finally:
        manager.close()


def test_idle_time_evicts_jobs_only_from_memory(tmp_path: Path) -> None:
    """TTL is an idle period, not a data retention deadline.

    A job with a saved state leaves memory after the idle period and is
    reloaded on the next access via the loader; without a loader (plain text
    without an entry) it is discarded as before.
    """

    now = [datetime(2026, 10, 30, 12, tzinfo=UTC)]
    analyzer = FakeAnalyzer()

    class Runner:
        def analyze(self, text: str, *, progress: Callable[[str], None]) -> DetectionResult:
            return analyzer.analyze_for_profile(text, None, progress=progress)  # type: ignore[arg-type]

    manager = JobManager(
        Runner(),
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=1024,
        root=tmp_path / "jobs",
        clock=lambda: now[0],
    )
    saved: dict[str, Any] = {}
    try:
        record = manager.submit_deferred(
            b"Hans Meier",
            filename="a.txt",
            extractor=lambda content, name: DocumentExtractor().extract_bytes(
                content, filename=name
            ),
            profile_id="grundprofil",
            entry_id="e",
            hooks=JobHooks(
                on_extracted=lambda document: saved.update(document=document),
                on_completed=lambda result: saved.update(result=result),
            ),
        )
        deadline = time.monotonic() + 3
        while manager.get(record.id).status is not JobStatus.COMPLETED or "result" not in saved:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert not hasattr(record, "held")
        assert manager.cleanup_expired(now[0] + timedelta(seconds=30)) == 0
        assert manager.cleanup_expired(now[0] + timedelta(seconds=61)) == 1
        assert not manager.is_loaded(record.id)
        assert not record.workspace.exists()
        with pytest.raises(JobNotFoundError):
            manager.get(record.id)

        manager.set_loader(
            lambda job_id: RestoredJob(
                id=job_id,
                entry_id="e",
                filename="a.txt",
                profile_id="grundprofil",
                source_size=10,
                created_at=record.created_at,
                status="completed",
                extracted_document=saved["document"],
                result=saved["result"],
                write_source=lambda path: path.write_bytes(b"Hans Meier"),
            )
        )
        reloaded = manager.get(record.id)
        assert reloaded is not record
        assert reloaded.status is JobStatus.COMPLETED
        assert manager.get_result(record.id) == saved["result"]
        assert manager.get_source_text(record.id) == "Hans Meier"
        assert reloaded.source_path.read_bytes() == b"Hans Meier"
    finally:
        manager.close()
