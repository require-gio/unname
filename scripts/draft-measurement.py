"""Measurement of draft state (focus: storage, load time, memory usage).

Measures via API v2 using the real draft state, but without models (a fixed detection
and an OCR with fixed words), which reflects the draft state cost:

1. Disk usage per format as a factor of the original (``testdata/layout`` and a large
   scan with many OCR words),
2. Load time of an entry with 10 files after a restart
   (``GET /entries/{id}`` loads all files from the draft state),
3. RSS after opening 20 entries sequentially – up to the maximum number of loaded jobs.

Outputs size and timing data only, as JSON to stdout or to ``--output`` file.

Run from the repository root:
    uv run --project sidecar python scripts/draft-measurement.py \
        [--output acceptance/drafts.json]
"""

from __future__ import annotations

import argparse
import gc
import io
import json
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sidecar" / "tests"))

from presidio_sidecar.app import create_app  # noqa: E402
from presidio_sidecar.config import Settings  # noqa: E402
from presidio_sidecar.diagnostics import DiagnosticsRecorder  # noqa: E402
from presidio_sidecar.extraction import DocumentExtractor, OcrWord  # noqa: E402
from presidio_sidecar.jobs import JobManager  # noqa: E402
from presidio_sidecar.profiles import load_profiles  # noqa: E402
from presidio_sidecar.services import PhaseOneServices  # noqa: E402
from presidio_sidecar.workers import WorkerCapacity  # noqa: E402
from test_api_v2 import AUTH, TOKEN  # noqa: E402
from test_drafts import CorpusAnalyzer  # noqa: E402

CORPUS = ROOT / "testdata" / "layout"
FORMATS = [
    ("lieferschein.pdf", "Text-PDF"),
    ("scan.pdf", "Scan-PDF (OCR)"),
    ("bildschirmfoto.png", "PNG (OCR)"),
    ("foto-exif6.jpg", "JPG (OCR)"),
    ("brief.docx", "DOCX"),
    ("kunden.xlsx", "XLSX"),
    ("kunden.csv", "CSV"),
]
WORDS_PER_PAGE = 600
SCAN_PAGES = 20


class DenseOcr:
    """A full page: ``WORDS_PER_PAGE`` words with bounding box and confidence."""

    def recognize(self, image: Image.Image, *, languages: tuple[str, ...]) -> tuple[OcrWord, ...]:
        width, height = image.size
        columns, rows = 12, WORDS_PER_PAGE // 12
        cell_w, cell_h = width // (columns + 1), height // (rows + 2)
        return tuple(
            OcrWord(
                f"Wort{row}x{column}" if (row + column) % 7 else f"Mustermann{row}",
                left=column * cell_w + 5,
                top=row * cell_h + 5,
                width=cell_w - 10,
                height=cell_h - 4,
                confidence=0.9,
                line_id=(1, 1, row, 1),
            )
            for row in range(rows)
            for column in range(columns)
        )


def big_scan() -> bytes:
    document = fitz.open()
    for _ in range(SCAN_PAGES):
        page = document.new_page(width=595, height=842)
        image = Image.new("RGB", (1240, 1754), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        page.insert_image(page.rect, stream=buffer.getvalue())
    return document.tobytes(deflate=True)


def rss_bytes() -> int:
    output = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(__import__("os").getpid())],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(output.stdout.strip()) * 1024


@contextmanager
def sidecar(root: Path) -> Iterator[tuple[TestClient, PhaseOneServices]]:
    settings = Settings(
        token=TOKEN, state_directory=root / "state", max_text_bytes=10 * 1024 * 1024
    )
    analyzer = CorpusAnalyzer()
    recorder = DiagnosticsRecorder()
    services = PhaseOneServices(
        settings,
        recorder,
        analyzer=analyzer,  # type: ignore[arg-type]
        jobs=JobManager(
            analyzer,  # type: ignore[arg-type]
            max_workers=2,
            ttl_seconds=900,
            max_text_bytes=settings.max_text_bytes,
            root=root / "jobs",
        ),
        extractor=DocumentExtractor(ocr_backend=DenseOcr()),
        profiles=load_profiles(),
        capacity=WorkerCapacity(1, 0, 2, 8 * 1024**3),
    )
    services.second_opinion_model = lambda: None  # type: ignore[method-assign]
    with TestClient(create_app(settings, recorder, phase_one=services)) as client:
        services.wait_for_startup()
        yield client, services


def submit(client: TestClient, files: list[tuple[str, bytes]]) -> str:
    entry_id = client.post("/entries", headers=AUTH, json={"kind": "documents"}).json()["id"]
    response = client.post(
        f"/entries/{entry_id}/files",
        headers=AUTH,
        files=[("file", (name, content, "application/octet-stream")) for name, content in files],
    )
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        progress = client.get(f"/entries/{entry_id}/progress", headers=AUTH).json()
        if progress["done"] == progress["total"]:
            return entry_id
        time.sleep(0.02)
    raise TimeoutError("Eintrag wurde nicht fertig.")


def measure_disk(work: Path) -> list[dict[str, Any]]:
    samples = [(name, label, (CORPUS / name).read_bytes()) for name, label in FORMATS]
    samples.append(
        (
            "gross-scan.pdf",
            f"Scan-PDF {SCAN_PAGES} S. × {WORDS_PER_PAGE} Woerter",
            big_scan(),
        )
    )
    results = []
    with sidecar(work / "disk") as (client, services):
        for name, label, content in samples:
            entry_id = submit(client, [(name, content)])
            usage = services.drafts().usage().entries[entry_id]
            directory = services.drafts().root / entry_id
            artifacts = {path.name: path.stat().st_size for path in directory.glob("*/*.enc")}
            results.append(
                {
                    "format": label,
                    "original_bytes": len(content),
                    "draft_bytes": usage,
                    "factor": round(usage / len(content), 2),
                    "artifacts": artifacts,
                }
            )
    return results


def measure_load(work: Path) -> dict[str, Any]:
    root = work / "load"
    names = ["lieferschein.pdf", "scan.pdf", "brief.docx", "kunden.xlsx", "kunden.csv"]
    files = [
        (f"{index}-{name}", (CORPUS / name).read_bytes()) for index in range(2) for name in names
    ]
    with sidecar(root) as (client, _services):
        entry_id = submit(client, files)
        scan_id = submit(client, [("gross-scan.pdf", big_scan())])
    timings: list[float] = []
    scan_timings: list[float] = []
    for _ in range(3):
        with sidecar(root) as (client, services):
            started = time.perf_counter()
            entry = client.get(f"/entries/{entry_id}", headers=AUTH).json()
            timings.append(time.perf_counter() - started)
            assert [item["status"] for item in entry["files"]] == ["review"] * len(files)
            started = time.perf_counter()
            scan = client.get(f"/entries/{scan_id}", headers=AUTH).json()
            scan_timings.append(time.perf_counter() - started)
            assert scan["files"][0]["status"] == "review"
            assert services.jobs()._jobs.__len__() == len(files) + 1
    return {
        "files": len(files),
        "seconds": [round(value, 3) for value in timings],
        "median_seconds": round(sorted(timings)[1], 3),
        "big_scan": {
            "pages": SCAN_PAGES,
            "ocr_words": SCAN_PAGES * WORDS_PER_PAGE,
            "seconds": [round(value, 3) for value in scan_timings],
            "median_seconds": round(sorted(scan_timings)[1], 3),
        },
    }


def measure_rss(work: Path) -> dict[str, Any]:
    root = work / "rss"
    content = [
        ("scan.pdf", (CORPUS / "scan.pdf").read_bytes()),
        ("lieferschein.pdf", (CORPUS / "lieferschein.pdf").read_bytes()),
        ("brief.docx", (CORPUS / "brief.docx").read_bytes()),
    ]
    with sidecar(root) as (client, _services):
        entry_ids = [submit(client, content) for _ in range(20)]
    with sidecar(root) as (client, services):
        gc.collect()
        before = rss_bytes()
        loaded = []
        for entry_id in entry_ids:
            client.get(f"/entries/{entry_id}", headers=AUTH)
            for item in client.get(f"/entries/{entry_id}", headers=AUTH).json()["files"]:
                client.get(f"/jobs/{item['id']}/findings", headers=AUTH)
            loaded.append(len(services.jobs()._jobs))
        gc.collect()
        after = rss_bytes()
        cap = services.jobs().max_loaded_jobs
    return {
        "entries": len(entry_ids),
        "files_per_entry": len(content),
        "max_loaded_jobs": cap,
        "loaded_jobs_after_each": loaded,
        "rss_before_mb": round(before / 1024**2, 1),
        "rss_after_mb": round(after / 1024**2, 1),
        "rss_growth_mb": round((after - before) / 1024**2, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="unname-arbeitsstand-") as work:
        base = Path(work)
        result = {
            "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "machine": {
                "platform": platform.platform(),
                "python": platform.python_version(),
            },
            "disk": measure_disk(base),
            "load": measure_load(base),
            "rss": measure_rss(base),
        }
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
