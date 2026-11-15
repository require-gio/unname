"""OCR with Apple Vision.

The protocol is verified against a fake helper, a Python script that runs
like the Office-engine tests. With ``@pytest.mark.vision`` the tests use the real
helper (``vision-ocr/.build/release``) on macOS only: they detect scans, photos
and screenshots, store a scan and an image with codes and read them back (A-29).
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import fitz
import pytest
from PIL import Image

from presidio_sidecar import exporting, resources
from presidio_sidecar.exporting import _read_code_sheet
from presidio_sidecar.extraction import DocumentExtractor, ExtractionMethod, OcrWord, ocr_vision
from presidio_sidecar.extraction.ocr import (
    LimitedOcrBackend,
    OcrUnavailableError,
    TesseractOcrBackend,
    default_ocr_backend,
    readback_layout_of,
)
from presidio_sidecar.extraction.ocr_vision import VisionOcrBackend
from test_layout_map import corpus, layout_corpus  # noqa: F401
from test_layout_pdf_export import _processing

#: Behaves like ``unname-vision-ocr``; ``MODE`` selects a failure mode.
#: Each invocation logs its arguments, environment and image header to ``LOG``.
_FAKE_HELPER = """\
#!{python}
import json, os, sys, time
MODE = {mode!r}
LOG = {log!r}
def log(entry):
    with open(LOG, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\\n")
if sys.argv[1:] == ["info"]:
    log({{"argv": sys.argv[1:], "env": sorted(os.environ), "stdin_tty": sys.stdin.read() == ""}})
    if MODE == "exit5-info":
        sys.exit(5)
    revisions = [1, 2] if MODE == "no-rev3" else [1, 2, 3]
    languages = ["en-US"] if MODE == "no-german" else ["en-US", "de-DE", "fr-FR"]
    print(json.dumps({{"engine": "apple-vision", "protocol": 2 if MODE == "protocol2" else 1,
        "helper": "1.0.0", "os": "27.2", "os_build": "26B123",
        "text_revisions": revisions, "document_revisions": [1], "languages": languages}}))
    sys.exit(0)
if sys.argv[1:2] != ["recognize"]:
    sys.exit(2)
data = sys.stdin.buffer.read()
magic, size, _maxval = data.split(b"\\n", 3)[:3]
width, height = (int(value) for value in size.split())
log({{"argv": sys.argv[1:], "env": sorted(os.environ), "magic": magic.decode(),
     "width": width, "height": height, "bytes": len(data)}})
if MODE == "sleep":
    time.sleep(5)
if MODE == "exit5":
    sys.stderr.write("Sprache nicht unterstuetzt: de-DE")
    sys.exit(5)
if MODE == "broken":
    sys.stdout.write("{{kein json")
    sys.exit(0)
words = [
    {{"text": "Max", "left": 10, "top": 10, "width": 30, "height": 12, "line": [1, 1, 1],
     "confidence": 1.0}},
    {{"text": "Mustermann", "left": 45, "top": 10, "width": 80, "height": 12, "line": [1, 1, 1],
     "confidence": 0.5}},
]
if MODE == "outside":
    words[1]["width"] = width
if MODE == "bad-line":
    words[1]["line"] = [0, 1, 1]
revision = 2 if MODE == "rev2" else 3
print(json.dumps({{"protocol": 1, "request": "text", "revision": revision, "os_build": "26B123",
    "width": width, "height": height, "tiles": "1x1", "elapsed_ms": 3, "words": words}}))
"""


def _helper(tmp_path: Path, mode: str = "ok") -> Path:
    path = tmp_path / f"unname-vision-ocr-{mode}"
    path.write_text(
        _FAKE_HELPER.format(python=sys.executable, mode=mode, log=str(tmp_path / f"{mode}.log")),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _log(tmp_path: Path, mode: str = "ok") -> list[dict[str, Any]]:
    path = tmp_path / f"{mode}.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Install a fake helper for each mode and discard the logged probe."""

    monkeypatch.setattr(resources, "bundled_assets_root", lambda: None)
    monkeypatch.setenv("PRESIDIO_SESSION_GEHEIMNIS", "darf-nicht-durch")

    def install(mode: str = "ok") -> Path:
        path = _helper(tmp_path, mode)
        monkeypatch.setenv(resources.VISION_OCR_ENV, str(path))
        ocr_vision.reset_probe()
        return path

    yield install
    ocr_vision.reset_probe()


def _image(width: int = 200, height: int = 50, mode: str = "RGB") -> Image.Image:
    return Image.new(mode, (width, height), 255 if mode == "L" else (255, 255, 255))


# -- Protocol against the fake helper -------------------------------------------


def test_info_checked_once_and_names_engine(helper: Any, tmp_path: Path) -> None:
    helper()
    first = ocr_vision.vision_helper_info()
    second = ocr_vision.vision_helper_info()

    assert first is second
    assert len(_log(tmp_path)) == 1
    engine = first.engine()
    assert (engine.name, engine.version, engine.request, engine.revision) == (
        "apple-vision",
        "1.0.0",
        "text",
        3,
    )
    assert (engine.os_version, engine.os_build, engine.readback_layout) == (
        "27.2",
        "26B123",
        "sheet",
    )
    # No child process for ``/health``.
    assert ocr_vision.cached_helper_info() is first
    assert ocr_vision.cached_probe_failure() is None


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("no-rev3", "Revision 3"),
        ("no-german", "de-DE"),
        ("protocol2", "Protokoll"),
        ("exit5-info", "nicht verfuegbar"),
    ],
)
def test_info_rejects_unsuitable_helpers(helper: Any, mode: str, message: str) -> None:
    helper(mode)
    with pytest.raises(OcrUnavailableError, match=message):
        ocr_vision.vision_helper_info()
    # The failure remains recorded.
    assert isinstance(ocr_vision.cached_probe_failure(), OcrUnavailableError)
    with pytest.raises(OcrUnavailableError, match=message):
        VisionOcrBackend().recognize(_image(), languages=("deu",))


def test_without_helper_vision_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resources, "bundled_assets_root", lambda: None)
    monkeypatch.setattr(resources, "repository_root", lambda: tmp_path)
    monkeypatch.delenv(resources.VISION_OCR_ENV, raising=False)
    ocr_vision.reset_probe()
    try:
        with pytest.raises(OcrUnavailableError, match="Vision-Helfer fehlt"):
            VisionOcrBackend().recognize(_image(), languages=("deu",))
    finally:
        ocr_vision.reset_probe()


def test_recognize_sends_ppm_with_vision_languages_and_minimal_environment(
    helper: Any, tmp_path: Path
) -> None:
    helper()
    words = VisionOcrBackend().recognize(_image(200, 50), languages=("deu", "eng"))

    assert words == (
        OcrWord("Max", 10, 10, 30, 12, (1, 1, 1, 1), 1.0),
        OcrWord("Mustermann", 45, 10, 80, 12, (1, 1, 1, 1), 0.5),
    )
    info, call = _log(tmp_path)
    assert info["argv"] == ["info"]
    assert info["stdin_tty"], "info bekommt kein stdin des Sidecars"
    assert call["argv"] == [
        "recognize",
        "--languages",
        "de-DE,en-US",
        "--tiles",
        "auto",
        "--correction",
        "off",
        "--qos",
        "user-initiated",
    ]
    assert (call["magic"], call["width"], call["height"]) == ("P6", 200, 50)
    assert call["bytes"] == len(b"P6\n200 50\n255\n") + 200 * 50 * 3
    # Only what the helper needs to start; no session variables.
    assert "PRESIDIO_SESSION_GEHEIMNIS" not in call["env"]
    assert set(call["env"]) <= {"HOME", "TMPDIR", "__CF_USER_TEXT_ENCODING", "LC_CTYPE"}


def test_grayscale_sent_as_pgm(helper: Any, tmp_path: Path) -> None:
    helper()
    VisionOcrBackend().recognize(_image(200, 50, "L"), languages=("eng",))
    assert _log(tmp_path)[-1]["magic"] == "P5"
    assert _log(tmp_path)[-1]["argv"][2] == "en-US"
    # Other modes become RGB.
    VisionOcrBackend().recognize(Image.new("RGBA", (200, 50)), languages=("eng",))
    assert _log(tmp_path)[-1]["magic"] == "P6"


def test_unknown_language_unavailable(helper: Any) -> None:
    helper()
    with pytest.raises(OcrUnavailableError, match="Deutsch und Englisch"):
        VisionOcrBackend().recognize(_image(), languages=("fra",))
    assert ocr_vision.vision_languages(("deu", "deu", "eng")) == ("de-DE", "en-US")


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("broken", "Protokoll"),
        ("outside", "Positionsdaten"),
        ("bad-line", "Positionsdaten"),
        ("rev2", "Revision"),
        ("exit5", "Sprache oder Revision"),
    ],
)
def test_broken_responses_rejected_without_content(helper: Any, mode: str, message: str) -> None:
    helper(mode)
    with pytest.raises(OcrUnavailableError, match=message) as caught:
        VisionOcrBackend().recognize(_image(), languages=("deu",))
    assert "Mustermann" not in str(caught.value)


def test_timeout_and_longer_limit_for_first_call(
    helper: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper("sleep")
    backend = VisionOcrBackend(timeout_seconds=0.2, first_timeout_seconds=0.3)
    with pytest.raises(OcrUnavailableError, match="zu lange"):
        backend.recognize(_image(), languages=("deu",))

    helper()
    timeouts: list[float] = []
    run = subprocess.run

    def recording(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("input") is not None:
            timeouts.append(kwargs["timeout"])
        return run(*args, **kwargs)

    monkeypatch.setattr(ocr_vision.subprocess, "run", recording)
    backend = VisionOcrBackend()
    backend.recognize(_image(), languages=("deu",))
    backend.recognize(_image(), languages=("deu",))
    assert timeouts == [
        ocr_vision.FIRST_RECOGNIZE_TIMEOUT_SECONDS,
        ocr_vision.RECOGNIZE_TIMEOUT_SECONDS,
    ]
    assert backend.warmed_up
    assert backend.last_elapsed_ms == 3


def test_too_large_image_not_sent_to_helper(helper: Any, tmp_path: Path) -> None:
    helper()
    with pytest.raises(OcrUnavailableError, match="zu gross"):
        ocr_vision.image_to_netpbm(Image.new("L", (20_000, 5_001)))
    with pytest.raises(OcrUnavailableError):
        ocr_vision.image_to_netpbm(b"keine Pixel")


def test_warm_up_detects_mini_image(helper: Any, tmp_path: Path) -> None:
    helper()
    backend = VisionOcrBackend()
    assert ocr_vision.warm_up(backend) >= 0
    assert backend.warmed_up
    assert _log(tmp_path)[-1]["magic"] == "P5"


# -- Selection per platform ----------------------------------------------------


def test_selection_per_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(resources.OCR_ENGINE_ENV, raising=False)
    monkeypatch.setattr(resources.sys, "platform", "darwin")
    assert isinstance(default_ocr_backend(), VisionOcrBackend)
    monkeypatch.setattr(resources.sys, "platform", "win32")
    assert isinstance(default_ocr_backend(), TesseractOcrBackend)

    # Switchable during development (A/B) …
    monkeypatch.setattr(resources.sys, "platform", "darwin")
    monkeypatch.setenv(resources.OCR_ENGINE_ENV, "tesseract")
    assert isinstance(default_ocr_backend(), TesseractOcrBackend)
    # ... frozen, no fallback on macOS.
    monkeypatch.setattr(resources.sys, "frozen", True, raising=False)
    try:
        assert isinstance(default_ocr_backend(), VisionOcrBackend)
    finally:
        monkeypatch.delattr(resources.sys, "frozen", raising=False)


def test_readback_per_engine() -> None:
    assert readback_layout_of(VisionOcrBackend()) == "sheet"
    assert readback_layout_of(LimitedOcrBackend(VisionOcrBackend(), parallel_slots=2)) == "sheet"
    assert readback_layout_of(TesseractOcrBackend()) == "page"
    assert readback_layout_of(object()) == "page"


def test_method_comes_from_backend(helper: Any) -> None:
    helper()
    image = Image.new("RGB", (200, 50), (255, 255, 255))
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    backend = LimitedOcrBackend(VisionOcrBackend(), parallel_slots=2)
    result = DocumentExtractor(ocr_backend=backend).extract_bytes(
        buffer.getvalue(), filename="foto.png", ocr="auto"
    )
    assert result.method is ExtractionMethod.APPLE_VISION
    assert "Mustermann" in result.text


# -- Sheet ---------------------------------------------------------------


class SheetReader:
    """Read each line ``code<n>`` on the sheet in two parts; the right part is
    slightly higher, matching how Vision sometimes splits a code."""

    engine_name = "apple-vision"

    def __init__(self) -> None:
        self.sheets: list[tuple[int, int]] = []

    def recognize(self, image: Image.Image, *, languages: tuple[str, ...]) -> tuple[OcrWord, ...]:
        del languages
        line, gap, margin = (
            exporting._SHEET_LINE_HEIGHT,
            exporting._SHEET_GAP,
            exporting._SHEET_MARGIN,
        )
        offset = len(self.sheets) * exporting._SHEET_MAX_CODES
        self.sheets.append(image.size)
        rows = (image.height - 2 * margin + gap) // (line + gap)
        words = []
        for row in range(rows):
            top = margin + row * (line + gap)
            words.append(
                OcrWord(f"code{offset + row}", 140, top + 2, 60, line - 6, (1, 1, 1, 1), 1.0)
            )
            words.append(OcrWord("teil", margin, top + 6, 60, line - 10, (1, 1, 1, 1), 1.0))
        return tuple(words)


def test_code_sheet_orders_by_line_and_splits_long_lists() -> None:
    crops: list[Image.Image | None] = [Image.new("RGB", (60, 12), (255, 255, 255))] * 32
    crops[5] = None
    reader = SheetReader()

    read = _read_code_sheet(crops, backend=reader, languages=("deu",))

    line, gap, margin = exporting._SHEET_LINE_HEIGHT, exporting._SHEET_GAP, exporting._SHEET_MARGIN
    assert len(reader.sheets) == 2, "hoechstens 30 Codes je Blatt"
    assert reader.sheets[0][1] == 2 * margin + 30 * line + 29 * gap
    # Each row uses the sheet's line height: 60 × 12 → 5×.
    assert reader.sheets[0][0] == 60 * line // 12 + 2 * margin
    assert read[5] == ""
    present = [index for index in range(32) if index != 5]
    for position, index in enumerate(present):
        # Left before right, even if the right part is higher.
        assert read[index] == f"teil code{position}"


@pytest.mark.parametrize(
    ("read", "expected", "ok"),
    [
        ("<<PERSON_c8ccc8db>>", "<<PERSON_c8ccc8db>>", True),
        ("<<PERSON c8ccc8db>>", "<<PERSON_c8ccc8db>>", True),
        ("<<EMAIL_сdf5b3e8>>", "<<EMAIL_cdf5b3e8>>", True),
        ("<<ADRESSE_C0913267>>", "<<ADRESSE_c09132b7>>", False),
        ("<<EMAIL_C225f925>>", "<<EMAIL_c225f925>>", True),
        ("<<PERSON_ef25d858>", "<<PERSON_ef25d858>>", True),
        ("<<PERSON_ef25d85>>", "<<PERSON_ef25d858>>", False),
        ("<<EMAIL_ef25d858>>", "<<PERSON_ef25d858>>", False),
    ],
)
def test_scan_readback_tolerates_only_ocr_artifacts(read: str, expected: str, ok: bool) -> None:
    assert exporting._scan_readback_matches(read, expected) is ok


# -- Real detection (macOS only with built helper) -------------------------


def _vision_ready() -> bool:
    if sys.platform != "darwin" or resources.vision_ocr_executable() is None:
        return False
    try:
        ocr_vision.vision_helper_info()
    except OcrUnavailableError:
        return False
    return True


vision = pytest.mark.skipif(
    not _vision_ready(),
    reason=f"Vision-Helfer fehlt; einmalig `{resources.VISION_OCR_BUILD_COMMAND}`",
)


def _vision_extract(path: Path, backend: VisionOcrBackend | None = None) -> Any:
    extractor = DocumentExtractor(ocr_backend=backend or VisionOcrBackend())
    return extractor.extract_bytes(path.read_bytes(), filename=path.name, ocr="auto")


@pytest.mark.vision
@vision
@pytest.mark.parametrize("name", ["scan.pdf", "scan-rot90.pdf"])
def test_vision_reads_scan(corpus: dict[str, Path], name: str) -> None:  # noqa: F811
    document = _vision_extract(corpus[name])

    assert document.method is ExtractionMethod.APPLE_VISION
    for expected in (
        "MUSTERMANN",
        layout_corpus.STREET,
        layout_corpus.EMAIL,
        "XI1234567",
        "4QK2AB",
    ):
        assert expected in document.text, expected
    # Table rows: the item appears before its price.
    assert document.text.index("ARMLEHNSTUHL") < document.text.index("179,10")


@pytest.mark.vision
@vision
def test_vision_reads_photo_with_exif_and_screenshot(corpus: dict[str, Path]) -> None:  # noqa: F811
    photo = _vision_extract(corpus["foto-exif6.jpg"])
    assert "ATU13585627" in photo.text
    screen = _vision_extract(corpus["bildschirmfoto.png"])
    assert "MUSTERMANN" in screen.text


@pytest.mark.vision
@vision
@pytest.mark.parametrize("name", ["scan.pdf", "scan-rot90.pdf"])
def test_vision_saves_scan_and_reads_back_every_code(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
    name: str,
) -> None:
    """A-29 with real detection: code sheet, check original values."""

    from presidio_sidecar.document_workflow import export_processed_document

    path = corpus[name]
    backend = VisionOcrBackend()
    document = _vision_extract(path, backend)
    values = ("MUSTERMANN", layout_corpus.EMAIL)
    spans = []
    for value in values:
        offset = 0
        while (start := document.text.find(value, offset)) >= 0:
            spans.append((start, start + len(value)))
            offset = start + len(value)
    processing = _processing(document.text, spans)
    target = tmp_path / f"{path.stem}-geschuetzt.pdf"

    result = export_processed_document(
        path,
        target,
        document=document,
        source_text=document.text,
        processing=processing,
        ocr_backend=backend,
    )

    assert result.verification.readback_checked == len(spans)
    with fitz.open(target) as saved:
        text = saved[0].get_text()
    for value in values:
        assert value not in text


@pytest.mark.vision
@vision
def test_vision_saves_image_and_reads_back_code(
    corpus: dict[str, Path],  # noqa: F811
    tmp_path: Path,
) -> None:
    from presidio_sidecar.document_workflow import export_processed_document

    path = corpus["bildschirmfoto.png"]
    backend = VisionOcrBackend()
    document = _vision_extract(path, backend)
    start = document.text.index("MUSTERMANN")
    processing = _processing(document.text, [(start, start + len("MUSTERMANN"))])

    target = tmp_path / "bild.png"
    export_processed_document(
        path,
        target,
        document=document,
        source_text=document.text,
        processing=processing,
        ocr_backend=backend,
    )
    assert target.is_file()
