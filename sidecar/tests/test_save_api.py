"""Save is the commit, saved, review report, E4, E9."""

# The ``api`` fixture is imported from ``test_api_v2``; as a parameter it hides the import.
# ruff: noqa: F811

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import fitz
import pytest

from presidio_sidecar.saving import output_plan, text_fallback
from test_api_v2 import (
    ALL_CATEGORIES,
    ANONYMIZE,
    AUTH,
    DOCUMENT,
    REVERSIBLE,
    Harness,
    _apply,
    _entry_with_files,
    _wait_ready,
    api,  # noqa: F401
)

# -- Name and format of the protected copy ----------------------------------


def _plan(name: str, fmt: str, *, ocr: bool = False) -> tuple[str, str, bool]:
    plan = output_plan(name, document_format=fmt, ocr_used=ocr)
    return plan.name, plan.format, plan.as_text


def test_protected_copy_name_keeps_the_format() -> None:
    assert _plan("Rechnung.pdf", "pdf") == ("Rechnung_geschützt.pdf", "pdf", False)
    # E-L3 (replaces E12): a scan stays a PDF – overpainted, with text layer.
    assert _plan("Vollmacht.pdf", "pdf", ocr=True) == ("Vollmacht_geschützt.pdf", "pdf", False)
    assert output_plan("Vollmacht.pdf", document_format="pdf", ocr_used=True).scan_layer
    assert not output_plan("Rechnung.pdf", document_format="pdf", ocr_used=False).scan_layer
    # E-L4: an image stays an image, also with codes and placeholders.
    assert _plan("Foto.jpg", "jpg", ocr=True) == ("Foto_geschützt.jpg", "jpg", False)
    assert _plan("Foto.png", "png", ocr=True)[0] == "Foto_geschützt.png"
    assert _plan("Tabelle.xlsx", "xlsx")[0] == "Tabelle_geschützt.xlsx"
    assert _plan("notiz", "txt")[0] == "notiz_geschützt"


def test_fallback_to_text_keeps_name() -> None:
    plan = text_fallback(output_plan("Foto.png", document_format="png", ocr_used=True))
    assert (plan.name, plan.format, plan.as_text) == ("Foto_geschützt.txt", "txt", True)
    english = output_plan("Scan.pdf", document_format="pdf", ocr_used=True, language="en")
    assert text_fallback(english).name == "Scan_protected.txt"


def test_protected_copy_name_is_a_plain_filename() -> None:
    def name(raw: str, language: str = "de") -> str:
        return output_plan(raw, document_format="txt", ocr_used=False, language=language).name

    assert name("../../etc/passwd.txt") == "passwd_geschützt.txt"
    assert name("C:\\Akten\\Brief.txt") == "Brief_geschützt.txt"
    assert name('Kün"dig\nung.txt') == "Kün_dig_ung_geschützt.txt"
    assert name(".versteckt.txt") == "versteckt_geschützt.txt"
    assert name("Brief.txt", "en") == "Brief_protected.txt"
    assert len(name("x" * 400 + ".txt")) < 200


# -- Save ---------------------------------------------------------------


def _save(api: Harness, entry_id: str, masking: dict[str, Any], **body: Any) -> Any:
    return api.client.post(
        f"/entries/{entry_id}/save",
        headers=AUTH,
        json={"masking": masking, **body},
    )


def _publish(api: Harness, entry_id: str, saved: dict[str, Any], target: Path) -> Any:
    """What the shell does: review, copy, report back."""

    target.mkdir(parents=True, exist_ok=True)
    files = []
    for item in saved["staged"]:
        staged = Path(item["path"])
        assert staged.parent.parent == Path(saved["staging_directory"])
        assert staged.parent.name == item["file_id"]
        destination = target / item["name"]
        destination.write_bytes(staged.read_bytes())
        files.append({"file_id": item["file_id"], "path": str(destination)})
    return api.client.post(
        f"/entries/{entry_id}/outputs",
        headers=AUTH,
        json={"directory": str(target), "files": files},
    )


def test_save_writes_mappings_only_now_and_publishes(api: Harness) -> None:
    entry_id, (invoice, note) = _entry_with_files(
        api,
        {"Rechnung_Hofer.txt": DOCUMENT, "notiz.txt": "Hans Meier ruft an."},
    )
    _wait_ready(api, entry_id)
    for _ in range(3):
        preview = _apply(api, invoice, REVERSIBLE)
    assert api.mappings() == 0

    response = _save(api, entry_id, REVERSIBLE)
    assert response.status_code == 200, response.text
    saved = response.json()
    staged = {item["file_id"]: item for item in saved["staged"]}
    assert set(staged) == {invoice, note}
    assert staged[invoice]["name"] == "Rechnung_Hofer_geschützt.txt"
    # What was shown in the window is what gets saved.
    assert Path(staged[invoice]["path"]).read_text(encoding="utf-8") == preview["text"]
    assert "Hans Meier" not in Path(staged[note]["path"]).read_text(encoding="utf-8")

    # Only now the mappings appear in the table – with origin.
    assert saved["committed_mappings"] == api.mappings() > 0
    token = preview["text"].split("Kunde: ", 1)[1].split("\n", 1)[0]
    assert [origin for origin, _ in api.services.vault().token_origins(token)] == [entry_id]

    # The entry is considered saved only after the shell has published it.
    entry = saved["entry"]
    assert entry["status"] == "review"
    output = entry["files"][0]["output"]
    assert output["status"] == "saved"
    assert output["label"] == entry["files"][0]["label"] == "reversible"
    assert output["masked"] == 3
    assert output["masked_by_category"] == {"finance_ids": 1, "names": 2}
    assert output["masked_without_decision"] == 1
    assert output["audit_id"] is not None

    audit = api.services.audit_log().get(output["audit_id"])
    assert audit.context is not None
    assert audit.context.entry_id == entry_id
    assert audit.context.method == "reversible"
    assert audit.context.masked_without_decision == 1
    assert audit.export_sha256 == staged[invoice]["sha256"]

    published = _publish(api, entry_id, saved, api.tmp_path / "Kunden" / "geschützt")
    assert published.status_code == 200, published.text
    entry = published.json()
    assert entry["status"] == "saved"
    assert entry["saved_at"] is not None
    assert entry["output_dir"] == str(api.tmp_path / "Kunden" / "geschützt")
    assert entry["files"][0]["output_path"].endswith("Rechnung_Hofer_geschützt.txt")
    # The staging folder is removed after publishing.
    assert not Path(saved["staging_directory"]).exists()

    # History remains encrypted on disk.
    raw = (api.tmp_path / "state" / "entries.enc").read_bytes()
    assert b"Hans Meier" not in raw
    assert b"geschuetzt" not in raw and "geschützt".encode() not in raw


def test_save_takes_ui_state_and_masks_open_questions(
    api: Harness,
) -> None:
    entry_id, (job_id,) = _entry_with_files(api, {"rechnung.txt": DOCUMENT})
    _wait_ready(api, entry_id)
    findings = api.client.get(f"/jobs/{job_id}/findings", headers=AUTH).json()
    hans = next(item for item in findings["findings"] if item["text"] == "Hans Meier")

    response = _save(
        api,
        entry_id,
        REVERSIBLE,
        files=[
            {
                "file_id": job_id,
                "review": {
                    "decisions": [
                        {"finding_id": hans["id"], "action": "reject", "by": "ai-suggestion"}
                    ],
                    # The preview's "keep" flag is ignored on save.
                    "unresolved": "keep",
                },
            }
        ],
    )
    assert response.status_code == 200, response.text
    text = Path(response.json()["staged"][0]["path"]).read_text(encoding="utf-8")
    assert "Hans Meier" in text
    assert "Petra Novak" not in text
    output = response.json()["entry"]["files"][0]["output"]
    assert output["decided_by_ai"] == 1
    assert output["decided_by_user"] == 0
    assert output["kept_here"] == 1
    assert output["masked_without_decision"] == 1
    # The state lands on the entry – basis for “Edit again”.
    review = response.json()["entry"]["review"][job_id]
    assert review["decisions"] == [
        {"finding_id": hans["id"], "action": "reject", "by": "ai-suggestion"}
    ]

    unknown = _save(api, entry_id, REVERSIBLE, files=[{"file_id": "fremd"}])
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "unknown_file"


def test_irreversible_confirmed_once_per_entry(api: Harness) -> None:
    entry_id, _ = _entry_with_files(api, {"rechnung.txt": DOCUMENT})
    _wait_ready(api, entry_id)

    refused = _save(api, entry_id, ANONYMIZE)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "irreversible_confirmation_required"
    assert api.mappings() == 0
    staging_root = api.services.jobs().root
    assert not any(path.name.startswith("save-") for path in staging_root.iterdir())

    confirmed = _save(api, entry_id, ANONYMIZE, confirm_irreversible=True)
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["entry"]["irreversible_confirmed"] is True
    assert body["committed_mappings"] == 0  # no rollback
    text = Path(body["staged"][0]["path"]).read_text(encoding="utf-8")
    assert "[Name]" in text and "Hans Meier" not in text

    # Compact dialog on the first irreversible save per entry.
    again = _save(api, entry_id, ANONYMIZE)
    assert again.status_code == 200, again.text
    # A new staging folder replaces the old one.
    assert not Path(body["staging_directory"]).exists()


def test_file_without_span_gets_no_unreviewed_copy(api: Harness) -> None:
    entry_id, (invoice, blank) = _entry_with_files(
        api, {"rechnung.txt": DOCUMENT, "leer.txt": "Nichts zu tun hier."}
    )
    _wait_ready(api, entry_id)
    saved = _save(api, entry_id, REVERSIBLE).json()

    assert [item["file_id"] for item in saved["staged"]] == [invoice]
    assert saved["unchanged"] == [blank]
    output = next(item for item in saved["entry"]["files"] if item["id"] == blank)["output"]
    assert output["status"] == "unchanged"
    assert output["name"] is None
    assert output["label"] is None

    # The shell reports exactly the provided files – no more, no less.
    wrong = api.client.post(
        f"/entries/{entry_id}/outputs",
        headers=AUTH,
        json={"directory": str(api.tmp_path), "files": []},
    )
    assert wrong.status_code == 409
    relative = api.client.post(
        f"/entries/{entry_id}/outputs",
        headers=AUTH,
        json={"directory": "geschützt", "files": [{"file_id": invoice, "path": "a.txt"}]},
    )
    assert relative.status_code == 422
    published = _publish(api, entry_id, saved, api.tmp_path / "ziel")
    assert published.status_code == 200
    files = {item["id"]: item for item in published.json()["files"]}
    assert files[blank]["output_path"] is None


def test_pdf_saved_only_after_render_check(api: Harness) -> None:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 90), "Kunde: Hans Meier", fontsize=12)
    page.insert_text((72, 125), "Betrag: 100 EUR", fontsize=12)
    pdf = document.tobytes()
    document.close()

    entry = api.client.post("/entries", headers=AUTH, json={"kind": "documents"}).json()
    response = api.client.post(
        f"/entries/{entry['id']}/files",
        headers=AUTH,
        files=[("file", ("Rechnung.pdf", pdf, "application/pdf"))],
    )
    assert response.status_code == 202, response.text
    _wait_ready(api, entry["id"])

    saved = _save(api, entry["id"], REVERSIBLE)
    assert saved.status_code == 200, saved.text
    staged = saved.json()["staged"][0]
    assert staged["name"] == "Rechnung_geschützt.pdf"
    output = saved.json()["entry"]["files"][0]["output"]
    assert output["rendered_pages"] == 1
    assert output["text_extraction_checked"] is True
    with fitz.open(staged["path"]) as exported:
        text = exported[0].get_text()
    assert "Hans Meier" not in text
    assert "<<PERSON_" in text


def test_nothing_is_saved_during_detection_or_for_text_entries(
    api: Harness,
) -> None:
    text_entry = api.client.post("/entries", headers=AUTH, json={"kind": "text"}).json()
    assert _save(api, text_entry["id"], REVERSIBLE).status_code == 409

    empty = api.client.post("/entries", headers=AUTH, json={"kind": "documents"}).json()
    nothing = _save(api, empty["id"], REVERSIBLE)
    assert nothing.status_code == 409
    assert nothing.json()["detail"]["code"] == "nothing_to_save"

    api.analyzer.release.clear()
    try:
        entry_id, _ = _entry_with_files(api, {"lang.txt": "BITTE-WARTEN Hans Meier"})
        busy = _save(api, entry_id, REVERSIBLE)
        assert busy.status_code == 409
        assert busy.json()["detail"]["code"] == "still_processing"
    finally:
        api.analyzer.release.set()


# -- Edit again (E9) ---------------------------------------------------


def _replace(api: Harness, entry_id: str, file_id: str, name: str, content: str) -> Any:
    return api.client.post(
        f"/entries/{entry_id}/files/{file_id}/replace",
        headers=AUTH,
        files=[("file", (name, content.encode(), "text/plain"))],
    )


def _evict(api: Harness, entry_id: str, file_id: str) -> None:
    """Cooldown over: the job leaves memory once its detection is in the draft state.

    The worker reports a job complete before its hook writes the detection; evicted
    in between, the job would come back from the older manifest as ``queued``.
    """

    key = api.services.entries().draft_key(entry_id)
    assert key is not None
    drafts = api.services.drafts().open(entry_id, key)
    deadline = time.monotonic() + 5
    while drafts.read_manifest(file_id).state != "detected":
        assert time.monotonic() < deadline, "Detection was not saved to the draft state."
        time.sleep(0.01)
    api.services.jobs().delete(file_id)


def test_edit_again_no_longer_needs_original(api: Harness) -> None:
    """After the cooldown, the file is loaded from the saved state without marking as
    expired or re-reading; the same decision is kept."""

    entry_id, (job_id,) = _entry_with_files(api, {"rechnung.txt": DOCUMENT})
    _wait_ready(api, entry_id)
    findings = api.client.get(f"/jobs/{job_id}/findings", headers=AUTH).json()
    petra = next(item for item in findings["findings"] if item["text"] == "Petra Novak")
    keep_petra = {
        "file_id": job_id,
        "review": {"decisions": [{"finding_id": petra["id"], "action": "reject"}]},
    }
    saved = _save(api, entry_id, REVERSIBLE, files=[keep_petra]).json()
    assert _publish(api, entry_id, saved, api.tmp_path / "ziel").status_code == 200

    _evict(api, entry_id, job_id)
    assert not api.services.jobs().is_loaded(job_id)
    entry = api.client.get(f"/entries/{entry_id}", headers=AUTH).json()
    assert entry["files"][0]["status"] == "review"
    assert entry["files"][0]["id"] == job_id
    assert entry["status"] == "saved"
    assert api.client.get(f"/jobs/{job_id}/findings", headers=AUTH).json() == findings
    again = _save(api, entry_id, REVERSIBLE)
    assert again.status_code == 200, again.text
    assert "Petra Novak" in Path(again.json()["staged"][0]["path"]).read_text(encoding="utf-8")


def _forget_draft_keys(api: Harness) -> None:
    """Convert all entries to legacy state (pre-saved, without keys)."""

    store = api.services.entries()
    with store._lock:
        store._draft_keys = {}
        store._persist(store._entries, {})


def test_legacy_entry_rereads_the_original(api: Harness) -> None:
    """Legacy entries (without saved state) keep E9 behavior; afterwards they reside in the
    saved state."""

    entry_id, (job_id,) = _entry_with_files(api, {"rechnung.txt": DOCUMENT})
    _wait_ready(api, entry_id)
    findings = api.client.get(f"/jobs/{job_id}/findings", headers=AUTH).json()
    petra = next(item for item in findings["findings"] if item["text"] == "Petra Novak")
    keep_petra = {
        "file_id": job_id,
        "review": {"decisions": [{"finding_id": petra["id"], "action": "reject"}]},
    }
    saved = _save(api, entry_id, REVERSIBLE, files=[keep_petra]).json()
    assert _publish(api, entry_id, saved, api.tmp_path / "ziel").status_code == 200

    # Legacy, job TTL expired: the file is marked as ``expired`` in the entry.
    _forget_draft_keys(api)
    api.services.jobs().delete(job_id)
    entry = api.client.get(f"/entries/{entry_id}", headers=AUTH).json()
    assert entry["files"][0]["status"] == "expired"
    assert entry["status"] == "saved"
    expired = _save(api, entry_id, REVERSIBLE)
    assert expired.status_code == 409
    assert expired.json()["detail"] == {
        "code": "files_expired",
        "message": expired.json()["detail"]["message"],
        "file_id": job_id,
    }

    # Same original: new ID, same location, decision follows.
    replaced = _replace(api, entry_id, job_id, "rechnung.txt", DOCUMENT)
    assert replaced.status_code == 202, replaced.text
    body = replaced.json()
    assert body["changed"] is False
    new_id = body["file"]["id"]
    assert new_id != job_id
    assert body["entry"]["files"][0]["id"] == new_id
    assert body["entry"]["files"][0]["output"]["status"] == "saved"
    assert body["entry"]["review"][new_id]["decisions"][0]["finding_id"] == petra["id"]
    assert job_id not in body["entry"]["review"]
    _wait_ready(api, entry_id)
    # Re-reading also puts legacy data into the draft state.
    assert api.services.entries().draft_key(entry_id) is not None
    assert api.services.drafts().has_entry(entry_id)
    _evict(api, entry_id, new_id)
    assert (
        api.client.get(f"/entries/{entry_id}", headers=AUTH).json()["files"][0]["status"]
        == "review"
    )
    again = _save(api, entry_id, REVERSIBLE)
    assert again.status_code == 200, again.text
    assert "Petra Novak" in Path(again.json()["staged"][0]["path"]).read_text(encoding="utf-8")
    assert api.client.get(f"/entries/{entry_id}", headers=AUTH).json()["status"] == "saved"

    # A changed original is reported (hash); the decision no longer matches.
    changed = _replace(api, entry_id, new_id, "rechnung.txt", "Neu: " + DOCUMENT)
    assert changed.status_code == 202, changed.text
    assert changed.json()["changed"] is True
    assert changed.json()["file"]["source_changed"] is True

    missing = _replace(api, entry_id, "fremd", "rechnung.txt", DOCUMENT)
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "unknown_file"


# -- Review report --------------------------------------------------------------


def test_review_report_lists_counters_never_contents(api: Harness) -> None:
    entry_id, _ = _entry_with_files(
        api,
        {"Rechnung_Hofer.txt": DOCUMENT, "leer.txt": "Nichts zu tun hier."},
    )
    _wait_ready(api, entry_id)
    masking = {**REVERSIBLE, "categories": ALL_CATEGORIES}
    saved = _save(api, entry_id, masking).json()
    _publish(api, entry_id, saved, api.tmp_path / "ziel")

    response = api.client.get(f"/entries/{entry_id}/report", headers=AUTH)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["profile_version"] == "grundprofil@1.0.0"
    assert report["protection_level"] == 3
    assert [item["name"] for item in report["files"]] == [
        "Rechnung_Hofer_geschützt.txt",
        "leer.txt",
    ]
    totals = report["totals"]
    assert totals["masked"] == 4
    assert totals["masked_by_category"] == {"finance_ids": 1, "names": 2, "organizations": 1}
    assert totals["masked_without_decision"] == 2
    serialized = json.dumps(report, ensure_ascii=False)
    for value in ("Hans Meier", "Petra Novak", "Musterbau", "DE02120300000000202051"):
        assert value not in serialized

    text_entry = api.client.post("/entries", headers=AUTH, json={"kind": "text"}).json()
    assert api.client.get(f"/entries/{text_entry['id']}/report", headers=AUTH).status_code == 409


def test_copy_in_text_mode_logged_content_free(api: Harness) -> None:
    committed = api.client.post(
        "/text/commit",
        headers=AUTH,
        json={"text": "Hans Meier ruft an.", "masking": REVERSIBLE},
    )
    assert committed.status_code == 200, committed.text
    audit = api.services.audit_log().get(1)
    assert audit.kind == "text"
    assert audit.context is not None
    assert audit.context.entry_id == committed.json()["entry"]["id"]
    assert audit.findings_by_entity == {"PERSON": 1}


@pytest.mark.parametrize("path", ["/entries/x/save", "/entries/x/outputs"])
def test_unknown_entry(api: Harness, path: str) -> None:
    body = {"masking": REVERSIBLE} if path.endswith("save") else {"directory": "/tmp", "files": []}
    assert api.client.post(path, headers=AUTH, json=body).status_code == 404
