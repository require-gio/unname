"""Manual correction in review (spec §9.3).

Three interventions must survive through saving and audit log:
removing an automatic masking, adding a value by hand, and fixing a span boundary.
Each is verified against the result text, not just the status code – a correction
that the sidecar accepts then discards would be the most dangerous case.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from presidio_sidecar.app import create_app
from presidio_sidecar.audit import AuditAction
from presidio_sidecar.config import Settings
from presidio_sidecar.detection import DetectionResult, Finding, FindingSource
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.jobs import JobManager
from presidio_sidecar.processing import (
    FindingKind,
    ManualFinding,
    ProcessingMode,
    ProcessingRequest,
    ProcessingValidationError,
    ProtectionLevel,
    ProtectionOverrides,
    ReviewAction,
    ReviewDecision,
    TextProcessor,
    finding_id,
)
from presidio_sidecar.profiles import Profile, load_profiles
from presidio_sidecar.services import PhaseOneServices
from presidio_sidecar.vault import TokenVault
from presidio_sidecar.workers import WorkerCapacity

TOKEN = "review-manual-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

TEXT = "Rechnung von Hans Meier an die Tischlerei Brandstetter in Wien."


def _span(value: str, *, text: str = TEXT) -> tuple[int, int]:
    start = text.index(value)
    return start, start + len(value)


# ---------------------------------------------------------------- Units


def _finding(value: str, entity_type: str, *, text: str = TEXT) -> Finding:
    start, end = _span(value, text=text)
    return Finding(entity_type, start, end, 0.9, FindingSource.NER, text=value)


@pytest.fixture
def processor(tmp_path: Path) -> Iterator[TextProcessor]:
    vault = TokenVault(tmp_path / "state")
    instance = TextProcessor(vault)
    try:
        yield instance
    finally:
        vault.close()


def _manual_request(*manual: ManualFinding, level: int = 2, **extra: Any) -> ProcessingRequest:
    return ProcessingRequest(
        mode=ProcessingMode.REMOVE,
        protection_level=ProtectionLevel(level),
        manual_findings=manual,
        **extra,
    )


def test_manual_entry_is_replaced_and_separately_marked(
    processor: TextProcessor,
) -> None:
    start, end = _span("Tischlerei Brandstetter")
    result = processor.apply(
        TEXT,
        DetectionResult(confirmed=(_finding("Hans Meier", "PERSON"),)),
        _manual_request(ManualFinding("ORGANIZATION", start, end)),
    )

    assert "Tischlerei Brandstetter" not in result.text
    assert "Hans Meier" not in result.text
    assert result.report.manual_counts_by_entity == {"ORGANIZATION": 1}
    assert result.report.manual_count == 1
    # The manual entry also counts as an applied finding; the report lists it separately.
    assert result.report.applied_counts_by_entity["ORGANIZATION"] == 1


POLICY_TEXT = (
    "Polizze Nr. 1132784527 vom 25.08.2026.\n"
    "Seite 2 – Polizze Nr. 1132784527\n"
    "Seite 3 – Polizze Nr. 1132784527, Kunde Hans Meier"
)


def test_manual_entry_applies_to_every_occurrence_of_value(
    processor: TextProcessor,
) -> None:
    """Regression Polizze: one of seven spots marked → saving failed."""

    start, end = _span("1132784527", text=POLICY_TEXT)
    result = processor.apply(
        POLICY_TEXT,
        DetectionResult(),
        ProcessingRequest(
            mode=ProcessingMode.TOKEN_DETERMINISTIC,
            protection_level=ProtectionLevel(2),
            manual_findings=(ManualFinding("CUSTOMER_NUMBER", start, end),),
        ),
        commit=False,
    )

    assert "1132784527" not in result.text
    codes = {result.text[item.output_start : item.output_end] for item in result.applied_findings}
    # Same value appears three times, so the same short code is used three times.
    assert len(result.applied_findings) == 3 and len(codes) == 1
    assert result.report.manual_counts_by_entity == {"CUSTOMER_NUMBER": 3}
    # User added one entry; the remaining entries are derived from it.
    assert result.report.manual_added_count == 1


def test_explicitly_kept_occurrence_remains(processor: TextProcessor) -> None:
    first = POLICY_TEXT.index("1132784527")
    last = POLICY_TEXT.rindex("1132784527")
    detected = Finding(
        "CUSTOMER_NUMBER", last, last + 10, 0.9, FindingSource.RULE, text="1132784527"
    )
    result = processor.apply(
        POLICY_TEXT,
        DetectionResult(confirmed=(detected,)),
        _manual_request(
            ManualFinding("CUSTOMER_NUMBER", first, first + 10),
            decisions=(
                ReviewDecision(
                    finding_id(detected, kind=FindingKind.CONFIRMED), ReviewAction.REJECT
                ),
            ),
        ),
    )

    assert result.text.count("1132784527") == 1
    assert result.text.rstrip().endswith("Polizze Nr. 1132784527, Kunde Hans Meier")


def test_already_replaced_occurrences_are_not_added_twice(
    processor: TextProcessor,
) -> None:
    # All occurrences manually added plus one rule match on one of them.
    starts = [
        index for index in range(len(POLICY_TEXT)) if POLICY_TEXT.startswith("1132784527", index)
    ]
    detected = Finding(
        "CUSTOMER_NUMBER", starts[2], starts[2] + 10, 0.95, FindingSource.RULE, text="1132784527"
    )
    result = processor.apply(
        POLICY_TEXT,
        DetectionResult(confirmed=(detected,)),
        _manual_request(*(ManualFinding("CUSTOMER_NUMBER", s, s + 10) for s in starts[:2])),
    )

    assert "1132784527" not in result.text
    assert result.report.manual_counts_by_entity == {"CUSTOMER_NUMBER": 2}


def test_manual_entry_overrides_protection_level(processor: TextProcessor) -> None:
    """ORGANIZATION is level 3 — manual marking still applies."""

    start, end = _span("Tischlerei Brandstetter")
    result = processor.apply(
        TEXT,
        DetectionResult(),
        _manual_request(ManualFinding("ORGANIZATION", start, end), level=1),
    )

    assert "Tischlerei Brandstetter" not in result.text
    assert result.report.manual_count == 1
    assert result.report.kept_plain_counts_by_entity == {}


def test_keep_plain_does_not_cancel_manual_entry(processor: TextProcessor) -> None:
    start, end = _span("Wien")
    result = processor.apply(
        TEXT,
        DetectionResult(),
        _manual_request(
            ManualFinding("LOCATION", start, end),
            level=3,
            overrides=ProtectionOverrides(keep_plain=frozenset({"LOCATION"})),
        ),
    )

    assert "Wien" not in result.text


def test_manual_entry_uses_own_mode(processor: TextProcessor) -> None:
    start, end = _span("Wien")
    result = processor.apply(
        TEXT,
        DetectionResult(),
        _manual_request(ManualFinding("LOCATION", start, end, mode=ProcessingMode.PLACEHOLDER)),
    )

    assert "[Ort]" in result.text
    assert result.facts.effective_modes == (ProcessingMode.PLACEHOLDER,)


def test_manual_entry_on_accepted_hit_is_rejected(
    processor: TextProcessor,
) -> None:
    """Boundary correction means: discard the existing hit, then add a new one."""

    person = _finding("Hans Meier", "PERSON")
    start, _ = _span("Hans Meier")
    with pytest.raises(ProcessingValidationError):
        processor.apply(
            TEXT,
            DetectionResult(confirmed=(person,)),
            _manual_request(ManualFinding("PERSON", start, start + 4)),
        )


def test_boundary_correction_uses_explicit_target_id_instead_of_overlap(
    processor: TextProcessor,
) -> None:
    person = _finding("Hans Meier", "PERSON")
    person_id = finding_id(person, kind=FindingKind.CONFIRMED)
    start, end = _span("Wien")

    result = processor.apply(
        TEXT,
        DetectionResult(confirmed=(person,)),
        _manual_request(
            ManualFinding("PERSON", start, end, corrects_finding_id=person_id),
            decisions=(ReviewDecision(person_id, ReviewAction.REJECT),),
        ),
    )

    assert "Hans Meier" in result.text
    assert "Wien" not in result.text
    assert result.report.adjusted_span_counts_by_entity == {"PERSON": 1}
    assert result.report.manual_added_counts_by_entity == {}


def test_incidental_overlap_without_target_id_stays_an_addition(
    processor: TextProcessor,
) -> None:
    person = _finding("Hans Meier", "PERSON")
    person_id = finding_id(person, kind=FindingKind.CONFIRMED)
    start, end = _span("Hans Meier")

    result = processor.apply(
        TEXT,
        DetectionResult(confirmed=(person,)),
        _manual_request(
            ManualFinding("ORGANIZATION", start, end),
            decisions=(ReviewDecision(person_id, ReviewAction.REJECT),),
        ),
    )

    assert result.report.manual_added_counts_by_entity == {"ORGANIZATION": 1}
    assert result.report.adjusted_span_counts_by_entity == {}


@pytest.mark.parametrize("variant", ["unknown", "wrong_type", "not_rejected"])
def test_invalid_correction_reference_is_rejected(
    processor: TextProcessor,
    variant: str,
) -> None:
    person = _finding("Hans Meier", "PERSON")
    person_id = finding_id(person, kind=FindingKind.CONFIRMED)
    start, end = _span("Wien")
    target_id = "f_00000000000000000000" if variant == "unknown" else person_id
    entity_type = "LOCATION" if variant == "wrong_type" else "PERSON"
    decisions = (
        () if variant == "not_rejected" else (ReviewDecision(person_id, ReviewAction.REJECT),)
    )

    with pytest.raises(ProcessingValidationError, match="Grenzkorrektur"):
        processor.apply(
            TEXT,
            DetectionResult(confirmed=(person,)),
            _manual_request(
                ManualFinding(
                    entity_type,
                    start,
                    end,
                    corrects_finding_id=target_id,
                ),
                decisions=decisions,
            ),
        )


def test_overlapping_manual_entries_are_rejected() -> None:
    start, end = _span("Hans Meier")
    with pytest.raises(ProcessingValidationError):
        _manual_request(
            ManualFinding("PERSON", start, end),
            ManualFinding("PERSON", start + 2, end + 4),
        )


def test_unknown_type_and_empty_span_are_rejected() -> None:
    with pytest.raises(ProcessingValidationError):
        ManualFinding("GIBT_ES_NICHT", 0, 4)
    with pytest.raises(ProcessingValidationError):
        ManualFinding("PERSON", 5, 5)
    with pytest.raises(ProcessingValidationError):
        ManualFinding.from_dict({"entity_type": "PERSON", "start": 0, "end": 4, "farbe": "rot"})


def test_manual_entry_outside_text_is_rejected(
    processor: TextProcessor,
) -> None:
    with pytest.raises(ProcessingValidationError):
        processor.apply(
            TEXT,
            DetectionResult(),
            _manual_request(ManualFinding("PERSON", len(TEXT) - 2, len(TEXT) + 40)),
        )


# --------------------------------------------------------------- HTTP-Weg


class ReviewAnalyzer:
    """Only finds "Hans Meier" – everything else must be supplied manually."""

    def analyze(self, text: str, *, progress: Callable[[str], None]) -> DetectionResult:
        return self._analyze(text, progress=progress)

    def analyze_for_profile(
        self,
        text: str,
        profile: Profile,
        *,
        progress: Callable[[str], None],
    ) -> DetectionResult:
        return self._analyze(text, progress=progress)

    def _analyze(self, text: str, *, progress: Callable[[str], None]) -> DetectionResult:
        for phase in ("master_data", "rules", "ner", "suspicion"):
            progress(phase)
        if (start := text.find("Hans Meier")) < 0:
            return DetectionResult()
        return DetectionResult(
            confirmed=(
                Finding(
                    "PERSON",
                    start,
                    start + len("Hans Meier"),
                    0.95,
                    FindingSource.NER,
                    text="Hans Meier",
                    detector="review-offline",
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class ApiHarness:
    client: TestClient
    services: PhaseOneServices


@pytest.fixture
def api(tmp_path: Path) -> Iterator[ApiHarness]:
    settings = Settings(
        token=TOKEN,
        state_directory=tmp_path / "state",
        job_ttl_seconds=60,
        max_text_bytes=256 * 1024,
    )
    recorder = DiagnosticsRecorder()
    analyzer = ReviewAnalyzer()
    jobs = JobManager(
        analyzer,
        max_workers=1,
        ttl_seconds=60,
        max_text_bytes=settings.max_text_bytes,
        root=tmp_path / "jobs",
    )
    services = PhaseOneServices(
        settings,
        recorder,
        analyzer=analyzer,  # type: ignore[arg-type]
        jobs=jobs,
        profiles=load_profiles(),
        capacity=WorkerCapacity(1, 0, 2, 8 * 1024**3),
    )
    services.second_opinion_model = lambda: None  # type: ignore[method-assign]
    with TestClient(create_app(settings, recorder, phase_one=services)) as client:
        yield ApiHarness(client, services)


REMOVE = {
    "method": "irreversible",
    "style": "remove",
    "categories": ["names", "addresses", "contact", "numbers"],
}


def _upload(client: TestClient, source: Path) -> tuple[str, str]:
    entry = client.post("/entries", headers=AUTH, json={"kind": "documents"})
    assert entry.status_code == 201, entry.text
    entry_id = entry.json()["id"]
    response = client.post(
        f"/entries/{entry_id}/files",
        headers=AUTH,
        files=[("file", (source.name, source.read_bytes(), "text/plain"))],
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["accepted"][0]["id"]

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        progress = client.get(f"/entries/{entry_id}/progress", headers=AUTH).json()
        if progress["done"] == progress["total"]:
            assert progress["files"][0]["status"] == "review", progress
            return entry_id, job_id
        time.sleep(0.01)
    raise AssertionError("Review-Job wurde nicht fertig.")


def _person_finding(client: TestClient, job_id: str) -> dict[str, Any]:
    findings = client.get(f"/jobs/{job_id}/findings", headers=AUTH).json()
    return next(item for item in findings["confirmed"] if item["type"] == "PERSON")


@pytest.fixture
def document(tmp_path: Path) -> Path:
    source = tmp_path / "rechnung.txt"
    source.write_text(TEXT, encoding="utf-8")
    return source


def test_all_three_interventions_end_in_result_and_audit_log(
    api: ApiHarness,
    document: Path,
) -> None:
    entry_id, job_id = _upload(api.client, document)
    person = _person_finding(api.client, job_id)
    company_start, company_end = _span("Tischlerei Brandstetter")
    # Boundary correction: replace the detected PERSON span with a tighter entry ("Meier" instead of
    # "Hans Meier").
    surname_start, surname_end = _span("Meier")
    review = {
        "decisions": [{"finding_id": person["id"], "action": "reject"}],
        "manual": [
            {
                "entity_type": "PERSON",
                "start": surname_start,
                "end": surname_end,
                "corrects_finding_id": person["id"],
            },
            {"entity_type": "ORGANIZATION", "start": company_start, "end": company_end},
        ],
    }

    response = api.client.post(
        f"/jobs/{job_id}/apply", headers=AUTH, json={"masking": REMOVE, "review": review}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # After manual edit: first name stays, last name and company are removed.
    assert "Hans " in body["text"]
    assert "Meier" not in body["text"]
    assert "Tischlerei Brandstetter" not in body["text"]
    assert body["report"]["manual_count"] == 2
    assert body["report"]["manual_added_counts_by_entity"] == {"ORGANIZATION": 1}
    assert body["report"]["manual_added_count"] == 1
    assert body["report"]["adjusted_span_counts_by_entity"] == {"PERSON": 1}
    assert body["report"]["adjusted_span_count"] == 1
    assert body["report"]["rejected_counts_by_entity"] == {"PERSON": 1}

    saved = api.client.post(
        f"/entries/{entry_id}/save",
        headers=AUTH,
        json={
            "masking": REMOVE,
            "files": [{"file_id": job_id, "review": review}],
            "confirm_irreversible": True,
        },
    )
    assert saved.status_code == 200, saved.text
    staged = Path(saved.json()["staged"][0]["path"])
    assert staged.read_text(encoding="utf-8") == body["text"]

    output = saved.json()["entry"]["files"][0]["output"]
    audit = api.services.audit_log().get(output["audit_id"])
    actions = [(decision.entity_type, decision.action) for decision in audit.decisions]
    assert ("PERSON", AuditAction.REJECT) in actions
    # The explicit empty target ID forces a boundary correction instead of inferring the action from
    # overlap.
    assert ("PERSON", AuditAction.ADJUST_SPAN) in actions
    assert ("ORGANIZATION", AuditAction.ADD_MANUAL) in actions
    correction = next(
        decision for decision in audit.decisions if decision.action is AuditAction.ADJUST_SPAN
    )
    assert correction.finding_id == person["id"]


def test_unknown_type_is_not_allowed_manually(
    api: ApiHarness,
    document: Path,
) -> None:
    _, job_id = _upload(api.client, document)
    start, end = _span("Wien")

    response = api.client.post(
        f"/jobs/{job_id}/apply",
        headers=AUTH,
        json={
            "masking": REMOVE,
            "review": {"manual": [{"entity_type": "ERFUNDEN", "start": start, "end": end}]},
        },
    )

    assert response.status_code == 422


def test_ui_utf16_offsets_converted(
    api: ApiHarness,
    tmp_path: Path,
) -> None:
    """An emoji before the location shifts Python and UTF-16 offsets."""

    text = "Notiz 🧾 zur Firma Tischlerei Brandstetter."
    source = tmp_path / "notiz.txt"
    source.write_text(text, encoding="utf-8")
    _, job_id = _upload(api.client, source)

    python_start = text.index("Tischlerei Brandstetter")
    utf16_start = len(text[:python_start].encode("utf-16-le")) // 2
    assert utf16_start == python_start + 1

    response = api.client.post(
        f"/jobs/{job_id}/apply",
        headers=AUTH,
        json={
            "masking": REMOVE,
            "review": {
                "manual": [
                    {
                        "entity_type": "ORGANIZATION",
                        "start": utf16_start,
                        "end": utf16_start + len("Tischlerei Brandstetter"),
                    }
                ]
            },
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["text"] == "Notiz 🧾 zur Firma ."
