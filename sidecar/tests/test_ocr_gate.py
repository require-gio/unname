"""OCR quality gate: corpus, metrics, locks."""

from __future__ import annotations

import hashlib
import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from presidio_sidecar.extraction import OcrWord

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
ocr_corpus = importlib.import_module("scripts.quality.ocr_corpus")
ocr_gate = importlib.import_module("scripts.quality.ocr_gate")

fonts = pytest.mark.skipif(
    not (ocr_corpus.FONTSOURCE / "carlito").is_dir(),
    reason="OFL-Schriften fehlen; einmalig `npm ci --prefix app`",
)


def _perfect(page: Any) -> tuple[OcrWord, ...]:
    """An OCR that reads the exact truth."""

    return tuple(
        OcrWord(
            word.text,
            word.box[0],
            word.box[1],
            word.box[2] - word.box[0],
            word.box[3] - word.box[1],
            (1, 1, 1, word.line),
            1.0,
        )
        for word in page.words
    )


@fonts
def test_corpus_is_deterministic_and_covers_plan() -> None:
    first = list(ocr_corpus.pages())
    second = list(ocr_corpus.pages())
    assert len(first) == ocr_corpus.PAGES
    for a, b in zip(first[:3], second[:3], strict=True):
        assert a.id == b.id
        assert (
            hashlib.sha256(a.image.tobytes()).digest() == hashlib.sha256(b.image.tobytes()).digest()
        )
        assert a.words == b.words
    assert {page.layout for page in first} == set(ocr_corpus.LAYOUTS)
    assert {page.font for page in first} == set(ocr_corpus.FONTS)
    assert {page.dpi for page in first} == set(ocr_corpus.DPIS)
    assert set(ocr_corpus.AUGMENTS) <= {page.augment for page in first}
    assert {page.size_pt for page in first} == set(ocr_corpus.SIZES_PT)
    kinds = {word.pii for page in first for word in page.words}
    assert {"NAME", "ADDRESS", "IBAN", "PHONE", "EMAIL", "UID"} <= kinds
    # Every box is within the image, even on rotated or skewed pages.
    for page in first:
        width, height = page.image.size
        for word in page.words:
            left, top, right, bottom = word.box
            assert -2 <= left < right <= width + 2 and -2 <= top < bottom <= height + 2
    sheets = list(ocr_corpus.code_sheets())
    assert sum(len(sheet.codes) for sheet in sheets) == 144
    assert {sheet.size_pt for sheet in sheets} == {6, 8, 10, 12}


@fonts
def test_perfect_ocr_reaches_all_metrics() -> None:
    page = next(page for page in ocr_corpus.pages() if page.upright)
    result = ocr_gate.evaluate_page(page, _perfect(page), 0.1)
    assert result.pii_found == result.pii > 0
    assert result.words_found == result.words
    assert result.pii_covered == result.pii
    assert result.char_errors == 0
    assert result.overreach == []


@fonts
def test_errors_reduce_recall_and_overreaches_are_counted() -> None:
    page = next(page for page in ocr_corpus.pages() if page.upright and page.layout == "brief")
    words = list(_perfect(page))
    pii = next(index for index, word in enumerate(page.words) if word.pii == "IBAN")
    original = words[pii]
    words[pii] = OcrWord(
        original.text.replace("1", "l") + "x",
        original.left,
        original.top,
        original.width,
        original.height,
        original.line_id,
        1.0,
    )
    # A word whose box extends deep into the next line.
    first = next(index for index, word in enumerate(page.words) if word.line == 5)
    lower = next(word for word in page.words if word.line == 6)
    stretched = words[first]
    words[first] = OcrWord(
        stretched.text,
        lower.box[0],
        stretched.top,
        stretched.width,
        lower.box[3] - stretched.top,
        stretched.line_id,
        1.0,
    )

    result = ocr_gate.evaluate_page(page, tuple(words), 0.1)

    assert result.pii_found == result.pii - 1
    assert result.char_errors > 0
    assert result.overreach


def test_normalization_and_edit_distance() -> None:
    assert ocr_gate.normalize("„Kowalczyk•") == "Kowalczyk"
    assert ocr_gate.normalize("5864.") == "5864"
    assert ocr_gate.normalize("ﬁ") == "fi"
    assert ocr_gate.levenshtein("Yılmaz", "Yilmaz") == 1
    assert ocr_gate.levenshtein("", "abc") == 3


def _report(**metrics: float) -> dict[str, Any]:
    base = {
        "pii_word_recall": 0.97,
        "word_recall": 0.98,
        "cer": 0.01,
        "pii_box_coverage": 0.99,
        "overreach": 0,
        "readback_rate": 0.99,
        "seconds_per_page_median": 1.0,
    }
    return {
        "key": "apple-vision/text-r3/macos27/hardware",
        "profile": "hardware",
        "corpus": {"version": ocr_corpus.VERSION},
        "metrics": base | metrics,
        "pages": [{"page": "p00", "overreach": []}],
    }


def test_check_blocks_any_regression_against_own_baseline() -> None:
    baseline = {
        "baselines": {
            "apple-vision/text-r3/macos27/hardware": {
                "corpus_version": ocr_corpus.VERSION,
                "metrics": _report()["metrics"],
            }
        }
    }
    assert ocr_gate.check(_report(), baseline) == []
    assert ocr_gate.check(_report(seconds_per_page_median=9.0), baseline) == []
    assert ocr_gate.check(_report(pii_word_recall=0.969), baseline)
    assert ocr_gate.check(_report(overreach=1), baseline)
    assert ocr_gate.check(_report(cer=0.02), baseline)
    # CI VMs have leeway.
    ci = _report(pii_word_recall=0.96)
    ci["profile"] = "ci-vm"
    assert ocr_gate.check(ci, baseline) == []
    # Without a baseline for the key there is no silent pass.
    other = _report()
    other["key"] = "apple-vision/text-r3/macos28/hardware"
    assert ocr_gate.check(other, baseline)


def test_umschaltkriterium() -> None:
    vision = _report()
    tesseract = _report(pii_word_recall=0.72, pii_box_coverage=0.27, readback_rate=0.85)
    tesseract["pages"] = [{"page": "p00", "overreach": [3]}]
    assert ocr_gate.compare(vision, tesseract) == []
    assert ocr_gate.compare(_report(readback_rate=0.97), tesseract)
    assert ocr_gate.compare(_report(pii_word_recall=0.70), tesseract)
    worse = _report()
    worse["pages"] = [{"page": "p00", "overreach": [3, 4]}]
    assert ocr_gate.compare(worse, tesseract) == [
        "Vision greift 1-mal in die Nachbarzeile, Tesseract nicht."
    ]


def test_baseline_key_names_request_revision_and_macos_major_version() -> None:
    engine = {"name": "apple-vision", "request": "text", "revision": 3, "os_version": "27.2"}
    assert ocr_gate.baseline_key(engine, "ci-vm") == "apple-vision/text-r3/macos27/ci-vm"
    assert ocr_gate.baseline_key({"name": "tesseract", "version": "5.5.2"}, "hardware") == (
        "tesseract/5.5.2/hardware"
    )


def test_committed_baselines_match_corpus() -> None:
    for engine in ("apple-vision", "tesseract"):
        baseline = ocr_gate._read(ocr_gate.baseline_path(engine))
        assert baseline["schema"] == 1
        for key, entry in baseline["baselines"].items():
            assert key.startswith(engine)
            assert entry["corpus_version"] == ocr_corpus.VERSION
            assert entry["language_correction"] is False
