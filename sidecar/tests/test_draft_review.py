"""AS2: per-entry view, durable decisions before Apply, and stored-original retry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from presidio_sidecar import workflow
from presidio_sidecar.drafts import DraftDeletedError, EntryView
from presidio_sidecar.entries import EntryFile
from presidio_sidecar.extraction import DocumentExtractor
from presidio_sidecar.masking import MaskingSpec
from presidio_sidecar.processing import ProcessingError
from test_api_v2 import AUTH, BLOCK_MARKER, DOCUMENT, REVERSIBLE, FakeAnalyzer, _wait_ready
from test_drafts import CORPUS, NoOcr, _store, _submit, running


def test_view_survives_restart_without_touching_history(tmp_path: Path) -> None:
    with running(tmp_path) as first:
        entry_id, files = _submit(
            first, [("a.txt", DOCUMENT.encode()), ("b.txt", DOCUMENT.encode())]
        )
        _wait_ready(first, entry_id)
        before = first.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        history = (tmp_path / "state" / "entries.enc").read_bytes()
        assert first.client.get(f"/entries/{entry_id}/view", headers=AUTH).json() is None
        view = {
            "active_file_id": files[1],
            "representation": "faithful",
            "page": 3,
            "anchor": 42,
            "focused_question_id": "question-2",
            "preview": "side_by_side",
        }
        response = first.client.put(f"/entries/{entry_id}/view", headers=AUTH, json=view)
        assert response.status_code == 200, response.text
        assert response.json() == view
        assert (tmp_path / "state" / "entries.enc").read_bytes() == history
        assert (
            first.services.entries().get(entry_id).updated_at.isoformat().replace("+00:00", "Z")
            == before["updated_at"]
        )
        path = first.services.drafts().root / entry_id / "view.enc"
        assert b"question-2" not in path.read_bytes()
        assert not (path.parent / files[1] / "view.enc").exists()

    with running(tmp_path) as second:
        assert second.client.get(f"/entries/{entry_id}/view", headers=AUTH).json() == view
        # A damaged optional view falls back without making documents unavailable.
        path.write_bytes(b"broken")
        assert second.client.get(f"/entries/{entry_id}/view", headers=AUTH).json() is None
        entry = second.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert all(item["status"] == "review" for item in entry["files"])


def test_view_validation_and_crypto_binding(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_id, second_id = "ab" * 12, "cd" * 12
    first = store.open(first_id, store.create_key(first_id))
    second = store.open(second_id, store.create_key(second_id))
    first.write_view(EntryView(anchor=7))
    second.write_view(EntryView(anchor=8))
    # Even the optional view is authenticated to its own entry and artifact.
    (store.root / second_id / "view.enc").write_bytes(
        (store.root / first_id / "view.enc").read_bytes()
    )
    assert second.read_view() is None
    store.tombstone(first_id)
    with pytest.raises(DraftDeletedError):
        first.write_view(EntryView(anchor=9))

    with running(tmp_path / "api") as api:
        entry_id, _ = _submit(api, [("a.txt", DOCUMENT.encode())])
        for body in ({"page": 0}, {"anchor": -1}, {"active_file_id": "foreign"}):
            assert (
                api.client.put(f"/entries/{entry_id}/view", headers=AUTH, json=body).status_code
                == 422
            )


def test_review_persists_before_failed_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with running(tmp_path) as first:
        entry_id, (file_id,) = _submit(first, [("a.txt", DOCUMENT.encode())])
        _wait_ready(first, entry_id)
        finding = first.client.get(f"/jobs/{file_id}/findings", headers=AUTH).json()["findings"][0]
        review = {"decisions": [{"finding_id": finding["id"], "action": "reject", "by": "user"}]}

        def fail(*_args: Any, **_kwargs: Any) -> Any:
            # Persistence must already have happened before the expensive computation.
            stored_review = first.services.entries().get(entry_id).review[file_id]
            assert stored_review.decisions[0].action == "reject"
            raise ProcessingError("preview failed")

        monkeypatch.setattr(workflow, "dry_run", fail)
        response = first.client.post(
            f"/jobs/{file_id}/apply",
            headers=AUTH,
            json={"masking": REVERSIBLE, "review": review},
        )
        assert response.status_code == 503, response.text
        saved = first.services.entries().get(entry_id).review[file_id].model_dump(mode="json")
        masking = first.services.entries().get(entry_id).masking.model_dump(mode="json")
        monkeypatch.undo()
    with running(tmp_path) as second:
        entry = second.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert entry["review"][file_id] == saved
        assert entry["masking"] == masking


def test_failed_file_retries_stored_original_after_restart(tmp_path: Path) -> None:
    scan = (CORPUS / "scan.pdf").read_bytes()
    with running(tmp_path, extractor=DocumentExtractor(ocr_backend=NoOcr())) as first:
        entry_id, (file_id,) = _submit(first, [("scan.pdf", scan)])
        _wait_ready(first, entry_id)
        failed = first.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert failed["files"][0]["error_code"] == "ocr_unavailable"
    with running(tmp_path) as second:
        response = second.client.post(f"/entries/{entry_id}/files/{file_id}/retry", headers=AUTH)
        assert response.status_code == 202, response.text
        assert response.json()["file"]["id"] == file_id
        _wait_ready(second, entry_id)
        entry = second.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert entry["files"][0]["status"] == "review"
        assert entry["files"][0]["error_code"] is None
        assert (
            second.client.post(
                f"/entries/{entry_id}/files/{file_id}/retry", headers=AUTH
            ).status_code
            == 409
        )
    with running(tmp_path) as third:
        entry = third.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert entry["files"][0]["status"] == "review"


def test_unavailable_file_can_be_removed_with_cache_and_review(tmp_path: Path) -> None:
    with running(tmp_path) as api:
        entry_id, (file_id, remaining) = _submit(
            api, [("a.txt", DOCUMENT.encode()), ("b.txt", DOCUMENT.encode())]
        )
        _wait_ready(api, entry_id)
        api.client.post(
            f"/jobs/{file_id}/apply",
            headers=AUTH,
            json={"masking": REVERSIBLE, "review": {}},
        )
        path = api.services.drafts().root / entry_id / file_id
        (path / "manifest.enc").write_bytes(b"broken")
        response = api.client.delete(f"/entries/{entry_id}/files/{file_id}", headers=AUTH)
        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()["files"]] == [remaining]
        assert file_id not in response.json()["review"]
        assert not path.exists()
        assert not api.services.jobs().is_loaded(file_id)
        current = api.services.entries().get(entry_id).model_dump(mode="json")
        assert file_id not in json.dumps(current)


def test_cancelled_file_remains_cancelled_and_can_retry_after_restart(tmp_path: Path) -> None:
    analyzer = FakeAnalyzer()
    analyzer.release.clear()
    with running(tmp_path, analyzer=analyzer) as first:
        entry_id, (file_id,) = _submit(first, [("a.txt", (DOCUMENT + BLOCK_MARKER).encode())])
        response = first.client.delete(f"/jobs/{file_id}", headers=AUTH)
        assert response.status_code == 204, response.text
        analyzer.release.set()
        entry = first.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert entry["files"][0]["status"] == "cancelled"
    with running(tmp_path) as second:
        entry = second.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert entry["files"][0]["status"] == "cancelled"
        response = second.client.post(f"/entries/{entry_id}/files/{file_id}/retry", headers=AUTH)
        assert response.status_code == 202, response.text
        _wait_ready(second, entry_id)
        entry = second.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert entry["files"][0]["status"] == "review"


def test_view_does_not_migrate_legacy_files_without_originals(tmp_path: Path) -> None:
    with running(tmp_path) as api:
        store = api.services.entries()
        entry = store.create(
            kind="documents", masking=MaskingSpec.model_validate(REVERSIBLE), title="Legacy"
        )
        file_id = "ab" * 12
        store.update(
            entry.id,
            lambda current: current.model_copy(
                update={
                    "files": (
                        EntryFile(
                            id=file_id, name="a.txt", format="txt", size_bytes=10, status="expired"
                        ),
                    )
                }
            ),
        )
        response = api.client.put(
            f"/entries/{entry.id}/view", headers=AUTH, json={"active_file_id": file_id}
        )
        assert response.status_code == 200, response.text
        assert store.draft_key(entry.id) is None
        assert api.client.get(f"/entries/{entry.id}/view", headers=AUTH).json() is None
        restored = api.client.get(f"/entries/{entry.id}", headers=AUTH).json()
        assert restored["files"][0]["status"] == "expired"


def test_retry_rejects_corrupt_original_without_resetting_failed_manifest(tmp_path: Path) -> None:
    with running(tmp_path, extractor=DocumentExtractor(ocr_backend=NoOcr())) as api:
        entry_id, (file_id,) = _submit(api, [("scan.pdf", (CORPUS / "scan.pdf").read_bytes())])
        _wait_ready(api, entry_id)
        drafts = api.services.drafts().open(entry_id, api.services.entries().draft_key(entry_id))
        assert drafts.read_manifest(file_id).state == "failed"
        (api.services.drafts().root / entry_id / file_id / "original.enc").write_bytes(b"broken")
        response = api.client.post(f"/entries/{entry_id}/files/{file_id}/retry", headers=AUTH)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "files_unavailable"
        assert drafts.read_manifest(file_id).state == "failed"
        assert (
            api.client.get(f"/entries/{entry_id}", headers=AUTH).json()["files"][0]["status"]
            == "unavailable"
        )
