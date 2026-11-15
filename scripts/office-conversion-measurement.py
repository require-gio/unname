"""Measurement for draft AS3.6: how long does the Office conversion take?

AS3.6 caches the DOCX → PDF conversion of the faithful Word view in the draft
store only if it costs more than 2 s per document. This script measures the
conversion with the real Office engine for the corpus document and generated
documents of 10, 50 and 150 pages (headings, paragraphs, a table per page).

Result (sizes and times only, no content) as JSON on stdout or in ``--output``.

Run from the repository:
    uv run --project sidecar python scripts/office-conversion-measurement.py \
        [--output acceptance/office-conversion.json]
"""

from __future__ import annotations

import argparse
import io
import json
import platform
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docx import Document

from presidio_sidecar.office_engine import docx_to_pdf, office_engine

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DOCUMENT = ROOT / "testdata" / "layout" / "brief.docx"
GENERATED_PAGES = (10, 50, 150)
RUNS = 3
#: AS3.6 applies only above this time per document.
THRESHOLD_SECONDS = 2.0

PARAGRAPH = (
    "Sehr geehrte Frau Hofer, wir bestätigen den Eingang Ihrer Unterlagen vom 12. Oktober. "
    "Die Rechnung Nr. 2026-1043 über 1.284,50 EUR ist bis zum 30. November auf das Konto "
    "DE02 1203 0000 0000 2020 51 zu überweisen. "
) * 3


def generated_document(pages: int) -> bytes:
    document = Document()
    for page in range(pages):
        document.add_heading(f"Abschnitt {page + 1}", level=2)
        for _ in range(4):
            document.add_paragraph(PARAGRAPH)
        table = document.add_table(rows=4, cols=4)
        for row in range(4):
            for column in range(4):
                table.cell(row, column).text = f"Pos {row}.{column} · 12,50 EUR"
        document.add_page_break()
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def measure(engine: Any, name: str, data: bytes) -> dict[str, Any]:
    seconds: list[float] = []
    pdf = b""
    for _ in range(RUNS):
        start = time.perf_counter()
        pdf = docx_to_pdf(engine, data)
        seconds.append(time.perf_counter() - start)
    median = statistics.median(seconds)
    return {
        "document": name,
        "docx_bytes": len(data),
        "pdf_bytes": len(pdf),
        "median_ms": round(median * 1000),
        "max_ms": round(max(seconds) * 1000),
        "above_threshold": median > THRESHOLD_SECONDS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    engine = office_engine()
    if engine is None:
        sys.exit("No Office engine found (office-engine/target/release or PRESIDIO_OFFICE_ENGINE).")
    documents = [(CORPUS_DOCUMENT.name, CORPUS_DOCUMENT.read_bytes())] + [
        (f"generated, {pages} pages", generated_document(pages)) for pages in GENERATED_PAGES
    ]
    results = [measure(engine, name, data) for name, data in documents]
    result = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": {"platform": platform.platform(), "python": platform.python_version()},
        "engine": {"name": engine.name, "version": engine.version},
        "runs": RUNS,
        "threshold_ms": round(THRESHOLD_SECONDS * 1000),
        "conversions": results,
        "cache_needed": any(item["above_threshold"] for item in results),
    }
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if arguments.output:
        arguments.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
