"""Document flow via API v2: entry → detection → apply → save.

Until phase L, ``test_phase_two_api.py`` and ``test_phase_three_api.py`` shared the same
POST /jobs and /jobs/{id}/export contracts. Those routes are gone; cases now use the
single new endpoint ``POST /entries/{id}/save``:

- each format keeps the original and writes a new, reviewed copy with audit
  (DOCX, XLSX, CSV, PDF, PNG, JPG; an image whose code OCR cannot read back as text),
- PDF: full editorial, render and text-extraction review, decisions per place even for
  identical and multi-line values,
- the five UI styles and the categories up to location and date,
- the round-trip code → language model → plain text survives a restart.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

import fitz
import pytest
from docx import Document
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from PIL import Image

from presidio_sidecar import saving
from presidio_sidecar.app import create_app
from presidio_sidecar.catalog import CATEGORY_IDS
from presidio_sidecar.config import Settings
from presidio_sidecar.detection import (
    EXACT_DUPLICATE_DETECTOR,
    DetectionPipeline,
    DetectionResult,
    EntityThresholds,
    Finding,
    FindingSource,
)
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.exporting import sha256_file
from presidio_sidecar.extraction import DocumentExtractor, OcrWord
from presidio_sidecar.jobs import JobManager
from presidio_sidecar.profiles import Profile, load_profiles
from presidio_sidecar.services import PhaseOneServices
from presidio_sidecar.workers import WorkerCapacity

TOKEN = "documents-v2-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SECRET = "Hans Meier"
MULTILINE_SECRET = "Hans\nMeier"
ALL_CATEGORIES = list(CATEGORY_IDS)
DEFAULT_CATEGORIES = ["names", "addresses", "contact", "numbers"]
CODES = {"method": "reversible", "style": "codes", "categories": DEFAULT_CATEGORIES}
REMOVE = {"method": "irreversible", "style": "remove", "categories": DEFAULT_CATEGORIES}
MASK = {"method": "irreversible", "style": "mask", "categories": DEFAULT_CATEGORIES}
MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".txt": "text/plain",
}


class DeterministicAnalyzer:
    """Fixed matches without models; location and date for categories."""

    VALUES = {
        SECRET: "PERSON",
        MULTILINE_SECRET: "PERSON",
        "DE02120300000000202051": "IBAN_CODE",
        "Wien": "LOCATION",
        "03.04.2026": "DATE_TIME",
    }

    def nlp_status(self) -> dict[str, Any]:
        return {"active": "documents-v2-offline", "loaded": True, "device": "cpu"}

    def analyze_for_profile(
        self,
        text: str,
        profile: Profile,
        *,
        progress: Callable[[str], None],
    ) -> DetectionResult:
        for phase in ("master_data", "rules", "ner", "suspicion"):
            progress(phase)
        findings: list[Finding] = []
        for value, entity_type in self.VALUES.items():
            offset = 0
            while (start := text.find(value, offset)) >= 0:
                findings.append(
                    Finding(
                        entity_type=entity_type,
                        start=start,
                        end=start + len(value),
                        score=0.95,
                        source=FindingSource.NER,
                        text=value,
                        detector="documents-v2-offline",
                    )
                )
                offset = start + len(value)
        return DetectionResult(confirmed=tuple(sorted(findings, key=lambda item: item.start)))


class FirstLocationOnlyNer:
    """Specific model bug: only the first ``LG-Wels`` is detected."""

    def detect(self, text: str) -> tuple[Finding, ...]:
        value = "LG-Wels"
        start = text.index(value)
        return (
            Finding(
                entity_type="LOCATION",
                start=start,
                end=start + len(value),
                score=0.73,
                source=FindingSource.NER,
                text=value,
                detector="first-location-only",
            ),
        )


class DuplicateAwareAnalyzer:
    """Real pipeline with position-accurate repeat search."""

    def __init__(self) -> None:
        self.pipeline = DetectionPipeline(
            FirstLocationOnlyNer(),
            business_terms=(),
            sentence_starters=(),
        )
        self.thresholds = EntityThresholds({"LOCATION": 0.55}, default=0.55)

    def nlp_status(self) -> dict[str, Any]:
        return {"active": "first-location-only", "loaded": True, "device": "cpu"}

    def analyze_for_profile(
        self,
        text: str,
        profile: Profile,
        *,
        progress: Callable[[str], None],
    ) -> DetectionResult:
        progress("master_data")
        progress("rules")
        return self.pipeline.analyze(
            text,
            progress=progress,
            allowed_entities=set(profile.presidio_entities),
            thresholds=self.thresholds,
            suspicion_enabled=profile.suspect_check.enabled,
            sentence_starters=(),
        )


class FixedImageOcr:
    """Deterministic boxes with still real image extraction."""

    def recognize(self, image: object, *, languages: tuple[str, ...]) -> tuple[OcrWord, ...]:
        return (
            OcrWord(
                "Hans", left=20, top=12, width=28, height=16, line_id=(1, 1, 1, 1), confidence=0.99
            ),
            OcrWord(
                "Meier", left=52, top=12, width=32, height=16, line_id=(1, 1, 1, 1), confidence=0.99
            ),
        )


@dataclass(frozen=True, slots=True)
class Harness:
    client: TestClient
    services: PhaseOneServices
    tmp_path: Path


def _services(
    tmp_path: Path, analyzer: Any, state: str = "state"
) -> tuple[Settings, Any, PhaseOneServices]:
    settings = Settings(
        token=TOKEN,
        state_directory=tmp_path / state,
        job_ttl_seconds=60,
        max_text_bytes=1024 * 1024,
    )
    recorder = DiagnosticsRecorder()
    jobs = JobManager(
        analyzer,
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=settings.max_text_bytes,
        root=tmp_path / f"{state}-jobs",
    )
    services = PhaseOneServices(
        settings,
        recorder,
        analyzer=analyzer,
        jobs=jobs,
        extractor=DocumentExtractor(ocr_backend=FixedImageOcr()),
        profiles=load_profiles(),
        capacity=WorkerCapacity(1, 0, 2, 8 * 1024**3),
    )
    # No second opinion: development model must not make decisions here (see test_api_v2).
    services.second_opinion_model = lambda: None  # type: ignore[method-assign]
    return settings, recorder, services


@pytest.fixture
def api(tmp_path: Path) -> Iterator[Harness]:
    settings, recorder, services = _services(tmp_path, DeterministicAnalyzer())
    with TestClient(create_app(settings, recorder, phase_one=services)) as client:
        yield Harness(client, services, tmp_path)


@pytest.fixture
def duplicate_api(tmp_path: Path) -> Iterator[Harness]:
    settings, recorder, services = _services(tmp_path, DuplicateAwareAnalyzer())
    with TestClient(create_app(settings, recorder, phase_one=services)) as client:
        yield Harness(client, services, tmp_path)


# -- Helpers ------------------------------------------------------------------


def _submit(api: Harness, source: Path) -> tuple[str, str]:
    entry = api.client.post("/entries", headers=AUTH, json={"kind": "documents"})
    assert entry.status_code == 201, entry.text
    entry_id = entry.json()["id"]
    response = api.client.post(
        f"/entries/{entry_id}/files",
        headers=AUTH,
        files=[("file", (source.name, source.read_bytes(), MEDIA_TYPES[source.suffix]))],
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert not body["rejected"], body
    file_id = body["accepted"][0]["id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        progress = api.client.get(f"/entries/{entry_id}/progress", headers=AUTH).json()
        if progress["done"] == progress["total"]:
            assert progress["files"][0]["status"] == "review", progress
            return entry_id, file_id
        time.sleep(0.01)
    raise AssertionError("Erkennung wurde nicht fertig.")


def _findings(api: Harness, file_id: str) -> list[dict[str, Any]]:
    response = api.client.get(f"/jobs/{file_id}/findings", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()["findings"]


def _decisions(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"finding_id": finding_id, "action": action} for finding_id, action in pairs]


def _save(
    api: Harness,
    entry_id: str,
    masking: dict[str, Any],
    file_id: str | None = None,
    decisions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    files = [{"file_id": file_id, "review": {"decisions": decisions or []}}] if file_id else []
    response = api.client.post(
        f"/entries/{entry_id}/save",
        headers=AUTH,
        json={"masking": masking, "files": files, "confirm_irreversible": True},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _staged(api: Harness, saved: dict[str, Any], source: Path) -> Path:
    """Returned copy includes audit data; original file stays unchanged."""

    (staged,) = saved["staged"]
    path = Path(staged["path"])
    assert path != source
    assert path.parent.parent == Path(saved["staging_directory"])
    assert sha256_file(path) == staged["sha256"]
    output = saved["entry"]["files"][0]["output"]
    audit = api.services.audit_log().get(output["audit_id"])
    assert audit.export_sha256 == staged["sha256"]
    assert audit.context is not None
    assert audit.context.entry_id == saved["entry"]["id"]
    return path


def _raw_pdf_characters(page: fitz.Page) -> str:
    raw = page.get_text("rawdict")
    return "".join(
        str(character.get("c", ""))
        for block in raw.get("blocks", [])
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        for character in span.get("chars", [])
    )


def _pdf(path: Path, lines: list[tuple[int, str]], *, pages: int = 1) -> Path:
    document = fitz.open()
    for _ in range(pages):
        page = document.new_page(width=595, height=842)
        for y, text in lines:
            page.insert_text((72, y), text, fontsize=12)
    document.save(path)
    document.close()
    return path


def _image(path: Path) -> Path:
    image = Image.new("RGB", (120, 50), "white")
    for x in range(18, 88):
        for y in range(10, 31):
            image.putpixel((x, y), (190, 30, 30))
    image.save(path)
    return path


# -- Formats -----------------------------------------------------------------


def test_docx_keeps_original_and_writes_new_file_with_audit(api: Harness) -> None:
    source = api.tmp_path / "rechnung.docx"
    document = Document()
    document.add_heading("Rechnung", level=1)
    document.add_paragraph(f"Kunde: {SECRET}")
    document.add_paragraph("Betrag: 100 EUR")
    document.save(source)
    source_hash = sha256_file(source)

    entry_id, _ = _submit(api, source)
    saved = _save(api, entry_id, REMOVE)
    path = _staged(api, saved, source)

    assert sha256_file(source) == source_hash
    assert path.name == "rechnung_geschützt.docx"
    text = "\n".join(paragraph.text for paragraph in Document(path).paragraphs)
    assert SECRET not in text
    assert "Betrag: 100 EUR" in text


def test_xlsx_keeps_original_and_formula(api: Harness) -> None:
    source = api.tmp_path / "kunden.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Kunden"
    sheet["A1"] = "Name"
    sheet["B1"] = "Status"
    sheet["A2"] = f"{SECRET} / aktiv"
    sheet["B2"] = "=LEN(A2)"
    workbook.save(source)
    workbook.close()
    source_hash = sha256_file(source)

    entry_id, _ = _submit(api, source)
    path = _staged(api, _save(api, entry_id, REMOVE), source)

    assert sha256_file(source) == source_hash
    exported = load_workbook(path, data_only=False)
    assert exported["Kunden"]["A2"].value == " / aktiv"
    assert exported["Kunden"]["B2"].value == "=LEN(A2)"
    exported.close()


def test_csv_keeps_raw_structure(api: Harness) -> None:
    source = api.tmp_path / "kunden.csv"
    source.write_text(f"name;status\r\n{SECRET};aktiv\r\n", encoding="utf-8", newline="")
    source_hash = sha256_file(source)

    entry_id, _ = _submit(api, source)
    path = _staged(api, _save(api, entry_id, REMOVE), source)

    assert sha256_file(source) == source_hash
    with path.open("r", encoding="utf-8", newline="") as exported:
        assert exported.read() == "name;status\r\n;aktiv\r\n"


def test_pdf_remove_is_real_redaction_with_render_and_text_check(api: Harness) -> None:
    source = _pdf(
        api.tmp_path / "rechnung.pdf", [(90, f"Invoice for {SECRET}"), (125, "Amount: 100 EUR")]
    )
    source_hash = sha256_file(source)

    entry_id, _ = _submit(api, source)
    saved = _save(api, entry_id, REMOVE)
    path = _staged(api, saved, source)
    output = saved["entry"]["files"][0]["output"]

    assert sha256_file(source) == source_hash
    assert output["rendered_pages"] == 1
    assert output["text_extraction_checked"] is True
    with fitz.open(path) as redacted:
        page = redacted[0]
        assert SECRET not in page.get_text("text")
        assert SECRET not in " ".join(str(word[4]) for word in page.get_text("words"))
        assert SECRET not in _raw_pdf_characters(page)
        assert "Amount: 100 EUR" in page.get_text("text")
        assert page.get_pixmap(alpha=False).samples


def test_pdf_manual_span_masks_every_occurrence_and_saves(api: Harness) -> None:
    """Regression Polizze: the number appears on every page, only one was marked.

    Previously saving only replaced the marked spot, and the PDF post-check aborted
    with “nothing was saved”.
    """

    source = _pdf(api.tmp_path / "polizze.pdf", [(90, "Polizze Nr. 1132784527")], pages=3)
    entry_id, file_id = _submit(api, source)
    text = api.client.get(f"/jobs/{file_id}/findings", headers=AUTH).json()["text"]
    start = text.index("1132784527")
    review = {"manual": [{"entity_type": "CUSTOMER_NUMBER", "start": start, "end": start + 10}]}

    # Preview already shows what will be saved.
    preview = api.client.post(
        f"/jobs/{file_id}/apply", headers=AUTH, json={"masking": CODES, "review": review}
    )
    assert preview.status_code == 200, preview.text
    assert "1132784527" not in preview.json()["text"]

    response = api.client.post(
        f"/entries/{entry_id}/save",
        headers=AUTH,
        json={"masking": CODES, "files": [{"file_id": file_id, "review": review}]},
    )
    assert response.status_code == 200, response.text
    with fitz.open(_staged(api, response.json(), source)) as exported:
        for page in exported:
            assert "1132784527" not in page.get_text("text")
            assert "1132784527" not in _raw_pdf_characters(page)


def test_pdf_repetition_is_a_question_and_is_masked_in_doubt(
    duplicate_api: Harness,
) -> None:
    api = duplicate_api
    source = api.tmp_path / "doppeltes-lg-wels.pdf"
    document = fitz.open()
    document.new_page(width=595, height=842).insert_text((72, 90), "Registergericht: LG-Wels")
    document.new_page(width=595, height=842).insert_text((72, 90), "Footer: LG-Wels")
    document.save(source)
    document.close()
    places = {**REMOVE, "style": "placeholder", "categories": ALL_CATEGORIES, "anonymize": True}

    entry_id, file_id = _submit(api, source)
    locations = [item for item in _findings(api, file_id) if item["text"] == "LG-Wels"]
    assert len(locations) == 2
    confirmed = next(item for item in locations if item["source"] == "ner")
    duplicate = next(item for item in locations if item["detector"] == EXACT_DUPLICATE_DETECTOR)
    assert duplicate["source"] == "suspicion"
    assert duplicate["review_required"] is True
    assert duplicate["start"] > confirmed["start"]

    # Left open: instead of blocking export, we mask during save (per spec §3.3).
    open_question = _save(api, entry_id, places, file_id, _decisions((confirmed["id"], "accept")))
    with fitz.open(_staged(api, open_question, source)) as exported:
        text = "".join(page.get_text("text") for page in exported)
    assert text.count("[Ort]") == 2 and "LG-Wels" not in text
    assert open_question["entry"]["files"][0]["output"]["masked_without_decision"] == 1

    # “Keep only here” for the repetition: this exact spot remains.
    kept = _save(
        api,
        entry_id,
        places,
        file_id,
        _decisions((confirmed["id"], "accept"), (duplicate["id"], "reject")),
    )
    path = _staged(api, kept, source)
    with fitz.open(path) as exported:
        assert "LG-Wels" not in exported[0].get_text("text")
        assert "LG-Wels" in exported[1].get_text("text")
    audit = api.services.audit_log().get(kept["entry"]["files"][0]["output"]["audit_id"])
    assert [
        (decision.finding_id, decision.action)
        for decision in audit.decisions
        if decision.finding_id is not None
    ] == [(confirmed["id"], "accept"), (duplicate["id"], "reject")]


@pytest.mark.parametrize(
    ("lines", "secret"),
    [
        ([(90, f"Empfaenger: {SECRET}"), (125, f"Kontakt: {SECRET}")], SECRET),
        ([(90, "Hans"), (110, "Meier"), (160, "Hans"), (180, "Meier")], MULTILINE_SECRET),
    ],
    ids=["einzeilig", "mehrzeilig"],
)
def test_pdf_decides_per_span_even_for_identical_values(
    api: Harness,
    lines: list[tuple[int, str]],
    secret: str,
) -> None:
    source = _pdf(api.tmp_path / "doppelte-person.pdf", lines)
    entry_id, file_id = _submit(api, source)
    persons = [item for item in _findings(api, file_id) if item["text"] == secret]
    assert len(persons) == 2

    saved = _save(
        api,
        entry_id,
        REMOVE,
        file_id,
        _decisions((persons[0]["id"], "accept"), (persons[1]["id"], "reject")),
    )
    with fitz.open(_staged(api, saved, source)) as redacted:
        text = redacted[0].get_text("text")
    assert text.count("Hans") == 1
    assert text.count("Meier") == 1


@pytest.mark.parametrize("suffix", [".png", ".jpg"])
def test_image_redaction_covers_the_ocr_box(api: Harness, suffix: str) -> None:
    source = _image(api.tmp_path / f"scan{suffix}")
    source_hash = sha256_file(source)

    entry_id, _ = _submit(api, source)
    path = _staged(api, _save(api, entry_id, MASK), source)

    assert sha256_file(source) == source_hash
    assert path.suffix == suffix
    with Image.open(path) as redacted:
        extrema = redacted.convert("RGB").crop((22, 14, 82, 26)).getextrema()
    assert max(channel[1] for channel in extrema) <= (24 if suffix == ".jpg" else 0)


def test_scan_with_unreadable_code_becomes_text_instead_of_failing(api: Harness) -> None:
    """The code is in the image, but OCR does not read it back.

    ``FixedImageOcr`` always reads "Hans Meier". The copy then becomes ``.txt``,
    and the output says so. The case with a readable code is covered by
    ``test_layout_scans_and_images``.
    """

    source = _image(api.tmp_path / "scan.png")
    entry_id, _ = _submit(api, source)
    saved = _save(api, entry_id, CODES)
    path = _staged(api, saved, source)
    output = saved["entry"]["files"][0]["output"]
    assert output["from_scan"] is True and output["readback_failed"] is True

    assert path.name == "scan_geschützt.txt"
    text = path.read_text(encoding="utf-8")
    assert SECRET not in text
    assert re.search(r"<<PERSON_[0-9a-f]{8}>>", text)


# -- Styles and Categories ----------------------------------------------------


@pytest.mark.parametrize(
    ("masking", "pattern", "label"),
    [
        (CODES, r"<<PERSON_[0-9a-f]{8}>>", "reversible"),
        ({**CODES, "style": "surrogates"}, r"^Kunde: (?!Hans Meier)\S+ \S+", "reversible"),
        ({**REMOVE, "style": "placeholder"}, r"\[Name\]", "irreversible"),
        (MASK, r"•{3,}|█{3,}|\*{3,}", "irreversible"),
        (REMOVE, r"^Kunde: $", "irreversible"),
    ],
    ids=["codes", "erfundene-namen", "platzhalter", "schwaerzen", "entfernen"],
)
def test_each_ui_style_returns_its_result(
    api: Harness,
    masking: dict[str, Any],
    pattern: str,
    label: str,
) -> None:
    source = api.tmp_path / "notiz.txt"
    source.write_text(f"Kunde: {SECRET}\nIBAN DE02120300000000202051\n", encoding="utf-8")
    entry_id, file_id = _submit(api, source)

    applied = api.client.post(
        f"/jobs/{file_id}/apply", headers=AUTH, json={"masking": masking, "review": {}}
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert SECRET not in body["text"]
    assert "DE02120300000000202051" not in body["text"]
    assert re.search(pattern, body["text"], re.M), body["text"]
    assert body["labels"]["label"] == label

    saved = _save(api, entry_id, masking)
    assert Path(saved["staged"][0]["path"]).read_text(encoding="utf-8") == body["text"]


def test_categories_extend_to_location_and_date(api: Harness) -> None:
    """Standard corresponds to previous level 2, all categories to level 3."""

    source = api.tmp_path / "brief.txt"
    source.write_text(f"{SECRET} | Wien | 03.04.2026", encoding="utf-8")
    _, file_id = _submit(api, source)

    def apply(categories: list[str]) -> str:
        masking = {
            **REMOVE,
            "style": "placeholder",
            "categories": categories,
            # “Anonymisieren” is not a separate switch; it follows from
            # irreversible ∧ all categories – the sidecar enforces this.
            "anonymize": categories == ALL_CATEGORIES,
        }
        response = api.client.post(
            f"/jobs/{file_id}/apply", headers=AUTH, json={"masking": masking, "review": {}}
        )
        assert response.status_code == 200, response.text
        return response.json()["text"]

    assert apply(["finance_ids"]) == f"{SECRET} | Wien | 03.04.2026"
    assert apply(DEFAULT_CATEGORIES) == "[Name] | Wien | 03.04.2026"
    assert apply(ALL_CATEGORIES) == "[Name] | [Ort] | [Datum]"


# -- Round trip ---------------------------------------------------------------


def test_code_round_trip_survives_llm_change_and_restart(tmp_path: Path) -> None:
    source = tmp_path / "anfrage.txt"
    source.write_text(f"Bitte antworte {SECRET} freundlich.", encoding="utf-8")

    settings, recorder, services = _services(tmp_path, DeterministicAnalyzer())
    with TestClient(create_app(settings, recorder, phase_one=services)) as client:
        harness = Harness(client, services, tmp_path)
        entry_id, _ = _submit(harness, source)
        saved = _save(harness, entry_id, CODES)
        masked = Path(saved["staged"][0]["path"]).read_text(encoding="utf-8")
    code = re.search(r"<<PERSON_[0-9a-f]{8}>>", masked)
    assert code is not None

    # A language model changes case and parentheses.
    answer = f"Sehr geehrter {code.group(0)[1:-1].lower()}, danke fuer Ihre Anfrage."

    settings, recorder, services = _services(tmp_path, DeterministicAnalyzer())
    with TestClient(create_app(settings, recorder, phase_one=services)) as client:
        restored = client.post(
            "/restore/text", headers=AUTH, json={"text": answer, "entry_id": entry_id}
        )
    assert restored.status_code == 200, restored.text
    assert restored.json()["text"] == f"Sehr geehrter {SECRET}, danke fuer Ihre Anfrage."
    assert restored.json()["counts"]["unknown"] == 0


# -- Concurrency --------------------------------------------------------


def test_health_remains_reachable_while_saving(
    api: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = api.tmp_path / "rechnung.docx"
    document = Document()
    document.add_paragraph(f"Kunde: {SECRET}")
    document.save(source)
    entry_id, _ = _submit(api, source)

    original = saving.export_processed_document
    started, release = Event(), Event()

    def blocking_export(*args: Any, **kwargs: Any) -> Any:
        started.set()
        assert release.wait(timeout=3)
        return original(*args, **kwargs)

    monkeypatch.setattr(saving, "export_processed_document", blocking_export)
    with ThreadPoolExecutor(max_workers=2) as executor:
        save = executor.submit(_save, api, entry_id, REMOVE)
        try:
            assert started.wait(timeout=2)
            health = executor.submit(api.client.get, "/health", headers=AUTH).result(timeout=1)
        finally:
            release.set()
        saved = save.result(timeout=5)

    assert health.status_code == 200
    assert saved["staged"][0]["name"] == "rechnung_geschützt.docx"


def test_obsolete_apply_returns_409_instead_of_unsaved_success(
    api: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = api.tmp_path / "notiz.txt"
    source.write_text(f"Kunde: {SECRET}", encoding="utf-8")
    _, file_id = _submit(api, source)
    monkeypatch.setattr(api.services.jobs(), "store_processing", lambda *a, **k: False)

    response = api.client.post(
        f"/jobs/{file_id}/apply", headers=AUTH, json={"masking": CODES, "review": {}}
    )

    assert response.status_code == 409
    assert "neuere Review-Entscheidung" in response.json()["detail"]
