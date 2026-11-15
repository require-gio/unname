"""Performance with real models: 50-page PDF, 9 files, 1 000 codes.

Starts the sidecar like the app – using the real detection (GLiNER or
spaCy, rules, suspicion check, second opinion if installed) – and
measures via API v2 the same three cases as
``sidecar/tests/test_performance.py``:

1. a 50-page PDF: detection, apply, save with editorial review and
   render check,
2. an entry with 9 files (eight letters, one three-page PDF),
3. a text file with 1 000 codes: detection, apply with 500 decisions,
   save,
4. a 10-page scan (image-PDF, 200 dpi): OCR, save with code re-read for
   engine comparison. In development ``PRESIDIO_OCR_ENGINE`` selects
   ``apple-vision`` or ``tesseract``; ``--nur-scan`` measures only this case.

The texts are synthetic. The result (only timings, counts and
device info, no content) is written to ``acceptance/performance.json``.

Run from the repository:
    uv run --project sidecar python scripts/performance.py [--output acceptance/performance.json]
    PRESIDIO_OCR_ENGINE=tesseract uv run --project sidecar python scripts/performance.py \
        --nur-scan --output /tmp/scan-tesseract.json
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz
from fastapi.testclient import TestClient
from PIL import Image

from presidio_sidecar import __version__, resources
from presidio_sidecar.app import create_app
from presidio_sidecar.config import Settings
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.services import PhaseOneServices

TOKEN = "leistung"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CODES = {
    "method": "reversible",
    "style": "codes",
    "categories": ["names", "addresses", "contact", "numbers"],
}
REMOVE = {**CODES, "method": "irreversible", "style": "remove"}

FIRST_NAMES = ("Sabine", "Josef", "Renate", "Markus", "Petra", "Andreas", "Elif", "Jonas")
LAST_NAMES = (
    "Hofer",
    "Brandstetter",
    "Wimmer",
    "Leitner",
    "Novak",
    "Gruber",
    "Yilmaz",
    "Huber",
)
LOCATIONS = ("Wien", "Linz", "Graz", "Salzburg", "München", "Passau")


def _person(index: int) -> str:
    return f"{FIRST_NAMES[index % len(FIRST_NAMES)]} {LAST_NAMES[(index // 3) % len(LAST_NAMES)]}"


def _line(page_index: int, line: int) -> str:
    index = page_index * 31 + line
    if line % 5 == 0:
        return (
            f"Am {1 + index % 28}.{1 + index % 12}.2026 rief {_person(index)} aus "
            f"{LOCATIONS[index % len(LOCATIONS)]} wegen der Lieferung an."
        )
    if line % 7 == 0:
        return f"Rückfragen bitte an {_person(index + 1)}, Telefon +43 1 {4000000 + index}."
    return f"Position {index}: Holzplatten, Beschläge und Montage laut Angebot vom Vormonat."


def _pdf(pages: int) -> bytes:
    document = fitz.open()
    for page_index in range(pages):
        page = document.new_page(width=595, height=842)
        for line in range(22):
            page.insert_text((56, 60 + line * 34), _line(page_index, line), fontsize=9.5)
    data = document.tobytes()
    document.close()
    return data


def _scan(pages: int) -> bytes:
    """Image-PDF: each page from ``_pdf`` at 200 dpi as JPEG, without text layer."""

    template = fitz.open(stream=_pdf(pages), filetype="pdf")
    document = fitz.open()
    for page in template:
        pixmap = page.get_pixmap(dpi=200, colorspace=fitz.csRGB, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=80)
        new = document.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=buffer.getvalue())
    data = document.tobytes(garbage=3, deflate=True)
    template.close()
    document.close()
    return data


def _letter(index: int) -> bytes:
    return (
        f"Sehr geehrte Frau {LAST_NAMES[index % len(LAST_NAMES)]},\n\n"
        f"vielen Dank für Ihre Nachricht vom {1 + index}. Oktober. Wie mit {_person(index + 2)} "
        f"besprochen, senden wir die Unterlagen nach {LOCATIONS[index % len(LOCATIONS)]}.\n\n"
        f"Mit freundlichen Grüßen\n{_person(index + 5)}\n"
    ).encode()


def _thousand() -> bytes:
    return "".join(
        f"Position {index + 1}: Rückruf von {_person(index)} bestätigt.\n" for index in range(1000)
    ).encode()


class Measurement:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def submit(self, file_items: list[tuple[str, bytes, str]]) -> tuple[str, list[str], float]:
        entry = self.client.post("/entries", headers=AUTH, json={"kind": "documents"}).json()
        begonnen = time.perf_counter()
        response = self.client.post(
            f"/entries/{entry['id']}/files",
            headers=AUTH,
            files=[("file", item) for item in file_items],
        )
        response.raise_for_status()
        ids = [item["id"] for item in response.json()["accepted"]]
        if len(ids) != len(file_items):
            raise RuntimeError(f"Dateien abgelehnt: {response.json()['rejected']}")
        while True:
            progress = self.client.get(f"/entries/{entry['id']}/progress", headers=AUTH).json()
            if progress["done"] == progress["total"]:
                break
            time.sleep(0.05)
        duration = time.perf_counter() - begonnen
        status = {item["status"] for item in progress["files"]}
        if status != {"review"}:
            raise RuntimeError(f"Erkennung nicht fertig: {status}")
        return entry["id"], ids, duration

    def fundstellen(self, job_id: str) -> list[dict[str, Any]]:
        return self.client.get(f"/jobs/{job_id}/findings", headers=AUTH).json()["findings"]

    def apply(
        self, job_id: str, masking: dict[str, Any], **review: Any
    ) -> tuple[dict[str, Any], float]:
        begonnen = time.perf_counter()
        response = self.client.post(
            f"/jobs/{job_id}/apply",
            headers=AUTH,
            json={"masking": masking, "review": review},
        )
        response.raise_for_status()
        return response.json(), time.perf_counter() - begonnen

    def save_entry(
        self,
        entry_id: str,
        masking: dict[str, Any],
        files: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], float]:
        begonnen = time.perf_counter()
        response = self.client.post(
            f"/entries/{entry_id}/save",
            headers=AUTH,
            json={
                "masking": masking,
                "files": files or [],
                "confirm_irreversible": True,
            },
        )
        response.raise_for_status()
        return response.json(), time.perf_counter() - begonnen


def _round(sekunden: float) -> float:
    return round(sekunden, 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=resources.repository_root() / "acceptance" / "performance.json",
    )
    parser.add_argument(
        "--nur-scan",
        action="store_true",
        help="Nur den 10-seitigen Scan messen (Vergleich der Texterkennung)",
    )
    args = parser.parse_args(argv)
    case_results: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="unname-leistung-") as tmp:
        settings = Settings(token=TOKEN, state_directory=Path(tmp) / "state")
        recorder = DiagnosticsRecorder()
        services = PhaseOneServices(settings, recorder)
        with TestClient(create_app(settings, recorder, phase_one=services)) as client:
            measurement = Measurement(client)
            health = client.get("/health", headers=AUTH).json()

            # Warm-up: first document loads models; this measures initial setup,
            # not runtime performance.
            begonnen = time.perf_counter()
            measurement.submit([("warm.txt", _letter(0), "text/plain")])
            warmup = time.perf_counter() - begonnen

            scan_entry, (scan_job,), scan_detection = measurement.submit(
                [("Scan_10_Seiten.pdf", _scan(10), "application/pdf")]
            )
            scan_findings = len(measurement.fundstellen(scan_job))
            measurement.apply(scan_job, CODES)
            scan_saved, scan_save = measurement.save_entry(scan_entry, CODES)
            scan_output = scan_saved["entry"]["files"][0]["output"]
            ocr = client.get("/health", headers=AUTH).json().get("ocr") or {}
            case_results["scan_10_seiten"] = {
                "texterkennung": {
                    key: ocr.get(key)
                    for key in ("engine", "request", "revision", "os_build", "version")
                    if ocr.get(key) is not None
                },
                "places": scan_findings,
                "detection_s": _round(scan_detection),
                "save_s": _round(scan_save),
                "format": scan_output.get("format") if scan_output else None,
                "zurueckgelesen": scan_output.get("readback_checked") if scan_output else None,
            }
            if args.nur_scan:
                health = client.get("/health", headers=AUTH).json()
                return _write(args.output, health, warmup, case_results)

            pdf_entry, (pdf_job,), pdf_detection = measurement.submit(
                [("Protokoll_50_Seiten.pdf", _pdf(50), "application/pdf")]
            )
            pdf_findings = len(measurement.fundstellen(pdf_job))
            _, pdf_apply = measurement.apply(pdf_job, REMOVE)
            pdf_saved, pdf_save = measurement.save_entry(pdf_entry, REMOVE)
            pdf_output = pdf_saved["entry"]["files"][0]["output"]

            file_items = [
                (f"Brief_{index + 1}.txt", _letter(index), "text/plain") for index in range(8)
            ]
            file_items.append(("Beleg.pdf", _pdf(3), "application/pdf"))
            nine_entry, nine_jobs, nine_detection = measurement.submit(file_items)
            nine_apply = 0.0
            for job in nine_jobs:
                nine_apply += measurement.apply(job, CODES)[1]
            nine_saved, nine_save = measurement.save_entry(nine_entry, CODES)

            thousand_entry, (thousand_job,), thousand_detection = measurement.submit(
                [("Protokoll_1000.txt", _thousand(), "text/plain")]
            )
            places = measurement.fundstellen(thousand_job)
            names = [item for item in places if item["type"] == "PERSON"]
            decision_list = [{"finding_id": item["id"], "action": "reject"} for item in names[::2]]
            thousand_result, thousand_apply = measurement.apply(
                thousand_job, CODES, decisions=decision_list
            )
            _, thousand_save = measurement.save_entry(
                thousand_entry,
                CODES,
                [{"file_id": thousand_job, "review": {"decisions": decision_list}}],
            )

    case_results |= {
        "pdf_50_pages": {
            "places": pdf_findings,
            "detection_s": _round(pdf_detection),
            "apply_s": _round(pdf_apply),
            "save_s": _round(pdf_save),
            "rendered_pages": pdf_output.get("rendered_pages"),
        },
        "nine_files": {
            "detection_s": _round(nine_detection),
            "apply_all_s": _round(nine_apply),
            "save_s": _round(nine_save),
            "saved": len(nine_saved["staged"]),
        },
        "thousand_codes": {
            "places": len(places),
            "names": len(names),
            "decisions": len(decision_list),
            "codes_in_result": thousand_result["text"].count("<<PERSON_"),
            "detection_s": _round(thousand_detection),
            "apply_s": _round(thousand_apply),
            "save_s": _round(thousand_save),
        },
    }
    return _write(args.output, health, warmup, case_results)


def _write(
    output_file: Path, health: dict[str, Any], warmup: float, case_results: dict[str, Any]
) -> int:
    result = {
        "schema_version": 1,
        "measured_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "tool": "scripts/performance.py",
        "version": __version__,
        "device": {
            "system": platform.system(),
            "machine": platform.machine(),
            "processor": platform.processor() or None,
            "logical_cores": os.cpu_count(),
            "memory_gib": health.get("workers", {}).get("memory_gib"),
            "detection": health.get("nlp_engine", {}).get("active"),
            "device": health.get("nlp_engine", {}).get("device"),
            "models": {
                name: info.get("state") for name, info in (health.get("models") or {}).items()
            },
        },
        "warmup_s": _round(warmup),
        "cases": case_results,
    }
    output_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
