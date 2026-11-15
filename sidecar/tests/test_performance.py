"""Performance (spec §2): 50-page PDF, 9 files, 1,000 codes.

Measures the path the sidecar itself takes – extraction, apply (dry-run), saving with PDF
redaction and render check – using a fixed detection without models. The models are
measured by ``scripts/performance.py`` against the real sidecar; their runtime depends on
the hardware, not on this code.

The limits are generous (several times the measured value on an M-Mac) so the test
doesn’t flicker on slow machines, but still catches a fallback of an order of magnitude –
e.g. a quadratic loop over the matches. With ``PRESIDIO_LEISTUNG_AUSGABE=1`` the test
writes the measured times to the console.
"""

# The ``api`` fixture comes from ``test_documents_v2``; as a parameter it hides the import.
# ruff: noqa: F811

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import fitz
import pytest

from presidio_sidecar.api_common import Utf16Offsets, python_offset_from_utf16
from presidio_sidecar.processing import ProcessingValidationError
from test_documents_v2 import (
    AUTH,
    CODES,
    REMOVE,
    SECRET,
    Harness,
    _save,
    api,  # noqa: F401
)

OUTPUT = os.environ.get("PRESIDIO_LEISTUNG_AUSGABE") == "1"


def _report(was: str, seconds: float) -> None:
    if OUTPUT:
        print(f"\n[leistung] {was}: {seconds:.2f} s")


def _entry(api: Harness, files: list[tuple[str, bytes, str]]) -> tuple[str, list[str], float]:
    entry = api.client.post("/entries", headers=AUTH, json={"kind": "documents"})
    assert entry.status_code == 201, entry.text
    entry_id = entry.json()["id"]
    started = time.perf_counter()
    response = api.client.post(
        f"/entries/{entry_id}/files",
        headers=AUTH,
        files=[("file", item) for item in files],
    )
    assert response.status_code == 202, response.text
    accepted = [item["id"] for item in response.json()["accepted"]]
    assert len(accepted) == len(files), response.json()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        progress = api.client.get(f"/entries/{entry_id}/progress", headers=AUTH).json()
        if progress["done"] == progress["total"]:
            assert {item["status"] for item in progress["files"]} == {"review"}, progress
            return entry_id, accepted, time.perf_counter() - started
        time.sleep(0.02)
    raise AssertionError("Erkennung wurde nicht fertig.")


def _pdf(pages: int, lines_per_page: int = 20) -> bytes:
    document = fitz.open()
    for number in range(pages):
        page = document.new_page(width=595, height=842)
        for line in range(lines_per_page):
            text = (
                f"Seite {number + 1}, Zeile {line + 1}: Rueckfrage von {SECRET} zum Auftrag."
                if line % 4 == 0
                else f"Seite {number + 1}, Zeile {line + 1}: Positionen, Mengen und Preise."
            )
            page.insert_text((56, 60 + line * 36), text, fontsize=10)
    data = document.tobytes()
    document.close()
    return data


def test_fifty_page_pdf_is_detected_redacted_and_rendered_checked(api: Harness) -> None:
    pdf = _pdf(50)
    entry_id, (file_id,), detected = _entry(api, [("Vertrag.pdf", pdf, "application/pdf")])
    _report("50-Seiten-PDF bis zur Pruefansicht", detected)

    findings = api.client.get(f"/jobs/{file_id}/findings", headers=AUTH).json()
    assert len(findings["findings"]) == 250  # 5 names per page
    started = time.perf_counter()
    applied = api.client.post(
        f"/jobs/{file_id}/apply", headers=AUTH, json={"masking": REMOVE, "review": {}}
    )
    apply_seconds = time.perf_counter() - started
    assert applied.status_code == 200, applied.text
    _report("50-Seiten-PDF Apply", apply_seconds)

    started = time.perf_counter()
    saved = _save(api, entry_id, REMOVE)
    save_seconds = time.perf_counter() - started
    _report("50-Seiten-PDF Speichern mit Redaktion und Render-Pruefung", save_seconds)

    output = saved["entry"]["files"][0]["output"]
    assert output["rendered_pages"] == 50
    assert output["text_extraction_checked"] is True
    with fitz.open(saved["staged"][0]["path"]) as redacted:
        assert redacted.page_count == 50
        assert all(SECRET not in page.get_text("text") for page in redacted)
    assert detected < 30
    assert apply_seconds < 10
    assert save_seconds < 90


def test_nine_files_in_one_entry(api: Harness) -> None:
    files = [
        (
            f"Brief_{index}.txt",
            f"Sehr geehrter {SECRET},\nanbei Unterlage {index}.\n".encode(),
            "text/plain",
        )
        for index in range(8)
    ] + [("Beleg.pdf", _pdf(3), "application/pdf")]
    entry_id, file_ids, detected = _entry(api, files)
    _report("9 Dateien bis zur Pruefansicht", detected)
    assert len(file_ids) == 9

    started = time.perf_counter()
    for file_id in file_ids:
        response = api.client.post(
            f"/jobs/{file_id}/apply", headers=AUTH, json={"masking": CODES, "review": {}}
        )
        assert response.status_code == 200, response.text
    apply_seconds = time.perf_counter() - started
    _report("9 Dateien Apply (nacheinander)", apply_seconds)

    started = time.perf_counter()
    saved = _save(api, entry_id, CODES)
    save_seconds = time.perf_counter() - started
    _report("9 Dateien Speichern", save_seconds)

    assert len(saved["staged"]) == 9
    assert saved["committed_mappings"] == 1  # same name, same code
    assert detected < 30
    assert apply_seconds < 10
    assert save_seconds < 30


def test_thousand_codes_in_one_file(api: Harness) -> None:
    text = "".join(f"Zeile {index}: Termin mit {SECRET} bestaetigt.\n" for index in range(1000))
    entry_id, (file_id,), detected = _entry(api, [("Protokoll.txt", text.encode(), "text/plain")])
    _report("1.000 Codes bis zur Pruefansicht", detected)

    findings = api.client.get(f"/jobs/{file_id}/findings", headers=AUTH).json()["findings"]
    assert len(findings) == 1000
    # Keep only here on every second item: 500 decisions in the request.
    decisions = [{"finding_id": item["id"], "action": "reject"} for item in findings[::2]]
    times: list[float] = []
    result: dict[str, Any] = {}
    for _ in range(3):
        started = time.perf_counter()
        response = api.client.post(
            f"/jobs/{file_id}/apply",
            headers=AUTH,
            json={"masking": CODES, "review": {"decisions": decisions}},
        )
        times.append(time.perf_counter() - started)
        assert response.status_code == 200, response.text
        result = response.json()
    _report("1.000 Codes Apply (bester von 3)", min(times))

    assert result["text"].count("<<PERSON_") == 500
    assert result["text"].count(SECRET) == 500
    started = time.perf_counter()
    saved = _save(api, entry_id, CODES, file_id, decisions)
    save_seconds = time.perf_counter() - started
    _report("1.000 Codes Speichern", save_seconds)
    assert Path(saved["staged"][0]["path"]).read_text(encoding="utf-8") == result["text"]
    assert detected < 20
    assert min(times) < 5
    assert save_seconds < 20


@pytest.mark.parametrize("text", ["", "Hans Meier", "😀 Gebrüder 🧾 Wachtelhuber 👩‍👩‍👧", "🧾" * 5])
def test_utf16_conversion_matches_individual_calculation(text: str) -> None:
    offsets = Utf16Offsets(text)
    units = len(text.encode("utf-16-le")) // 2
    for offset in range(units + 2):
        try:
            expected: int | str = python_offset_from_utf16(text, offset)
        except ProcessingValidationError as error:
            expected = "teilt" if "teilt" in str(error) else "ausserhalb"
        try:
            actual: int | str = offsets.python(offset, ProcessingValidationError)
        except ProcessingValidationError as error:
            actual = "teilt" if "teilt" in str(error) else "ausserhalb"
        assert actual == expected, (text, offset)
