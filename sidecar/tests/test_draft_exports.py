"""AS3: saved entries stay editable, export history, redetect on request, storage."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from presidio_sidecar import draft_jobs, workflow
from presidio_sidecar.detection import DetectionResult, Finding, FindingSource
from presidio_sidecar.entries import (
    DecisionState,
    Entry,
    FileReview,
    ManualState,
    confirmation_key,
)
from presidio_sidecar.extraction import DocumentExtractor
from presidio_sidecar.masking import MaskingSpec
from presidio_sidecar.processing import FindingKind, finding_id
from test_api_v2 import ALL_CATEGORIES, ANONYMIZE, AUTH, DOCUMENT, REVERSIBLE, _apply, _wait_ready
from test_drafts import CORPUS, Harness, RecordingOcr, _submit, running

PSEUDONYMIZE = {**REVERSIBLE, "style": "surrogates"}
MASK = {"method": "irreversible", "style": "mask", "categories": ALL_CATEGORIES, "anonymize": True}


def _entry(api: Harness, entry_id: str) -> dict[str, Any]:
    response = api.client.get(f"/entries/{entry_id}", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def _save(api: Harness, entry_id: str, masking: dict[str, Any], **body: Any) -> Any:
    return api.client.post(
        f"/entries/{entry_id}/save", headers=AUTH, json={"masking": masking, **body}
    )


def _publish(api: Harness, entry_id: str, saved: dict[str, Any], target: Path) -> dict[str, Any]:
    """What the shell does, including its collision-free numbering."""

    target.mkdir(parents=True, exist_ok=True)
    files = []
    for item in saved["staged"]:
        stem, dot, extension = item["name"].rpartition(".")
        destination, number = target / item["name"], 2
        while destination.exists():
            destination = target / f"{stem} ({number}){dot}{extension}"
            number += 1
        destination.write_bytes(Path(item["path"]).read_bytes())
        files.append({"file_id": item["file_id"], "path": str(destination)})
    response = api.client.post(
        f"/entries/{entry_id}/outputs",
        headers=AUTH,
        json={"directory": str(target), "files": files},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _save_and_publish(
    api: Harness, entry_id: str, masking: dict[str, Any], target: Path, **body: Any
) -> dict[str, Any]:
    response = _save(api, entry_id, masking, **body)
    assert response.status_code == 200, response.text
    return _publish(api, entry_id, response.json(), target)


def _changed(entry: dict[str, Any]) -> bool:
    saved = entry["saved_revision"]
    return saved is not None and entry["draft_revision"] > saved


# -- spec §3.1 changed since save ------------------------------------------


def test_revision_counts_changes_but_not_looking(tmp_path: Path) -> None:
    with running(tmp_path) as api:
        entry_id, (file_id,) = _submit(api, [("brief.txt", DOCUMENT.encode())])
        _wait_ready(api, entry_id)
        start = _entry(api, entry_id)["draft_revision"]
        _apply(api, file_id, REVERSIBLE)
        first = _entry(api, entry_id)["draft_revision"]
        # Re-applying the same state (as opening the review does) changes nothing.
        _apply(api, file_id, REVERSIBLE)
        api.client.put(f"/entries/{entry_id}/view", headers=AUTH, json={"page": 2})
        api.client.get(f"/entries/{entry_id}/progress", headers=AUTH)
        assert _entry(api, entry_id)["draft_revision"] == first >= start

        saved = _save_and_publish(api, entry_id, REVERSIBLE, tmp_path / "out")
        assert saved["status"] == "saved"
        assert saved["saved_revision"] == saved["draft_revision"]
        assert not _changed(saved)
        _apply(api, file_id, REVERSIBLE)
        assert not _changed(_entry(api, entry_id))

        finding = api.client.get(f"/jobs/{file_id}/findings", headers=AUTH).json()["findings"][0]
        _apply(
            api, file_id, REVERSIBLE, decisions=[{"finding_id": finding["id"], "action": "reject"}]
        )
        changed = _entry(api, entry_id)
        assert _changed(changed) and changed["status"] == "saved"

    # Survives a restart, and saving again clears it.
    with running(tmp_path) as again:
        assert _changed(_entry(again, entry_id))
        resaved = _save_and_publish(again, entry_id, REVERSIBLE, tmp_path / "out")
        assert not _changed(resaved)
        assert len(resaved["exports"]) == 2


def test_decision_during_publish_is_not_marked_saved(tmp_path: Path) -> None:
    with running(tmp_path) as api:
        entry_id, (file_id,) = _submit(api, [("brief.txt", DOCUMENT.encode())])
        _wait_ready(api, entry_id)
        response = _save(api, entry_id, REVERSIBLE)
        assert response.status_code == 200, response.text
        finding = api.client.get(f"/jobs/{file_id}/findings", headers=AUTH).json()["findings"][0]
        _apply(
            api, file_id, REVERSIBLE, decisions=[{"finding_id": finding["id"], "action": "reject"}]
        )
        published = _publish(api, entry_id, response.json(), tmp_path / "out")
        assert _changed(published)
        assert published["exports"][-1]["revision"] == published["saved_revision"]


# -- Switch method, export history -------------------------------


def test_milestone_anonymize_restart_pseudonymize_keeps_both_exports(tmp_path: Path) -> None:
    target = tmp_path / "geschuetzt"
    with running(tmp_path) as first:
        entry_id, (file_id,) = _submit(first, [("Brief.txt", DOCUMENT.encode())])
        _wait_ready(first, entry_id)
        needs = _save(first, entry_id, ANONYMIZE)
        assert needs.status_code == 409
        assert needs.json()["detail"]["code"] == "irreversible_confirmation_required"
        anonymized = _save_and_publish(
            first, entry_id, ANONYMIZE, target, confirm_irreversible=True
        )
        assert anonymized["irreversible_confirmed"]

    # The original is gone: only the draft store carries the entry from here on.
    with running(tmp_path) as second:
        assert _entry(second, entry_id)["files"][0]["status"] == "review"
        pseudonymized = _save_and_publish(second, entry_id, PSEUDONYMIZE, target)
        exported = sorted(path.name for path in target.iterdir())
        assert exported == ["Brief_geschützt (2).txt", "Brief_geschützt.txt"]
        first_text = (target / "Brief_geschützt.txt").read_text(encoding="utf-8")
        second_text = (target / "Brief_geschützt (2).txt").read_text(encoding="utf-8")
        assert "Hans Meier" not in first_text and "Hans Meier" not in second_text
        assert first_text != second_text

        exports = pseudonymized["exports"]
        assert [item["masking"]["style"] for item in exports] == ["placeholder", "surrogates"]
        assert [Path(item["files"][0]["path"]).name for item in exports] == exported[::-1]
        assert exports[0]["files"][0]["output"]["label"] in {"irreversible", "anonymous"}
        assert exports[1]["files"][0]["output"]["label"] == "reversible"
        # Output and saved_at stay the last save.
        assert pseudonymized["files"][0]["output"]["label"] == "reversible"
        assert pseudonymized["output_dir"] == str(target)

        # One report per save; without an id it is the latest.
        latest = second.client.get(f"/entries/{entry_id}/report", headers=AUTH).json()
        assert latest["export_id"] == exports[1]["id"]
        assert latest["masking"]["style"] == "surrogates"
        earlier = second.client.get(
            f"/entries/{entry_id}/report", headers=AUTH, params={"export_id": exports[0]["id"]}
        ).json()
        assert earlier["masking"]["style"] == "placeholder"
        assert earlier["files"][0]["output"]["sha256"] == exports[0]["files"][0]["output"]["sha256"]
        unknown = second.client.get(
            f"/entries/{entry_id}/report", headers=AUTH, params={"export_id": "00" * 8}
        )
        assert unknown.status_code == 422

        # The history stays content-free.
        assert "Hans Meier" not in json.dumps(pseudonymized)
        assert file_id in {item["file_id"] for item in exports[1]["files"]}


def test_irreversible_confirmation_follows_the_method(tmp_path: Path) -> None:
    with running(tmp_path) as api:
        entry_id, (file_id,) = _submit(api, [("Brief.txt", DOCUMENT.encode())])
        _wait_ready(api, entry_id)
        _save_and_publish(api, entry_id, ANONYMIZE, tmp_path / "a", confirm_irreversible=True)
        # Same method again: no second question.
        assert _save(api, entry_id, ANONYMIZE).status_code == 200
        # Switching style asks again – both directly and after a preview.
        assert _save(api, entry_id, MASK).json()["detail"]["code"] == (
            "irreversible_confirmation_required"
        )
        _apply(api, file_id, MASK)
        assert not _entry(api, entry_id)["irreversible_confirmed"]
        _apply(api, file_id, ANONYMIZE)
        assert _entry(api, entry_id)["irreversible_confirmed"]


def test_legacy_entry_keeps_its_confirmation_and_saved_state() -> None:
    now = datetime.now(UTC)
    masking = MaskingSpec.model_validate(ANONYMIZE)
    legacy = Entry.model_validate(
        {
            "id": "ab" * 12,
            "title": "Alt",
            "kind": "documents",
            "status": "saved",
            "created_at": now,
            "updated_at": now,
            "masking": masking.model_dump(mode="json"),
            "saved_at": now,
            "irreversible_confirmed": True,
        }
    )
    assert legacy.irreversible_confirmed_for == confirmation_key(masking)
    assert legacy.saved_revision == 0 and not legacy.changed_since_save
    assert legacy.exports == ()


def test_export_history_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("presidio_sidecar.saving.MAX_EXPORTS", 3)
    with running(tmp_path) as api:
        entry_id, _ = _submit(api, [("Brief.txt", DOCUMENT.encode())])
        _wait_ready(api, entry_id)
        for _ in range(4):
            entry = _save_and_publish(api, entry_id, REVERSIBLE, tmp_path / "out")
        assert len(entry["exports"]) == 3
        assert len(list((tmp_path / "out").iterdir())) == 4


# -- Redetect after settings change ------------------------------------------------------------


def _redetect_status(api: Harness, entry_id: str) -> list[dict[str, Any]]:
    response = api.client.get(f"/entries/{entry_id}/redetect", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()["files"]


def test_redetect_is_offered_after_settings_change_and_keeps_decisions(tmp_path: Path) -> None:
    with running(tmp_path) as api:
        entry_id, (file_id,) = _submit(api, [("Brief.txt", DOCUMENT.encode())])
        _wait_ready(api, entry_id)
        assert _redetect_status(api, entry_id) == []
        nothing = api.client.post(f"/entries/{entry_id}/redetect", headers=AUTH, json={})
        assert nothing.status_code == 409
        assert nothing.json()["detail"]["code"] == "nothing_to_redetect"

        finding = api.client.get(f"/jobs/{file_id}/findings", headers=AUTH).json()["findings"][0]
        decision = {"finding_id": finding["id"], "action": "reject", "by": "user"}
        _apply(api, file_id, REVERSIBLE, decisions=[decision])
        _save_and_publish(api, entry_id, REVERSIBLE, tmp_path / "out")

        api.services.settings_store().update({"document_languages": {"languages": ["en"]}})
        stale = _redetect_status(api, entry_id)
        assert stale == [{"file_id": file_id, "changes": ["languages"], "reextract": False}]

    # Re-opening never redetects by itself – the offer survives a restart.
    with running(tmp_path) as again:
        calls = again.analyzer.calls
        assert _entry(again, entry_id)["files"][0]["status"] == "review"
        assert again.analyzer.calls == calls
        assert _redetect_status(again, entry_id) == stale

        response = again.client.post(f"/entries/{entry_id}/redetect", headers=AUTH, json={})
        assert response.status_code == 202, response.text
        assert response.json()["files"] == stale
        _wait_ready(again, entry_id)
        entry = _entry(again, entry_id)
        assert again.analyzer.calls == calls + 1
        assert entry["status"] == "saved" and _changed(entry)
        assert entry["files"][0]["id"] == file_id
        assert not entry["files"][0]["extraction_changed"]
        assert entry["review"][file_id]["decisions"] == [decision]
        assert _redetect_status(again, entry_id) == []

        # On explicit request it works without a settings change, too.
        explicit = again.client.post(
            f"/entries/{entry_id}/redetect", headers=AUTH, json={"file_ids": [file_id]}
        )
        assert explicit.status_code == 202, explicit.text
        assert explicit.json()["files"] == [{"file_id": file_id, "changes": [], "reextract": False}]
        _wait_ready(again, entry_id)
        foreign = again.client.post(
            f"/entries/{entry_id}/redetect", headers=AUTH, json={"file_ids": ["ef" * 16]}
        )
        assert foreign.status_code == 422


def test_redetect_rereads_a_scan_from_the_stored_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan = (CORPUS / "scan.pdf").read_bytes()
    extractor = DocumentExtractor(ocr_backend=RecordingOcr())
    with running(tmp_path, extractor=extractor) as api:
        entry_id, (file_id,) = _submit(api, [("scan.pdf", scan)])
        _wait_ready(api, entry_id)
        before = api.client.get(f"/jobs/{file_id}/findings", headers=AUTH).json()
        monkeypatch.setattr(draft_jobs, "selected_ocr_engine", lambda: "other-engine")
        stale = _redetect_status(api, entry_id)
        assert stale == [{"file_id": file_id, "changes": ["ocr_engine"], "reextract": True}]

        reads: list[str] = []
        original = extractor.extract_bytes

        def counting(content: bytes, **kwargs: Any) -> Any:
            reads.append(kwargs["filename"])
            return original(content, **kwargs)

        monkeypatch.setattr(extractor, "extract_bytes", counting)
        response = api.client.post(f"/entries/{entry_id}/redetect", headers=AUTH, json={})
        assert response.status_code == 202, response.text
        _wait_ready(api, entry_id)
        assert reads == ["scan.pdf"]
        after = api.client.get(f"/jobs/{file_id}/findings", headers=AUTH).json()
        assert after["text"] == before["text"]
        assert not _entry(api, entry_id)["files"][0]["extraction_changed"]
        assert _redetect_status(api, entry_id) == []


def test_migrate_review_keeps_only_what_still_exists_on_same_text() -> None:
    old = "Kunde Hofer zahlt an Meier."
    new = "Kunde Hofer zahlt an Huber."
    hofer = Finding("PERSON", 6, 11, 0.9, FindingSource.NER, "Hofer", "x")
    meier = Finding("PERSON", 21, 26, 0.9, FindingSource.NER, "Meier", "x")
    detection = DetectionResult(confirmed=(hofer, meier), suspicious=())
    review = FileReview(
        decisions=(
            DecisionState(
                finding_id=finding_id(hofer, kind=FindingKind.CONFIRMED), action="reject"
            ),
            DecisionState(
                finding_id=finding_id(meier, kind=FindingKind.CONFIRMED), action="reject"
            ),
            DecisionState(finding_id="f_unknown", action="accept"),
        ),
        manual=(
            ManualState(entity_type="PERSON", start=0, end=5),
            ManualState(entity_type="PERSON", start=21, end=26),
        ),
    )
    migrated, changed = workflow.migrate_review(review, old, new, detection)
    assert changed
    assert [item.finding_id for item in migrated.decisions] == [review.decisions[0].finding_id]
    assert [(item.start, item.end) for item in migrated.manual] == [(0, 5)]
    assert workflow.migrate_review(review, old, old, detection) == (review, False)
    # A shorter text drops what no longer fits.
    short, _ = workflow.migrate_review(review, old, "Kunde", detection)
    assert short.decisions == () and [(m.start, m.end) for m in short.manual] == [(0, 5)]


def test_pipeline_changes_ignore_unknowns_and_versions() -> None:
    stored = {"app": "1", "languages": "de", "ner": "gliner", "ocr_engine": "vision"}
    assert draft_jobs.pipeline_changes(stored, {**stored, "app": "2"}) == []
    assert draft_jobs.pipeline_changes(stored, {"languages": "en", "ocr_engine": "vision"}) == [
        "languages"
    ]
    assert draft_jobs.pipeline_changes({}, {"languages": "en"}) == []


# Storage handling -----------------------------------------------------------


def test_storage_shows_usage_and_deletion_date_and_drops_after_delete(tmp_path: Path) -> None:
    with running(tmp_path) as api:
        empty = api.client.get("/storage", headers=AUTH).json()
        assert empty["total_bytes"] == 0 and empty["entries"] == []
        assert empty["free_bytes"] is None or empty["free_bytes"] > 0
        assert empty["low_space_bytes"] > 0

        entry_id, _ = _submit(api, [("Brief.txt", DOCUMENT.encode())])
        _wait_ready(api, entry_id)
        before = api.client.get("/storage", headers=AUTH).json()
        other_id, _ = _submit(api, [("Notiz.txt", (DOCUMENT * 50).encode())])
        _wait_ready(api, other_id)
        usage = api.client.get("/storage", headers=AUTH).json()
        sizes = {item["entry_id"]: item["bytes"] for item in usage["entries"]}
        assert sizes[other_id] > sizes[entry_id] > len(DOCUMENT)
        assert usage["total_bytes"] == sum(sizes.values())
        retention = usage["retention_days"]
        assert retention == 30
        entry = api.services.entries().get(other_id)
        delete_at = next(item for item in usage["entries"] if item["entry_id"] == other_id)
        expected = entry.updated_at + timedelta(days=retention)
        assert datetime.fromisoformat(delete_at["delete_at"].replace("Z", "+00:00")) == expected

        assert api.client.delete(f"/entries/{other_id}", headers=AUTH).status_code == 204
        after = api.client.get("/storage", headers=AUTH).json()
        assert after["total_bytes"] == before["total_bytes"]

        api.services.settings_store().update({"history": {"retention_days": None}})
        never = api.client.get("/storage", headers=AUTH).json()
        assert never["retention_days"] is None
        assert never["entries"][0]["delete_at"] is None
