"""Persistent encrypted draft state.

The milestone requires that after a restart – even after ``kill -9`` – opening a draft
without “Reload” shows the same findings and decisions, the same error remains,
and after deletion nothing remains on disk or in the sidecar for that draft.
"""

# The ``api`` fixture comes from ``test_api_v2``; as a parameter it hides the import.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from PIL import Image

from presidio_sidecar import drafts as drafts_module
from presidio_sidecar import workflow
from presidio_sidecar.app import create_app
from presidio_sidecar.config import Settings
from presidio_sidecar.detection import DetectionResult, Finding, FindingSource, LanguageSegment
from presidio_sidecar.detection.second_opinion import Assessment
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.drafts import (
    DraftDeletedError,
    DraftManifest,
    DraftStore,
    DraftUnavailableError,
    decode_detection,
    decode_document,
    encode_detection,
    encode_document,
)
from presidio_sidecar.entries import FORMAT_HEADER as ENTRIES_HEADER
from presidio_sidecar.entries import STORE_SCHEMA_VERSION, EntryStore
from presidio_sidecar.extraction import (
    DocumentExtractor,
    DocumentFormat,
    OcrUnavailableError,
    OcrWord,
    structured_payload,
)
from presidio_sidecar.jobs import JobManager, JobStatus, RestoredJob
from presidio_sidecar.masking import MaskingSpec
from presidio_sidecar.processing import FindingKind, finding_id
from presidio_sidecar.profiles import Profile, load_profiles
from presidio_sidecar.registry_crypto import load_or_create_registry_key
from presidio_sidecar.services import PhaseOneServices
from presidio_sidecar.workers import WorkerCapacity
from test_api_v2 import (
    AUTH,
    DOCUMENT,
    REVERSIBLE,
    TOKEN,
    FakeAnalyzer,
    Harness,
    _apply,
    _entry_with_files,
    _wait_ready,
    api,  # noqa: F401
)

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "testdata" / "layout"
TESTS = Path(__file__).resolve().parent
ENTRY = "ab" * 12
OTHER_ENTRY = "cd" * 12
FILE = "12" * 16
OTHER_FILE = "34" * 16


# -- Helpers -------------------------------------------------------------------


def _store(tmp_path: Path) -> DraftStore:
    return DraftStore(tmp_path / "drafts.noindex", tmp_path / "drafts.key")


def _manifest(file_id: str = FILE, state: str = "ingested") -> DraftManifest:
    now = datetime(2026, 11, 14, 9, tzinfo=UTC)
    return DraftManifest(
        file_id=file_id,
        state=state,  # type: ignore[arg-type]
        filename="rechnung.txt",
        format="txt",
        size_bytes=3,
        source_sha256="0" * 64,
        profile_id="grundprofil",
        created_at=now,
        updated_at=now,
    )


class RecordingOcr:
    """Fixed words per image – OCR without engine, with confidence."""

    def recognize(self, image: Image.Image, *, languages: tuple[str, ...]) -> tuple[OcrWord, ...]:
        width, height = image.size
        return (
            OcrWord(
                "Kunde",
                left=width // 10,
                top=height // 10,
                width=width // 8,
                height=height // 20,
                confidence=0.935,
                line_id=(1, 1, 1, 1),
            ),
            OcrWord(
                "Brandstetter",
                left=width // 3,
                top=height // 10,
                width=width // 4,
                height=height // 20,
                confidence=0.7125,
                line_id=(1, 1, 1, 1),
            ),
        )


class NoOcr:
    def recognize(self, image: Image.Image, *, languages: tuple[str, ...]) -> tuple[OcrWord, ...]:
        raise OcrUnavailableError("Keine Texterkennung auf diesem Geraet.")


class CorpusAnalyzer(FakeAnalyzer):
    """Detects capitalised words, alternating between confirmed and suspicious,
    and adds language zones and second-opinion estimates."""

    def analyze_for_profile(
        self,
        text: str,
        profile: Profile,
        *,
        progress: Callable[[str], None],
    ) -> DetectionResult:
        self.calls += 1
        for phase in ("master_data", "rules", "ner", "suspicion"):
            progress(phase)
        words = list(re.finditer(r"[A-ZÄÖÜ][a-zäöüß]{3,}", text))
        confirmed = tuple(
            Finding("PERSON", m.start(), m.end(), 0.9137, FindingSource.NER, m.group(), "corpus")
            for m in words[0::2]
        )
        suspicious = tuple(
            Finding("PERSON", m.start(), m.end(), 0.31, FindingSource.SUSPICION, m.group(), "c")
            for m in words[1::2]
        )
        middle = len(text) // 2
        return DetectionResult(
            confirmed=confirmed,
            suspicious=suspicious,
            languages=(
                LanguageSegment(0, middle, "de"),
                LanguageSegment(middle, len(text), "en", detected=False),
            ),
            assessments={
                finding_id(finding, kind=FindingKind.SUSPICIOUS): Assessment(
                    label="person",
                    confidence=0.7123456789,
                    protect=0.8123,
                    suggestion="accept",
                    decided=False,
                    model="test-model",
                )
                for finding in suspicious[:3]
            },
        )


@contextmanager
def running(
    root: Path,
    *,
    analyzer: FakeAnalyzer | None = None,
    extractor: DocumentExtractor | None = None,
    ttl: float = 60,
) -> Iterator[Harness]:
    """A sidecar bound to fixed directories; restarting it twice triggers a fresh start."""

    settings = Settings(
        token=TOKEN,
        state_directory=root / "state",
        job_ttl_seconds=ttl,
        max_text_bytes=4 * 1024 * 1024,
    )
    recorder = DiagnosticsRecorder()
    used = analyzer or FakeAnalyzer()
    jobs = JobManager(
        used,  # type: ignore[arg-type]
        max_workers=2,
        ttl_seconds=ttl,
        max_text_bytes=settings.max_text_bytes,
        root=root / "jobs",
    )
    services = PhaseOneServices(
        settings,
        recorder,
        analyzer=used,  # type: ignore[arg-type]
        jobs=jobs,
        extractor=extractor or DocumentExtractor(ocr_backend=RecordingOcr()),
        profiles=load_profiles(),
        capacity=WorkerCapacity(1, 0, 2, 8 * 1024**3),
    )
    services.second_opinion_model = lambda: None  # type: ignore[method-assign]
    with TestClient(create_app(settings, recorder, phase_one=services)) as client:
        services.wait_for_startup()
        yield Harness(client, services, used, root)
    used.release.set()


def _submit(api: Harness, files: list[tuple[str, bytes]]) -> tuple[str, list[str]]:
    entry = api.client.post("/entries", headers=AUTH, json={"kind": "documents"})
    assert entry.status_code == 201, entry.text
    entry_id = entry.json()["id"]
    response = api.client.post(
        f"/entries/{entry_id}/files",
        headers=AUTH,
        files=[("file", (name, content, "application/octet-stream")) for name, content in files],
    )
    assert response.status_code == 202, response.text
    assert not response.json()["rejected"], response.text
    return entry_id, [item["id"] for item in response.json()["accepted"]]


def _decision(api: Harness, job_id: str) -> dict[str, Any]:
    findings = api.client.get(f"/jobs/{job_id}/findings", headers=AUTH).json()["findings"]
    return {"finding_id": findings[0]["id"], "action": "reject"} if findings else {}


def _entries_plaintext(state: Path) -> dict[str, Any]:
    cipher = AESGCM(load_or_create_registry_key(state / "entries.key"))
    payload = (state / "entries.enc").read_bytes()
    nonce_end = len(ENTRIES_HEADER) + 12
    plaintext = cipher.decrypt(
        payload[len(ENTRIES_HEADER) : nonce_end], payload[nonce_end:], ENTRIES_HEADER
    )
    return json.loads(plaintext)


def _wait(condition: Callable[[], bool], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "Zeitlimit beim Warten"
        time.sleep(0.01)


# -- Encryption -----------------------------------------------------------


def test_artifacts_are_encrypted_and_private(tmp_path: Path) -> None:
    store = _store(tmp_path)
    drafts = store.open(ENTRY, store.create_key(ENTRY))
    drafts.write_original(FILE, b"Hans Meier, IBAN DE02120300000000202051")
    drafts.write_manifest(FILE, _manifest())

    directory = store.root / ENTRY / FILE
    for artifact in ("original.enc", "manifest.enc"):
        raw = (directory / artifact).read_bytes()
        assert raw.startswith(drafts_module.FORMAT_HEADER)
        assert b"Hans" not in raw and b"rechnung" not in raw
        if os.name == "posix":
            assert (directory / artifact).stat().st_mode & 0o777 == 0o600
    if os.name == "posix":
        assert store.root.stat().st_mode & 0o777 == 0o700
        assert directory.stat().st_mode & 0o777 == 0o700
        assert (tmp_path / "drafts.key").stat().st_mode & 0o777 == 0o600
    target = tmp_path / "source.txt"
    assert drafts.restore_original(FILE, target) == 39
    assert target.read_bytes().startswith(b"Hans Meier")
    assert drafts.read_manifest(FILE) == _manifest()


@pytest.fixture
def small_blocks(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(drafts_module, "BLOCK_SIZE", 64)
    return 64


def _blocks(path: Path) -> tuple[bytes, list[bytes]]:
    raw = path.read_bytes()
    header_size = len(drafts_module.FORMAT_HEADER) + 1 + drafts_module.NONCE_PREFIX_SIZE
    chunk = drafts_module.BLOCK_SIZE + drafts_module.TAG_SIZE
    body = raw[header_size:]
    return raw[:header_size], [body[index : index + chunk] for index in range(0, len(body), chunk)]


@pytest.mark.parametrize("size", [0, 1, 63, 64, 65, 64 * 5, 64 * 5 + 7])
def test_blocks_survive_any_length(tmp_path: Path, small_blocks: int, size: int) -> None:
    store = _store(tmp_path)
    drafts = store.open(ENTRY, store.create_key(ENTRY))
    payload = os.urandom(size)
    drafts.write_original(FILE, payload)
    target = tmp_path / "out.bin"
    assert drafts.restore_original(FILE, target) == size
    assert target.read_bytes() == payload


@pytest.mark.parametrize("tamper", ["swap", "truncate_block", "truncate_tail", "flip", "extend"])
def test_manipulated_blocks_return_error_instead_of_data(
    tmp_path: Path, small_blocks: int, tamper: str
) -> None:
    store = _store(tmp_path)
    drafts = store.open(ENTRY, store.create_key(ENTRY))
    drafts.write_original(FILE, os.urandom(64 * 4 + 10))
    path = store.root / ENTRY / FILE / "original.enc"
    header, blocks = _blocks(path)
    if tamper == "swap":
        blocks[0], blocks[1] = blocks[1], blocks[0]
    elif tamper == "truncate_block":
        # Whole blocks cut off: the new last block wasn't marked as final.
        blocks = blocks[:-1]
    elif tamper == "truncate_tail":
        blocks[-1] = blocks[-1][:-3]
    elif tamper == "flip":
        blocks[2] = bytes([blocks[2][0] ^ 1]) + blocks[2][1:]
    elif tamper == "extend":
        blocks.append(blocks[0])
    path.write_bytes(header + b"".join(blocks))
    target = tmp_path / "out.bin"
    with pytest.raises(DraftUnavailableError):
        drafts.restore_original(FILE, target)
    # No partial data should remain.
    assert not target.exists()


def test_swapped_artifact_files_and_entries_are_detected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    drafts = store.open(ENTRY, store.create_key(ENTRY))
    other = store.open(OTHER_ENTRY, store.create_key(OTHER_ENTRY))
    for handle in (drafts, other):
        for file_id in (FILE, OTHER_FILE):
            handle.write_original(file_id, b"Original " + file_id.encode())
            handle.write_manifest(file_id, _manifest(file_id))

    def swap(source: Path, target: Path) -> None:
        target.write_bytes(source.read_bytes())

    root = store.root
    # Different file of same entry.
    swap(root / ENTRY / OTHER_FILE / "original.enc", root / ENTRY / FILE / "original.enc")
    with pytest.raises(DraftUnavailableError):
        drafts.restore_original(FILE, tmp_path / "a")
    # Different entry, same file ID.
    swap(
        root / OTHER_ENTRY / OTHER_FILE / "manifest.enc", root / ENTRY / OTHER_FILE / "manifest.enc"
    )
    with pytest.raises(DraftUnavailableError):
        drafts.read_manifest(OTHER_FILE)
    # Different artifact type.
    swap(root / OTHER_ENTRY / FILE / "manifest.enc", root / OTHER_ENTRY / FILE / "original.enc")
    with pytest.raises(DraftUnavailableError):
        other.restore_original(FILE, tmp_path / "b")


def test_foreign_key_or_entry_decrypts_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    key = store.create_key(ENTRY)
    drafts = store.open(ENTRY, key)
    drafts.write_manifest(FILE, _manifest())

    # The packed DEK belongs to exactly one entry (AAD).
    with pytest.raises(DraftUnavailableError):
        store.open(OTHER_ENTRY, key)
    # A different DEK cannot read the artifacts.
    stranger = store.open(ENTRY, store.create_key(ENTRY))
    with pytest.raises(DraftUnavailableError):
        stranger.read_manifest(FILE)
    # A different primary key does not unpack the DEK.
    elsewhere = DraftStore(store.root, tmp_path / "anderer.key")
    with pytest.raises(DraftUnavailableError):
        elsewhere.open(ENTRY, key)
    with pytest.raises(DraftUnavailableError):
        store.open(ENTRY, "kein-base64!")


def test_write_abort_leaves_no_valid_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    drafts = store.open(ENTRY, store.create_key(ENTRY))
    drafts.write_original(FILE, b"erstes Original")
    drafts.write_manifest(FILE, _manifest())

    def crash(_source: object, _target: object) -> None:
        raise OSError("Platte voll")

    monkeypatch.setattr(drafts_module.os, "replace", crash)
    with pytest.raises(drafts_module.DraftError):
        drafts.write_original(FILE, b"zweites, nie fertig geschrieben")
    monkeypatch.undo()
    # The previous state remains, no ``*.tmp`` files are left behind.
    assert drafts.restore_original(FILE, tmp_path / "out") == len(b"erstes Original")
    assert not list((store.root / ENTRY / FILE).glob("*.tmp"))
    assert not list((store.root / ENTRY / FILE).glob(".*.tmp"))

    # Original without manifest: the file is considered missing.
    drafts.write_original(OTHER_FILE, b"ohne Manifest")
    assert not drafts.has_file(OTHER_FILE)
    with pytest.raises(DraftUnavailableError):
        drafts.read_manifest(OTHER_FILE)


def test_tombstone_blocks_late_writers(tmp_path: Path) -> None:
    store = _store(tmp_path)
    drafts = store.open(ENTRY, store.create_key(ENTRY))
    drafts.write_manifest(FILE, _manifest())

    inside = threading.Event()
    release = threading.Event()
    original_writing = store._writing

    @contextmanager
    def slow_writing(entry_id: str, file_id: str) -> Iterator[None]:
        with original_writing(entry_id, file_id):
            inside.set()
            assert release.wait(5)
            yield

    store._writing = slow_writing  # type: ignore[method-assign]
    writer = threading.Thread(target=lambda: drafts.write_original(FILE, b"x"))
    writer.start()
    assert inside.wait(5)
    tombstoned = threading.Event()
    blocker = threading.Thread(target=lambda: (store.tombstone(ENTRY), tombstoned.set()))
    blocker.start()
    # The tombstone waits for the ongoing writer …
    time.sleep(0.05)
    assert not tombstoned.is_set()
    release.set()
    writer.join(5)
    blocker.join(5)
    assert tombstoned.is_set()
    store._writing = original_writing  # type: ignore[method-assign]
    # … and after that no one writes any more.
    with pytest.raises(DraftDeletedError):
        drafts.write_detection(FILE, DetectionResult())
    with pytest.raises(DraftDeletedError):
        store.open(ENTRY, store.create_key(ENTRY))


def test_cleaner_removes_only_old_orphans(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for entry_id in (ENTRY, OTHER_ENTRY):
        handle = store.open(entry_id, store.create_key(entry_id))
        for file_id in (FILE, OTHER_FILE):
            handle.write_manifest(file_id, _manifest(file_id))
    leftover = store.root / ENTRY / FILE / ".manifest.enc.abc.tmp"
    leftover.write_bytes(b"halb")
    old = time.time() - 3600
    for path in (
        store.root / OTHER_ENTRY,
        store.root / ENTRY / OTHER_FILE,
        leftover,
    ):
        os.utime(path, (old, old))

    # Freshly created and not yet known: stays (reading in progress).
    fresh = store.root / ("ef" * 12)
    fresh.mkdir()
    removed = store.sweep({ENTRY: {FILE}})
    assert removed == 3
    assert not (store.root / OTHER_ENTRY).exists()
    assert not (store.root / ENTRY / OTHER_FILE).exists()
    assert not leftover.exists()
    assert (store.root / ENTRY / FILE / "manifest.enc").exists()
    assert fresh.exists()
    assert store.usage().entries.keys() == {ENTRY, "ef" * 12}


# -- Round-Trip: Extraction and detection ---------------------------------------

CORPUS_FILES = [
    ("lieferschein.pdf", "Text-PDF"),
    ("scan.pdf", "Scan-PDF (OCR)"),
    ("bildschirmfoto.png", "Bild"),
    ("foto-exif6.jpg", "Bild (EXIF)"),
    ("brief.docx", "DOCX"),
    ("kunden.xlsx", "XLSX"),
    ("kunden.csv", "CSV"),
]


@pytest.mark.parametrize("name", [name for name, _ in CORPUS_FILES])
def test_extraction_survives_round_trip(name: str) -> None:
    profile_structured = "presidio_structured"
    extractor = DocumentExtractor(ocr_backend=RecordingOcr())
    document = extractor.extract_bytes(
        (CORPUS / name).read_bytes(),
        filename=name,
        structured_data=profile_structured,
    )
    encoded = json.loads(json.dumps(encode_document(document)))
    restored = decode_document(
        encoded,
        structured=lambda text, fmt: structured_payload(
            text, fmt, structured_data=profile_structured
        ),
    )
    # Offsets, boxes, confidence, pages, method – all identical.
    assert restored == document
    assert restored.segments == document.segments
    if document.format is DocumentFormat.CSV:
        assert document.structured is not None and restored.structured is not None
        assert restored.structured.columns == document.structured.columns
        assert restored.structured.data.equals(document.structured.data)
    if name in {"scan.pdf", "bildschirmfoto.png"}:
        assert restored.ocr_used
        assert {segment.confidence for segment in restored.segments} == {0.935, 0.7125}


def test_detection_survives_round_trip() -> None:
    text = "Sehr geehrte Frau Hofer, Ihr Termin bei Musterbau GmbH in Linz."
    result = CorpusAnalyzer().analyze_for_profile(text, None, progress=lambda _phase: None)  # type: ignore[arg-type]
    assert result.assessments and result.languages
    restored = decode_detection(json.loads(json.dumps(encode_detection(result))))
    assert restored == result
    assert dict(restored.assessments) == dict(result.assessments)
    with pytest.raises(DraftUnavailableError):
        decode_detection({**encode_detection(result), "v": 99})


# -- Restart -------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [name for name, _ in CORPUS_FILES] + ["../regression/lieferschein-moebelhaus.txt"],
)
def test_restart_shows_same_findings_and_same_apply(tmp_path: Path, name: str) -> None:
    path = (CORPUS / name).resolve()
    with running(tmp_path, analyzer=CorpusAnalyzer()) as first:
        entry_id, (job_id,) = _submit(first, [(path.name, path.read_bytes())])
        _wait_ready(first, entry_id)
        findings = first.client.get(f"/jobs/{job_id}/findings", headers=AUTH).json()
        decision = _decision(first, job_id)
        before = _apply(first, job_id, REVERSIBLE, decisions=[decision] if decision else [])
        entry_before = first.client.get(f"/entries/{entry_id}", headers=AUTH).json()

    with running(tmp_path, analyzer=CorpusAnalyzer()) as second:
        entry = second.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert [item["status"] for item in entry["files"]] == ["review"]
        assert entry["files"][0]["id"] == job_id
        assert entry["review"] == entry_before["review"]
        assert entry["files"][0]["counts"] == entry_before["files"][0]["counts"]
        # Reopening does not recompute.
        assert second.analyzer.calls == 0
        assert second.client.get(f"/jobs/{job_id}/findings", headers=AUTH).json() == findings
        after = _apply(second, job_id, REVERSIBLE, decisions=[decision] if decision else [])
        assert after == before
        layout = second.client.get(f"/jobs/{job_id}/layout", headers=AUTH)
        assert layout.status_code == 200, layout.text
        if path.suffix in {".pdf", ".png", ".jpg"}:
            scale = layout.json()["pages"][0]["scales"][0]
            page = second.client.get(f"/jobs/{job_id}/pages/0?scale={scale}", headers=AUTH)
            assert page.status_code == 200, page.text
            assert page.headers["content-type"] == "image/png"


def test_restart_with_decisions_and_multiple_files(tmp_path: Path) -> None:
    with running(tmp_path) as first:
        entry_id, (one, two) = _submit(
            first,
            [("a.txt", DOCUMENT.encode()), ("b.txt", ("Petra Novak " + DOCUMENT).encode())],
        )
        _wait_ready(first, entry_id)
        decision = _decision(first, one)
        _apply(first, one, REVERSIBLE, decisions=[decision])
        reviewed = first.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert reviewed["review"][one]["decisions"][0]["finding_id"] == decision["finding_id"]

    with running(tmp_path) as second:
        entry = second.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert {item["id"]: item["status"] for item in entry["files"]} == {
            one: "review",
            two: "review",
        }
        assert "expired" not in json.dumps(entry)
        assert entry["review"] == reviewed["review"]
        assert entry["title"] == reviewed["title"]
        progress = second.client.get(f"/entries/{entry_id}/progress", headers=AUTH).json()
        assert progress["ready"] and progress["done"] == 2


def test_failed_file_shows_same_error_after_restart(tmp_path: Path) -> None:
    scan = (CORPUS / "scan.pdf").read_bytes()
    with running(tmp_path, extractor=DocumentExtractor(ocr_backend=NoOcr())) as first:
        entry_id, (job_id, _) = _submit(first, [("scan.pdf", scan), ("a.txt", DOCUMENT.encode())])
        _wait_ready(first, entry_id)
        entry = first.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert entry["files"][0]["status"] == "failed"
        assert entry["files"][0]["error_code"] == "ocr_unavailable"

    with running(tmp_path) as second:
        entry = second.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert entry["files"][0]["status"] == "failed"
        assert entry["files"][0]["error_code"] == "ocr_unavailable"
        progress = second.client.get(f"/entries/{entry_id}/progress", headers=AUTH).json()
        assert progress["files"][0]["error_code"] == "ocr_unavailable"
        # The error stays the error: nothing is silently repeated.
        assert second.services.jobs().get(job_id).status is JobStatus.FAILED


def test_interrupted_detection_continues_on_next_start(tmp_path: Path) -> None:
    analyzer = FakeAnalyzer()
    analyzer.release.clear()
    with running(tmp_path, analyzer=analyzer) as first:
        entry_id, (job_id,) = _submit(first, [("lang.txt", b"BITTE-WARTEN Hans Meier")])
        _wait(lambda: first.services.jobs().get(job_id).status is JobStatus.RUNNING)
        # Extraction already in draft state, detection not yet.
        _wait(
            lambda: (
                workflow.draft_jobs.entry_drafts(first.services, entry_id)
                .read_manifest(job_id)
                .state
                == "extracted"
            )
        )
    analyzer.release.set()

    counting = DocumentExtractor()
    calls: list[str] = []
    original = counting.extract_bytes

    def extract_bytes(content: bytes, **options: Any) -> Any:
        calls.append(options["filename"])
        return original(content, **options)

    counting.extract_bytes = extract_bytes  # type: ignore[method-assign]
    with running(tmp_path, extractor=counting) as second:
        assert workflow.resume_interrupted(second.services) in {0, 1}
        body = _wait_ready(second, entry_id)
        assert body["files"][0]["status"] == "review"
        findings = second.client.get(f"/jobs/{job_id}/findings", headers=AUTH).json()
        assert {item["text"] for item in findings["findings"]} == {"Hans Meier"}
        # Continued at detection step, not during extraction.
        assert calls == []
        assert (
            workflow.draft_jobs.entry_drafts(second.services, entry_id).read_manifest(job_id).state
            == "detected"
        )


_KILL_SCRIPT = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from test_api_v2 import DOCUMENT, FakeAnalyzer
from presidio_sidecar import workflow
from presidio_sidecar.config import Settings
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.extraction import DocumentExtractor
from presidio_sidecar.jobs import JobManager
from presidio_sidecar.profiles import load_profiles
from presidio_sidecar.services import PhaseOneServices
from presidio_sidecar.workers import WorkerCapacity

root, marker = Path(sys.argv[2]), Path(sys.argv[3])

class StuckExtractor(DocumentExtractor):
    def extract_bytes(self, *args, **kwargs):
        marker.write_text("mitten im Auslesen")
        time.sleep(3600)

settings = Settings(token="t", state_directory=root / "state", max_text_bytes=1 << 22)
analyzer = FakeAnalyzer()
services = PhaseOneServices(
    settings, DiagnosticsRecorder(), analyzer=analyzer,
    jobs=JobManager(analyzer, max_workers=1, ttl_seconds=60, max_text_bytes=1 << 22,
                    root=root / "jobs"),
    extractor=StuckExtractor(), profiles=load_profiles(),
    capacity=WorkerCapacity(1, 0, 2, 8 * 1024**3),
)
services.second_opinion_model = lambda: None
entry = services.entries().create(
    kind="documents", masking=workflow.default_masking(services), title="Dokument",
    draft_key=services.drafts().create_key,
)
_, accepted, _ = workflow.submit_files(
    services, entry.id, [workflow.Upload("rechnung.txt", DOCUMENT.encode())]
)
print(entry.id, accepted[0].id, flush=True)
time.sleep(3600)
"""


@pytest.mark.skipif(os.name != "posix", reason="kill -9 gibt es nur auf POSIX")
def test_after_kill_9_during_extraction_detection_continues(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    child = subprocess.Popen(
        [sys.executable, "-c", _KILL_SCRIPT, str(TESTS), str(tmp_path), str(marker)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        entry_id, job_id = child.stdout.readline().split()
        _wait(marker.exists, timeout=60)
        os.kill(child.pid, signal.SIGKILL)
        child.wait(10)
    finally:
        if child.poll() is None:
            child.kill()
    assert child.returncode == -signal.SIGKILL

    with running(tmp_path) as restarted:
        entry = restarted.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        assert entry["files"][0]["id"] == job_id
        assert entry["files"][0]["status"] in {"queued", "processing", "review"}
        body = _wait_ready(restarted, entry_id)
        assert body["files"][0]["status"] == "review"
        findings = restarted.client.get(f"/jobs/{job_id}/findings", headers=AUTH).json()
        assert {item["text"] for item in findings["findings"]} >= {"Hans Meier", "Petra Novak"}


# -- Idle time and memory cap ----------------------------------------------------


def test_idle_time_and_cap_free_memory(tmp_path: Path) -> None:
    now = [datetime(2026, 11, 14, 12, tzinfo=UTC)]
    analyzer = FakeAnalyzer()
    manager = JobManager(
        analyzer,  # type: ignore[arg-type]
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=1024,
        root=tmp_path / "jobs",
        clock=lambda: now[0],
        max_loaded_jobs=2,
        eviction_grace_seconds=0,
    )
    document = DocumentExtractor().extract_bytes(b"Hans Meier", filename="a.txt")
    result = DetectionResult()

    def spec(job_id: str) -> RestoredJob:
        return RestoredJob(
            id=job_id,
            entry_id="e",
            filename="a.txt",
            profile_id="grundprofil",
            source_size=10,
            created_at=now[0],
            status="completed",
            extracted_document=document,
            result=result,
            write_source=lambda path: path.write_bytes(b"Hans Meier"),
        )

    manager.set_loader(spec)
    try:
        ids = [f"{index:032x}" for index in range(1, 4)]
        for job_id in ids:
            now[0] += timedelta(seconds=1)
            manager.get(job_id)
        # At most two are loaded; the longest idle one was evicted.
        assert [manager.is_loaded(job_id) for job_id in ids] == [False, True, True]
        assert not (tmp_path / "jobs" / ids[0]).exists()
        # And it returns on the next access.
        assert manager.get(ids[0]).status is JobStatus.COMPLETED
        assert sum(manager.is_loaded(job_id) for job_id in ids) == 2
    finally:
        manager.close()


# -- Delete -------------------------------------------------------------------


def _assert_gone(api: Harness, entry_id: str, job_ids: list[str]) -> None:
    services = api.services
    assert not (services.drafts().root / entry_id).exists()
    assert services.entries().draft_key(entry_id) is None
    assert entry_id not in _entries_plaintext(api.tmp_path / "state").get("draft_keys", {})
    for job_id in job_ids:
        assert not services.jobs().is_loaded(job_id)
        assert services.job_memory().get(job_id, "probe") is None
        assert not (api.tmp_path / "jobs" / job_id).exists()
        assert api.client.get(f"/jobs/{job_id}/findings", headers=AUTH).status_code == 404


@pytest.mark.parametrize("way", ["delete", "clear", "retention"])
def test_deletion_leaves_no_entry_behind(tmp_path: Path, way: str) -> None:
    with running(tmp_path) as api:
        entry_id, job_ids = _submit(api, [("a.txt", DOCUMENT.encode()), ("b.txt", b"Hans Meier")])
        keep_id, _ = _submit(api, [("c.txt", b"Petra Novak")])
        _wait_ready(api, entry_id)
        _wait_ready(api, keep_id)
        for job_id in job_ids:
            api.services.job_memory().put(job_id, "probe", b"Seitenbild")
        assert (api.services.drafts().root / entry_id).is_dir()
        assert api.services.entries().draft_key(entry_id) is not None

        if way == "delete":
            response = api.client.delete(f"/entries/{entry_id}", headers=AUTH)
            assert response.status_code == 204
        elif way == "clear":
            response = api.client.delete("/entries?confirm=true", headers=AUTH)
            assert response.status_code == 204
        else:
            store = api.services.entries()
            clock = store._clock
            store._clock = lambda: datetime.now(UTC) - timedelta(days=40)
            store.update(entry_id, lambda entry: entry.model_copy(update={"title": "Alt"}))
            store._clock = clock
            api.services.settings_store().update({"history": {"retention_days": 30}})
            assert api.services.purge_expired_entries() == 1

        _assert_gone(api, entry_id, job_ids)
        assert api.client.get(f"/entries/{entry_id}", headers=AUTH).status_code == 404
        if way == "clear":
            assert api.services.drafts().usage().bytes == 0
        else:
            assert (api.services.drafts().root / keep_id).is_dir()
            kept = api.client.get(f"/entries/{keep_id}", headers=AUTH).json()
            assert kept["files"][0]["status"] == "review"


class BlockingOcr(RecordingOcr):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def recognize(self, image: Image.Image, *, languages: tuple[str, ...]) -> tuple[OcrWord, ...]:
        self.started.set()
        assert self.release.wait(10)
        try:
            return super().recognize(image, languages=languages)
        finally:
            self.finished.set()


def test_deletion_during_ocr_writes_nothing_afterwards(tmp_path: Path) -> None:
    ocr = BlockingOcr()
    with running(tmp_path, extractor=DocumentExtractor(ocr_backend=ocr)) as api:
        entry_id, (job_id,) = _submit(api, [("scan.pdf", (CORPUS / "scan.pdf").read_bytes())])
        assert ocr.started.wait(10)
        assert api.client.delete(f"/entries/{entry_id}", headers=AUTH).status_code == 204
        root = api.services.drafts().root
        assert not (root / entry_id).exists()
        ocr.release.set()
        assert ocr.finished.wait(10)
        time.sleep(0.2)
        # The worker finished and wrote nothing further.
        assert not (root / entry_id).exists()
        assert not api.services.jobs().is_loaded(job_id)
        assert api.client.get(f"/entries/{entry_id}", headers=AUTH).status_code == 404


# -- Unreadable -------------------------------------------------------------------


def test_unreadable_draft_becomes_unavailable_and_selecting_original_helps(
    tmp_path: Path,
) -> None:
    with running(tmp_path) as first:
        entry_id, (job_id, intact) = _submit(
            first, [("a.txt", DOCUMENT.encode()), ("b.txt", b"Hans Meier")]
        )
        _wait_ready(first, entry_id)
    detection = tmp_path / "state" / "drafts.noindex" / entry_id / job_id / "detection.enc"
    raw = bytearray(detection.read_bytes())
    raw[-1] ^= 0xFF
    detection.write_bytes(bytes(raw))

    with running(tmp_path) as second:
        entry = second.client.get(f"/entries/{entry_id}", headers=AUTH).json()
        statuses = {item["id"]: item["status"] for item in entry["files"]}
        assert statuses == {job_id: "unavailable", intact: "review"}
        saved = second.client.post(
            f"/entries/{entry_id}/save", headers=AUTH, json={"masking": REVERSIBLE}
        )
        assert saved.status_code == 409
        assert saved.json()["detail"]["code"] == "files_unavailable"
        replaced = second.client.post(
            f"/entries/{entry_id}/files/{job_id}/replace",
            headers=AUTH,
            files=[("file", ("a.txt", DOCUMENT.encode(), "text/plain"))],
        )
        assert replaced.status_code == 202, replaced.text
        new_id = replaced.json()["file"]["id"]
        _wait_ready(second, entry_id)
        assert not (tmp_path / "state" / "drafts.noindex" / entry_id / job_id).exists()
        statuses = {
            item["id"]: item["status"]
            for item in second.client.get(f"/entries/{entry_id}", headers=AUTH).json()["files"]
        }
        assert statuses == {new_id: "review", intact: "review"}


def test_startup_sync_marks_missing_manifests(tmp_path: Path) -> None:
    with running(tmp_path) as first:
        entry_id, (job_id,) = _submit(first, [("a.txt", DOCUMENT.encode())])
        _wait_ready(first, entry_id)
    (tmp_path / "state" / "drafts.noindex" / entry_id / job_id / "manifest.enc").unlink()
    with running(tmp_path) as second:
        # Even the history (without opening the entry) lists the file.
        listed = second.client.get("/entries", headers=AUTH).json()["entries"]
        assert listed[0]["files"][0]["status"] == "unavailable"


# -- History: keys and old entries -----------------------------------------


def test_key_never_leaves_sidecar(api: Harness) -> None:
    entry_id, _ = _entry_with_files(api, {"a.txt": DOCUMENT})
    _wait_ready(api, entry_id)
    key = api.services.entries().draft_key(entry_id)
    assert key is not None
    for response in (
        api.client.get("/entries", headers=AUTH),
        api.client.get(f"/entries/{entry_id}", headers=AUTH),
        api.client.get(f"/entries/{entry_id}/progress", headers=AUTH),
    ):
        assert key not in response.text
        assert "draft_key" not in response.text
    stored = _entries_plaintext(api.tmp_path / "state")
    assert stored["schema_version"] == STORE_SCHEMA_VERSION == 4
    assert stored["draft_keys"] == {entry_id: key}


def test_history_schema_one_loads_unchanged(tmp_path: Path) -> None:
    key_path = tmp_path / "entries.key"
    cipher = AESGCM(load_or_create_registry_key(key_path))
    masking = MaskingSpec(**REVERSIBLE)
    document = {
        "schema_version": 1,
        "entries": [
            {
                "id": ENTRY,
                "title": "Rechnung",
                "kind": "documents",
                "status": "saved",
                "created_at": "2026-10-01T09:00:00Z",
                "updated_at": "2026-10-01T09:00:00Z",
                "masking": json.loads(masking.model_dump_json()),
                "files": [
                    {
                        "id": FILE,
                        "name": "rechnung.pdf",
                        "format": "pdf",
                        "size_bytes": 10,
                        "status": "expired",
                    }
                ],
            }
        ],
    }
    nonce = os.urandom(12)
    plaintext = json.dumps(document).encode()
    (tmp_path / "entries.enc").write_bytes(
        ENTRIES_HEADER + nonce + cipher.encrypt(nonce, plaintext, ENTRIES_HEADER)
    )
    store = EntryStore(tmp_path / "entries.enc", key_path)
    entry = store.get(ENTRY)
    assert entry.files[0].status == "expired"
    assert store.draft_key(ENTRY) is None
    assert store.ensure_draft_key(ENTRY, lambda _id: "verpackt") == "verpackt"
    assert EntryStore(tmp_path / "entries.enc", key_path).draft_key(ENTRY) == "verpackt"


# -- Disk full ------------------------------------------------------------------


def test_full_disk_rejects_file_instead_of_saving_half(
    api: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def disk_full(_path: Path, _chunks: Iterator[bytes]) -> None:
        raise OSError(28, "No space left on device")

    entry = api.client.post("/entries", headers=AUTH, json={"kind": "documents"}).json()
    monkeypatch.setattr(drafts_module, "_atomic_write", disk_full)
    response = api.client.post(
        f"/entries/{entry['id']}/files",
        headers=AUTH,
        files=[("file", ("a.txt", DOCUMENT.encode(), "text/plain"))],
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["accepted"] == []
    assert [item["code"] for item in body["rejected"]] == ["storage_failed"]
    assert "Hans" not in response.text
    assert body["entry"]["files"] == []
    # No job, no partial state.
    assert api.services.jobs().jobs_for_entry(entry["id"]) == ()
    entry_directory = api.services.drafts().root / entry["id"]
    assert not entry_directory.exists() or not any(entry_directory.iterdir())
    assert any(item.category == "draft_write_failed" for item in api.services.diagnostics.entries())

    monkeypatch.undo()
    entry_id, _ = _entry_with_files(api, {"a.txt": DOCUMENT})
    assert _wait_ready(api, entry_id)["files"][0]["status"] == "review"


def test_cleared_job_temp_reprovides_original(api: Harness) -> None:
    """The job temp is cached and may disappear at any time."""

    entry_id, (job_id,) = _entry_with_files(api, {"a.txt": DOCUMENT})
    _wait_ready(api, entry_id)
    record = api.services.jobs().get(job_id)
    shutil.rmtree(api.services.jobs().root)
    response = api.client.get(f"/jobs/{job_id}/layout", headers=AUTH)
    assert response.status_code == 200, response.text
    assert record.source_path.read_bytes() == DOCUMENT.encode()
    if os.name == "posix":
        assert api.services.jobs().root.stat().st_mode & 0o777 == 0o700
