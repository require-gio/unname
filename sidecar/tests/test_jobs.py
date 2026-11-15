"""Job isolation, progress, worker pool and TTL deletion."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest

from presidio_sidecar.detection import (
    DetectionResult,
    DuplicateReviewCapacityError,
    Finding,
    FindingSource,
)
from presidio_sidecar.jobs import (
    JobManager,
    JobNotFoundError,
    JobNotReadyError,
    JobStatus,
)
from presidio_sidecar.processing import ProcessingMode, ProcessingRequest, TextProcessor
from presidio_sidecar.vault import TokenVault


class FakeRunner:
    def analyze(self, text: str, *, progress):  # noqa: ANN001, ANN201
        progress("master_data")
        progress("rules")
        progress("ner")
        progress("suspicion")
        start = text.index("Erika")
        return DetectionResult(
            confirmed=(
                Finding(
                    entity_type="PERSON",
                    start=start,
                    end=start + len("Erika Musterfrau"),
                    score=1.0,
                    source=FindingSource.MASTER_DATA,
                ),
            )
        )


class BlockingRunner:
    def analyze(self, text: str, *, progress):  # noqa: ANN001, ANN201
        progress("ner")
        time.sleep(0.1)
        return DetectionResult()


class LongBlockingRunner:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def analyze(self, text: str, *, progress):  # noqa: ANN001, ANN201
        del text
        progress("ner")
        self.started.set()
        self.release.wait(timeout=2)
        return DetectionResult()


class LeakyErrorRunner:
    def analyze(self, text: str, *, progress):  # noqa: ANN001, ANN201
        progress("ner")
        raise ValueError(f"Fehler in vertraulichem Text: {text}")


class DuplicateCapacityRunner:
    def analyze(self, text: str, *, progress):  # noqa: ANN001, ANN201
        progress("suspicion")
        raise DuplicateReviewCapacityError(f"Zu viele Wiederholungen von {text}")


def _wait(manager: JobManager, job_id: str, timeout: float = 2) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.get(job_id).status_dict()
        if status["status"] in {JobStatus.COMPLETED, JobStatus.FAILED}:
            return status
        time.sleep(0.01)
    raise AssertionError("Job wurde nicht fertig.")


def test_job_lives_in_random_isolated_folder(tmp_path: Path) -> None:
    manager = JobManager(
        FakeRunner(),
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=1024,
        root=tmp_path,
    )
    try:
        first = manager.submit_text("Hallo Erika Musterfrau")
        second = manager.submit_text("Hallo Erika Musterfrau")
        assert first.id != second.id
        assert first.workspace.parent == tmp_path
        assert second.workspace.parent == tmp_path
        assert first.text_path.name == "input.txt"
    finally:
        manager.close()


def test_job_reports_phases_and_result(tmp_path: Path) -> None:
    manager = JobManager(
        FakeRunner(),
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=1024,
        root=tmp_path,
    )
    try:
        job = manager.submit_text("Hallo Erika Musterfrau", filename="../../rechnung.txt")
        status = _wait(manager, job.id)
        assert status["status"] == JobStatus.COMPLETED
        assert status["progress"] == 100
        assert status["filename"] == "rechnung.txt"
        assert all(phase["status"] == "completed" for phase in status["phases"])  # type: ignore[index]
        assert manager.get_result(job.id).confirmed[0].entity_type == "PERSON"
        assert manager.get_source_text(job.id) == "Hallo Erika Musterfrau"
        assert not job.text_path.exists()
    finally:
        manager.close()


def test_older_apply_must_not_overwrite_newer_export_state(
    tmp_path: Path,
) -> None:
    manager = JobManager(
        FakeRunner(),
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=1024,
        root=tmp_path / "jobs",
    )
    vault = TokenVault(tmp_path / "state")
    try:
        job = manager.submit_text("Hallo Erika Musterfrau")
        _wait(manager, job.id)
        text = manager.get_source_text(job.id)
        detection = manager.get_result(job.id)
        processor = TextProcessor(vault)
        older_request = ProcessingRequest(mode=ProcessingMode.REMOVE)
        newer_request = ProcessingRequest(mode=ProcessingMode.PLACEHOLDER)
        older_result = processor.apply(text, detection, older_request)
        newer_result = processor.apply(text, detection, newer_request)

        older_revision = manager.begin_processing(job.id)
        newer_revision = manager.begin_processing(job.id)
        assert manager.store_processing(
            job.id,
            newer_revision,
            newer_request,
            newer_result,
        )
        assert not manager.store_processing(
            job.id,
            older_revision,
            older_request,
            older_result,
        )

        stored_request, stored_result = manager.get_processing(job.id)
        assert stored_request is newer_request
        assert stored_result is newer_result
        snapshot = manager.get_processing_snapshot(job.id)
        assert snapshot.revision == newer_revision
        assert snapshot.request is newer_request
        assert snapshot.result is newer_result

        # If a newer apply has started, the previous completed state must not be
        # overwritten until it finishes successfully.
        pending_revision = manager.begin_processing(job.id)
        with pytest.raises(JobNotReadyError, match="aktuelle Review-Stand"):
            manager.get_processing(job.id)
        assert manager.abandon_processing(job.id, pending_revision)
        restored = manager.get_processing_snapshot(job.id)
        assert restored.revision == newer_revision
        assert manager.begin_processing(job.id) > pending_revision
    finally:
        vault.close()
        manager.close()


def test_findings_before_completion_not_output(tmp_path: Path) -> None:
    manager = JobManager(
        BlockingRunner(),
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=1024,
        root=tmp_path,
    )
    try:
        job = manager.submit_text("Noch nicht fertig")
        with pytest.raises(JobNotReadyError):
            manager.get_result(job.id)
    finally:
        manager.close()


def test_expiry_and_deletion_report_job_id(tmp_path: Path) -> None:
    """Page images and layout maps are removed from memory when the job ends."""

    now = datetime(2026, 7, 30, tzinfo=UTC)
    manager = JobManager(
        FakeRunner(),
        max_workers=1,
        ttl_seconds=10,
        max_text_bytes=1024,
        root=tmp_path,
        clock=lambda: now,
    )
    removed: list[str] = []
    manager.on_removed(removed.append)
    manager.on_removed(lambda _: 1 / 0)  # a broken listener does not interfere
    try:
        expiring = manager.submit_text("Hallo Erika Musterfrau")
        _wait(manager, expiring.id)
        assert manager.cleanup_expired(now + timedelta(seconds=11)) == 1
        assert removed == [expiring.id]
        deleted = manager.submit_text("Hallo Max Mustermann")
        _wait(manager, deleted.id)
        manager.delete(deleted.id)
        assert removed == [expiring.id, deleted.id]
    finally:
        manager.close()


def test_ttl_removes_data_and_status(tmp_path: Path) -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    manager = JobManager(
        FakeRunner(),
        max_workers=1,
        ttl_seconds=10,
        max_text_bytes=1024,
        root=tmp_path,
        clock=lambda: now,
    )
    try:
        job = manager.submit_text("Hallo Erika Musterfrau")
        _wait(manager, job.id)
        workspace = job.workspace
        assert manager.cleanup_expired(now + timedelta(seconds=11)) == 1
        assert not workspace.exists()
        with pytest.raises(JobNotFoundError):
            manager.get(job.id)
    finally:
        manager.close()


def test_lease_protects_apply_and_export_from_delete_and_ttl(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    manager = JobManager(
        FakeRunner(),
        max_workers=1,
        ttl_seconds=10,
        max_text_bytes=1024,
        root=tmp_path,
        clock=lambda: now,
    )
    try:
        job = manager.submit_text("Hallo Erika Musterfrau")
        _wait(manager, job.id)
        expired_at = now + timedelta(seconds=11)

        with manager.lease(job.id):
            assert manager.cleanup_expired(expired_at) == 0
            with pytest.raises(JobNotReadyError, match="verarbeitet oder exportiert"):
                manager.delete(job.id)
            assert job.workspace.exists()

        assert manager.cleanup_expired(expired_at) == 1
        assert not job.workspace.exists()
    finally:
        manager.close()


def test_ttl_janitor_does_not_wait_for_export_under_manager_lock(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    manager = JobManager(
        FakeRunner(),
        max_workers=1,
        ttl_seconds=10,
        max_text_bytes=1024,
        root=tmp_path,
        clock=lambda: now,
    )
    try:
        job = manager.submit_text("Hallo Erika Musterfrau")
        _wait(manager, job.id)
        expired_at = now + timedelta(seconds=11)

        # Simulate a long record lock held by an ongoing PDF verification.
        # cleanup_expired must skip the job instead of acquiring the global lock and affecting
        # health_status.
        with ThreadPoolExecutor(max_workers=1) as executor:
            job.lock.acquire()
            cleanup = executor.submit(manager.cleanup_expired, expired_at)
            try:
                assert cleanup.result(timeout=0.5) == 0
                assert manager.health_status()["healthy"] is True
                assert job.workspace.exists()
            finally:
                # Ensure the test finishes cleanly even if a regression occurs:
                # the previous blocking janitor can still terminate afterwards.
                job.lock.release()

        assert manager.cleanup_expired(expired_at) == 1
        assert not job.workspace.exists()
    finally:
        manager.close()


def test_error_status_does_not_reflect_document_content(
    tmp_path: Path,
) -> None:
    manager = JobManager(
        LeakyErrorRunner(),
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=1024,
        root=tmp_path,
    )
    try:
        job = manager.submit_text("GEHEIMER PERSONENNAME")
        status = _wait(manager, job.id)
        assert status["status"] is JobStatus.FAILED
        assert "GEHEIMER" not in str(status["error"])
        assert status["error"] == "Analyse fehlgeschlagen (ValueError)."
    finally:
        manager.close()


def test_duplicate_capacity_error_provides_safe_helpful_message(
    tmp_path: Path,
) -> None:
    manager = JobManager(
        DuplicateCapacityRunner(),
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=1024,
        root=tmp_path,
    )
    try:
        job = manager.submit_text("GEHEIMER PERSONENNAME")
        status = _wait(manager, job.id)
        assert status["status"] is JobStatus.FAILED
        assert "GEHEIMER" not in str(status["error"])
        assert status["error"] == (
            "Analyse fehlgeschlagen: Zu viele wiederholte Fundstellen fuer ein sicheres Review."
        )
    finally:
        manager.close()


def test_too_large_and_empty_texts_rejected_before_file_creation(tmp_path: Path) -> None:
    manager = JobManager(
        FakeRunner(),
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=4,
        root=tmp_path,
    )
    try:
        with pytest.raises(ValueError, match="leer"):
            manager.submit_text("")
        with pytest.raises(ValueError, match="groesser"):
            manager.submit_text("12345")
        assert not tuple(tmp_path.iterdir())
    finally:
        manager.close()


def test_partial_write_error_leaves_no_plaintext_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = JobManager(
        FakeRunner(),
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=1024,
        root=tmp_path,
    )
    original_write = Path.write_bytes

    def fail_after_partial_write(path: Path, data: bytes) -> int:
        original_write(path, data[:4])
        raise OSError("simulierter Schreibfehler")

    monkeypatch.setattr(Path, "write_bytes", fail_after_partial_write)
    try:
        with pytest.raises(OSError, match="Schreibfehler"):
            manager.submit_text("personenbezogener Klartext")

        assert not tuple(tmp_path.iterdir())
    finally:
        manager.close()


def test_janitor_survives_file_lock_and_retries_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = JobManager(
        FakeRunner(),
        max_workers=1,
        ttl_seconds=0.1,
        max_text_bytes=1024,
        root=tmp_path,
    )
    original_delete = manager._delete_workspace
    delete_calls = 0

    def fail_once(workspace: Path) -> None:
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls == 1:
            raise PermissionError("Datei voruebergehend gesperrt")
        original_delete(workspace)

    monkeypatch.setattr(manager, "_delete_workspace", fail_once)
    try:
        job = manager.submit_text("Hallo Erika Musterfrau")
        _wait(manager, job.id)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and (delete_calls < 2 or job.workspace.exists()):
            time.sleep(0.02)

        assert delete_calls >= 2
        assert not job.workspace.exists()
        assert manager._janitor is not None
        assert manager._janitor.is_alive()
    finally:
        manager.close()


def test_ttl_marks_stuck_job_as_failed_and_deletes_plaintext(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    runner = LongBlockingRunner()
    manager = JobManager(
        runner,
        max_workers=1,
        ttl_seconds=10,
        timeout_seconds=10,
        max_text_bytes=1024,
        root=tmp_path,
        clock=lambda: now,
    )
    try:
        job = manager.submit_text("Klartext darf nicht liegen bleiben")
        assert runner.started.wait(timeout=1)
        assert manager.cleanup_expired(now + timedelta(seconds=11)) == 1
        assert manager.get(job.id).status is JobStatus.FAILED
        assert manager.health_status()["healthy"] is False
        with pytest.raises(RuntimeError, match="Neustart"):
            manager.submit_text("zweiter Job")
        assert not job.workspace.exists()
        assert manager.get(job.id).status is JobStatus.FAILED

        before_close = time.monotonic()
        manager.close()
        assert time.monotonic() - before_close < 0.1
    finally:
        runner.release.set()
        manager.close()


def test_restart_removes_ttl_old_job_root_of_hard_stopped_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orphan = tmp_path / "presidio-sidecar-jobs-999999-verwaist"
    orphan.mkdir()
    (orphan / "input.txt").write_text("personenbezogener Klartext", encoding="utf-8")
    old = time.time() - 120
    import os

    os.utime(orphan, (old, old))
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    manager = JobManager(
        FakeRunner(),
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=1024,
    )
    try:
        assert not orphan.exists()
        assert manager.root.parent == tmp_path
    finally:
        manager.close()


def test_busy_from_submit_to_job_end(tmp_path: Path) -> None:
    """``/health`` reports ``busy``, the harness prevents an activity."""

    runner = LongBlockingRunner()
    manager = JobManager(
        runner,
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=1024,
        root=tmp_path,
    )
    try:
        assert not manager.busy
        job = manager.submit_text("Hallo Erika Musterfrau")
        assert manager.busy
        assert runner.started.wait(timeout=2)
        assert manager.busy
        runner.release.set()
        assert job.future is not None
        job.future.result(timeout=2)
        deadline = time.monotonic() + 2
        while manager.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not manager.busy
    finally:
        runner.release.set()
        manager.close()
