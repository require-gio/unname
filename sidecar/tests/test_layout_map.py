"""Layout map and page image (layout plan).

The test verifies the requirement behind A-25: each box in the layout map must align with its word
in the page image, even for rotated pages, CropBox offsets, scans, and EXIF-rotated photos. The
synthetic corpus from ``scripts/layout-corpus.py`` is regenerated fresh for each test.

Glyphs have page offsets (a Times “1” starts 1.7 pt after its box). Therefore the rotation tests
compare the rotated page image under each box with the unrotated image under the same box: if the
transformation is wrong, different pixels appear.
"""

from __future__ import annotations

import importlib.util
import io
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageChops, ImageStat

from presidio_sidecar.app import create_app
from presidio_sidecar.config import Settings
from presidio_sidecar.detection import DetectionResult, Finding, FindingSource
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.extraction import DocumentExtractor, DocumentFormat, OcrWord
from presidio_sidecar.jobs import JobManager
from presidio_sidecar.layout import build_layout
from presidio_sidecar.profiles import Profile, load_profiles
from presidio_sidecar.rendering import JobMemoryCache, render_page_png
from presidio_sidecar.services import PhaseOneServices
from presidio_sidecar.workers import WorkerCapacity

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("layout_corpus", REPO / "scripts/layout-corpus.py")
assert _spec is not None and _spec.loader is not None
layout_corpus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(layout_corpus)

MAX_PIXELS = 100_000_000
SCALE = 2.0


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return layout_corpus.generate(tmp_path_factory.mktemp("layout-corpus"))


def _extract(path: Path, ocr: Any = None, **options: Any) -> Any:
    extractor = DocumentExtractor(ocr_backend=ocr) if ocr is not None else DocumentExtractor()
    return extractor.extract_bytes(path.read_bytes(), filename=path.name, **options)


def _render(path: Path, document_format: DocumentFormat, scale: float = SCALE) -> Image.Image:
    payload = render_page_png(path, document_format, 0, scale, max_image_pixels=MAX_PIXELS)
    return Image.open(io.BytesIO(payload)).convert("L")


def _crop(image: Image.Image, box: tuple[float, float, float, float], scale: float) -> Image.Image:
    return image.crop(tuple(round(value * scale) for value in box))


def _difference(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        b = b.resize(a.size)
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def _ink(image: Image.Image) -> float:
    """Proportion of dark pixels."""

    histogram = image.histogram()
    return sum(histogram[:160]) / max(1, sum(histogram))


def _visible_words(layout: dict[str, Any]) -> list[list[Any]]:
    hidden = [(item["start"], item["end"]) for item in layout["hidden"]]
    return [
        word for word in layout["words"] if not any(start <= word[5] < end for start, end in hidden)
    ]


# -- Corpus ------------------------------------------------------------------


def test_corpus_covers_the_planned_cases(corpus: dict[str, Path]) -> None:
    assert set(corpus) == set(layout_corpus.FILES)
    replica = fitz.open(corpus["lieferschein.pdf"])
    text = replica[0].get_text()
    for value in (
        layout_corpus.NAME,
        layout_corpus.STREET,
        layout_corpus.PHONE,
        layout_corpus.CONTROL_TEXT,
    ):
        assert value in text
    assert fitz.open(corpus["lieferschein-rot90.pdf"])[0].rotation == 90
    assert fitz.open(corpus["lieferschein-cropbox.pdf"])[0].cropbox != replica[0].mediabox
    assert not fitz.open(corpus["scan.pdf"])[0].get_text().strip()
    with Image.open(corpus["foto-exif6.jpg"]) as photo:
        assert photo.getexif().get(0x0112) == 6


# -- Fixed pages with text layer ----------------------------------------------


def test_pdf_layout_lists_pages_words_and_hidden_text(corpus: dict[str, Path]) -> None:
    path = corpus["lieferschein.pdf"]
    document = _extract(path, ocr="never")
    layout = build_layout("j-1", document, path, max_image_pixels=MAX_PIXELS)

    assert layout["kind"] == "pages"
    assert layout["offset_encoding"] == "utf-16"
    assert layout["pages"] == [
        {
            "index": 0,
            "width": 595.0,
            "height": 842.0,
            "unit": "pt",
            "source": "text",
            "scales": [1.0, 1.5, 2.0, 3.0],
        }
    ]
    words = layout["words"]
    assert len(words) == sum(1 for segment in document.segments if segment.bbox is not None)
    # Reading order follows source order.
    assert [word[5] for word in words] == sorted(word[5] for word in words)
    for page, x0, y0, x1, y1, start, end, line in words:
        assert page == 0 and 0 <= x0 < x1 <= 595 and 0 <= y0 < y1 <= 842
        assert document.text[start:end] and line >= 0
    # Words on a line share the line number; the address has three entries.
    address = [word for word in words if document.text[word[5] : word[6]] in {"MAX", "MUSTERMANN"}]
    assert len({word[7] for word in address}) == 1
    # The white control text ("Befund 1.1") is marked as hidden.
    (hidden,) = layout["hidden"]
    assert hidden["reason"] == "white"
    assert document.text[hidden["start"] : hidden["end"]] == layout_corpus.CONTROL_TEXT


def test_every_visible_word_box_sits_on_ink(corpus: dict[str, Path]) -> None:
    path = corpus["lieferschein.pdf"]
    layout = build_layout("j", _extract(path, ocr="never"), path, max_image_pixels=MAX_PIXELS)
    image = _render(path, DocumentFormat.PDF)
    for word in _visible_words(layout):
        assert _ink(_crop(image, tuple(word[1:5]), SCALE)) > 0.03, word


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_pages_show_the_same_pixels_under_each_box(
    corpus: dict[str, Path], rotation: int
) -> None:
    upright_path = corpus["lieferschein.pdf"]
    turned_path = corpus[f"lieferschein-rot{rotation}.pdf"]
    upright = build_layout(
        "a", _extract(upright_path, ocr="never"), upright_path, max_image_pixels=MAX_PIXELS
    )
    turned = build_layout(
        "b", _extract(turned_path, ocr="never"), turned_path, max_image_pixels=MAX_PIXELS
    )
    page = turned["pages"][0]
    if rotation in {90, 270}:
        assert (page["width"], page["height"]) == (842.0, 595.0)
    upright_image = _render(upright_path, DocumentFormat.PDF)
    turned_image = _render(turned_path, DocumentFormat.PDF)
    assert turned_image.size == (round(page["width"] * SCALE), round(page["height"] * SCALE))

    # PIL rotates counter-clockwise, ``/Rotate`` rotates clockwise.
    for a, b in zip(_visible_words(upright), _visible_words(turned), strict=True):
        assert a[5:7] == b[5:7]
        expected = _crop(upright_image, tuple(a[1:5]), SCALE)
        actual = _crop(turned_image, tuple(b[1:5]), SCALE).rotate(rotation, expand=True)
        assert _difference(expected, actual) < 12, (a, b)


def test_cropbox_offset_needs_no_conversion(corpus: dict[str, Path]) -> None:
    upright_path = corpus["lieferschein.pdf"]
    cropped_path = corpus["lieferschein-cropbox.pdf"]
    upright = build_layout(
        "a", _extract(upright_path, ocr="never"), upright_path, max_image_pixels=MAX_PIXELS
    )
    cropped = build_layout(
        "b", _extract(cropped_path, ocr="never"), cropped_path, max_image_pixels=MAX_PIXELS
    )
    assert (cropped["pages"][0]["width"], cropped["pages"][0]["height"]) == (555.0, 782.0)
    # The control text lies outside the CropBox: not in text, not in image.
    assert cropped["hidden"] == []
    shifted = {(round(word[1] - 20, 1), round(word[2] - 30, 1)) for word in _visible_words(upright)}
    assert {(round(word[1], 1), round(word[2], 1)) for word in cropped["words"]} <= shifted


def test_hidden_text_reasons(tmp_path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((20, 30), "Weisstext", fontsize=10, color=(1, 1, 1))
    page.insert_text((20, 60), "Unsichtbar", fontsize=10, render_mode=3)
    page.insert_text((20, 80), "Winzig", fontsize=1.5)
    page.draw_rect(fitz.Rect(15, 100, 150, 130), color=None, fill=(0.1, 0.1, 0.3))
    page.insert_text((20, 120), "Logoschrift", fontsize=10, color=(1, 1, 1))
    page.insert_text((20, 160), "Sichtbar", fontsize=10)
    path = tmp_path / "versteckt.pdf"
    document.save(path)
    document.close()

    extracted = _extract(path, ocr="never")
    layout = build_layout("j", extracted, path, max_image_pixels=MAX_PIXELS)
    reasons = {
        extracted.text[item["start"] : item["end"]]: item["reason"] for item in layout["hidden"]
    }
    assert reasons == {"Weisstext": "white", "Unsichtbar": "invisible", "Winzig": "tiny"}


# -- Scans and images (OCR) --------------------------------------------------


class RecordingOcr:
    """Provides fixed boxes in the given image and stores the image."""

    def __init__(self, boxes: list[tuple[str, tuple[int, int, int, int]]] | None = None) -> None:
        self.boxes = boxes
        self.images: list[Image.Image] = []

    def recognize(self, image: Image.Image, *, languages: tuple[str, ...]) -> tuple[OcrWord, ...]:
        self.images.append(image.copy())
        width, height = image.size
        boxes = self.boxes or [
            ("Oben", (width // 10, height // 12, width // 4, height // 12 + 40)),
            ("Mitte", (width // 3, height // 2, width // 2, height // 2 + 40)),
        ]
        return tuple(
            OcrWord(text, left=x0, top=y0, width=x1 - x0, height=y1 - y0, line_id=(1, 1, 1, 1))
            for text, (x0, y0, x1, y1) in boxes
        )


@pytest.mark.parametrize("name", ["scan.pdf", "scan-rot90.pdf"])
def test_scan_ocr_boxes_land_where_the_ocr_saw_them(corpus: dict[str, Path], name: str) -> None:
    path = corpus[name]
    ocr = RecordingOcr()
    document = _extract(path, ocr=ocr)
    layout = build_layout("j", document, path, max_image_pixels=MAX_PIXELS)
    (page,) = layout["pages"]
    assert page["source"] == "ocr"
    (seen,) = ocr.images
    # Extraction scales x and y independently; the image dimensions are rounded to integers.
    fx, fy = page["width"] / seen.width, page["height"] / seen.height
    assert fx == pytest.approx(fy, rel=1e-3)

    image = _render(path, DocumentFormat.PDF)
    gray = seen.convert("L")
    for word, (_, (x0, y0, x1, y1)) in zip(layout["words"], ocr.boxes or _boxes(seen), strict=True):
        assert word[1:5] == pytest.approx([x0 * fx, y0 * fy, x1 * fx, y1 * fy], abs=0.02)
        expected = gray.crop((x0, y0, x1, y1))
        assert _difference(expected, _crop(image, tuple(word[1:5]), SCALE)) < 12


def _boxes(image: Image.Image) -> list[tuple[str, tuple[int, int, int, int]]]:
    width, height = image.size
    return [
        ("Oben", (width // 10, height // 12, width // 4, height // 12 + 40)),
        ("Mitte", (width // 3, height // 2, width // 2, height // 2 + 40)),
    ]


def test_exif_rotated_photo_uses_display_pixels(corpus: dict[str, Path]) -> None:
    path = corpus["foto-exif6.jpg"]
    ocr = RecordingOcr()
    document = _extract(path, ocr=ocr)
    layout = build_layout("j", document, path, max_image_pixels=MAX_PIXELS)
    (seen,) = ocr.images
    (page,) = layout["pages"]
    with Image.open(path) as stored:
        assert stored.size == (seen.height, seen.width)
    assert (page["width"], page["height"], page["unit"]) == (seen.width, seen.height, "px")
    assert page["scales"] == [0.5, 1.0]

    image = _render(path, DocumentFormat.JPEG, scale=1.0)
    assert image.size == seen.size
    for word, (_, box) in zip(layout["words"], _boxes(seen), strict=True):
        assert word[1:5] == pytest.approx(list(box))
        assert _difference(seen.convert("L").crop(box), image.crop(box)) < 4


def test_offsets_are_utf16(corpus: dict[str, Path]) -> None:
    path = corpus["bildschirmfoto.png"]
    ocr = RecordingOcr([("𝔄b", (10, 10, 40, 30)), ("Hans", (50, 10, 90, 30))])
    document = _extract(path, ocr=ocr)
    layout = build_layout("j", document, path, max_image_pixels=MAX_PIXELS)
    assert document.text == "𝔄b Hans"
    assert [word[5:7] for word in layout["words"]] == [[0, 3], [4, 8]]


@pytest.mark.parametrize("name", ["notiz.txt", "notiz.md"])
def test_formats_without_layout_fall_back_to_text(tmp_path: Path, name: str) -> None:
    # Excel and CSV have used a grid since spec §R (see test_layout_spreadsheets.py).
    path = tmp_path / name
    path.write_text("Max Mustermann ruft an.\n", encoding="utf-8")
    layout = build_layout("j", _extract(path), path, max_image_pixels=MAX_PIXELS)
    assert layout == {"job_id": "j", "kind": "text", "offset_encoding": "utf-16"}


def test_page_scales_respect_the_pixel_limit(corpus: dict[str, Path]) -> None:
    path = corpus["lieferschein.pdf"]
    limit = int(595 * 1.5 * 842 * 1.5) + 1
    layout = build_layout("j", _extract(path, ocr="never"), path, max_image_pixels=limit)
    assert layout["pages"][0]["scales"] == [1.0, 1.5]


# -- memory ----------------------------------------------------------------


def test_job_memory_cache_evicts_and_forgets() -> None:
    cache = JobMemoryCache(max_bytes=10)
    cache.put("a", 1, b"1234")
    cache.put("b", 1, b"5678")
    assert cache.get("a", 1) == b"1234"  # entry "a" was most recently used
    cache.put("c", 1, b"9999")
    assert cache.get("b", 1) is None and cache.get("a", 1) == b"1234"
    cache.put("a", 2, b"xx")
    cache.forget("a")
    assert cache.get("a", 1) is None and cache.get("a", 2) is None
    assert cache.size == 4
    cache.put("big", 1, b"x" * 11)
    assert cache.get("big", 1) is None


# -- API ---------------------------------------------------------------------

TOKEN = "layout-phase-m"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class NameAnalyzer:
    def nlp_status(self) -> dict[str, Any]:
        return {"active": "layout-offline", "loaded": True, "device": "cpu"}

    def analyze_for_profile(
        self, text: str, profile: Profile, *, progress: Callable[[str], None]
    ) -> DetectionResult:
        for phase in ("master_data", "rules", "ner", "suspicion"):
            progress(phase)
        start = text.find("MUSTERMANN")
        if start < 0:
            return DetectionResult(confirmed=())
        return DetectionResult(
            confirmed=(
                Finding(
                    entity_type="PERSON",
                    start=start,
                    end=start + len("MUSTERMANN"),
                    score=0.95,
                    source=FindingSource.NER,
                    text="MUSTERMANN",
                    detector="layout-offline",
                ),
            )
        )


@pytest.fixture
def api(tmp_path: Path) -> Iterator[tuple[TestClient, PhaseOneServices]]:
    settings = Settings(
        token=TOKEN,
        state_directory=tmp_path / "state",
        job_ttl_seconds=60,
        max_text_bytes=1024 * 1024,
    )
    analyzer = NameAnalyzer()
    services = PhaseOneServices(
        settings,
        DiagnosticsRecorder(),
        analyzer=analyzer,
        jobs=JobManager(
            analyzer,
            max_workers=1,
            ttl_seconds=60,
            max_text_bytes=settings.max_text_bytes,
            root=tmp_path / "jobs",
        ),
        extractor=DocumentExtractor(ocr_backend=RecordingOcr()),
        profiles=load_profiles(),
        capacity=WorkerCapacity(1, 0, 2, 8 * 1024**3),
    )
    services.second_opinion_model = lambda: None  # type: ignore[method-assign]
    with TestClient(create_app(settings, DiagnosticsRecorder(), phase_one=services)) as client:
        yield client, services


def _submit(client: TestClient, path: Path, media_type: str) -> str:
    entry = client.post("/entries", headers=AUTH, json={"kind": "documents"}).json()["id"]
    response = client.post(
        f"/entries/{entry}/files",
        headers=AUTH,
        files=[("file", (path.name, path.read_bytes(), media_type))],
    )
    assert response.status_code == 202, response.text
    file_id = response.json()["accepted"][0]["id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        progress = client.get(f"/entries/{entry}/progress", headers=AUTH).json()
        if progress["done"] == progress["total"]:
            assert progress["files"][0]["status"] == "review", progress
            return file_id
        time.sleep(0.01)
    raise AssertionError("Erkennung wurde nicht fertig.")


def test_api_serves_layout_and_page_images(
    api: tuple[TestClient, PhaseOneServices], corpus: dict[str, Path]
) -> None:
    client, services = api
    job = _submit(client, corpus["lieferschein-rot90.pdf"], "application/pdf")

    response = client.get(f"/jobs/{job}/layout", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    layout = response.json()
    assert layout["kind"] == "pages" and layout["job_id"] == job
    findings = client.get(f"/jobs/{job}/findings", headers=AUTH).json()
    (finding,) = findings["findings"]
    # The word from /findings appears at the same position in the layout map.
    assert any(
        word[5] == finding["start"] and word[6] == finding["end"] for word in layout["words"]
    )

    page = client.get(f"/jobs/{job}/pages/0?scale=1.5", headers=AUTH)
    assert page.status_code == 200
    assert page.headers["content-type"] == "image/png"
    assert page.headers["cache-control"] == "no-store"
    with Image.open(io.BytesIO(page.content)) as image:
        # MuPDF rounds 892.5 up.
        assert image.size == (1263, 893)
    assert services.job_memory().size > 0

    assert client.get(f"/jobs/{job}/pages/0?scale=1.25", headers=AUTH).status_code == 422
    assert client.get(f"/jobs/{job}/pages/1?scale=2", headers=AUTH).status_code == 404
    assert client.get(f"/jobs/{job}/pages/0").status_code == 401
    assert client.get(f"/jobs/{job}/layout").status_code == 401

    # Memory cleared: page images removed, but the state remains.
    # The page will be regenerated on the next request.
    assert client.delete(f"/jobs/{job}", headers=AUTH).status_code == 204
    assert services.job_memory().size == 0
    assert not services.jobs().is_loaded(job)
    assert client.get(f"/jobs/{job}/pages/0?scale=2", headers=AUTH).status_code == 200


def test_api_layout_without_fixed_pages(
    api: tuple[TestClient, PhaseOneServices], corpus: dict[str, Path]
) -> None:
    client, _ = api
    job = _submit(client, corpus["kunden.csv"], "text/csv")
    assert client.get(f"/jobs/{job}/layout", headers=AUTH).json()["kind"] == "grid"
    assert client.get(f"/jobs/{job}/pages/0?scale=2", headers=AUTH).status_code == 422


def test_unknown_job_is_404(api: tuple[TestClient, PhaseOneServices]) -> None:
    client, _ = api
    assert client.get("/jobs/nope/layout", headers=AUTH).status_code == 404
