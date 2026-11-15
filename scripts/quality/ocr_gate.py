"""OCR quality gate per engine.

Measures OCR on the synthetic corpus (``ocr_corpus.py``) and blocks any regression
against its own baseline. Reports contain only metrics and page IDs.

Usage (from repository root)::

    uv run --project sidecar python -m scripts.quality.ocr_gate run \\
        --engine apple-vision --output acceptance/ocr-mac.json
    uv run --project sidecar python -m scripts.quality.ocr_gate check \\
        --report acceptance/ocr-mac.json
    uv run --project sidecar python -m scripts.quality.ocr_gate compare \\
        --vision vision.json --tesseract tesseract.json

Metrics: PII-word recall (critical) · word recall · character error rate
(word-aligned) · box coverage of PII words (fraction with ≥ 0.95
of the glyph box under the OCR box) · overreach into neighboring line ·
code read-back rate for 6–12 pt · time per page.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import ocr_corpus

REPORT_SCHEMA = 1
BASELINE_DIR = ocr_corpus.REPO / "testdata" / "quality"
#: Minimum fraction of the glyph box under the OCR box for a PII word to count as covered.
COVERAGE_MIN = 0.95
#: Switch criterion (U4): minimum read-back rate Vision must achieve.
READBACK_MIN = 0.98
#: Tolerances per profile: real hardware blocks any regression, CI VMs get slack
#: because they lack a Neural Engine and each run is cold.
TOLERANCES = {
    "hardware": {"rate": 0.0, "cer": 0.0, "overreach": 0},
    "ci-vm": {"rate": 0.02, "cer": 0.01, "overreach": 2},
}
_EDGE_PUNCTUATION = ".,;:!?()[]\"'„“”‚‘’«»–-·•"


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip(_EDGE_PUNCTUATION)


def levenshtein(first: str, second: str) -> int:
    previous = list(range(len(second) + 1))
    for index, char in enumerate(first, start=1):
        current = [index]
        for other_index, other in enumerate(second, start=1):
            current.append(
                min(
                    previous[other_index] + 1,
                    current[other_index - 1] + 1,
                    previous[other_index - 1] + (char != other),
                )
            )
        previous = current
    return previous[-1]


def _intersection(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int]:
    return (
        max(0, min(a[2], b[2]) - max(a[0], b[0])),
        max(0, min(a[3], b[3]) - max(a[1], b[1])),
    )


def _area(box: tuple[int, int, int, int]) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


@dataclass(slots=True)
class PageResult:
    page: str
    words: int
    words_found: int
    pii: int
    pii_found: int
    pii_covered: int
    chars: int
    char_errors: int
    #: Words whose glyphs extend into an OCR box of another line.
    overreach: list[int]
    seconds: float


def evaluate_page(page: ocr_corpus.Page, ocr_words: tuple[Any, ...], seconds: float) -> PageResult:
    boxes = [
        (word, (word.left, word.top, word.left + word.width, word.top + word.height))
        for word in ocr_words
    ]
    truth = [word for word in page.words if normalize(word.text)]
    found = covered = pii = pii_found = chars = errors = 0
    #: Mapping from OCR box index to the truth line with the best overlap.
    line_of: dict[int, int] = {}
    for word in truth:
        wanted = normalize(word.text)
        candidates = []
        for index, (ocr, box) in enumerate(boxes):
            width, height = _intersection(word.box, box)
            if width and height:
                candidates.append((width * height, index, ocr, box))
        candidates.sort(key=lambda item: item[0], reverse=True)
        if candidates:
            overlap, index, _ocr, _box = candidates[0]
            previous = line_of.get(index)
            if previous is None or overlap > previous:
                line_of[index] = word.line
        match = next((item for item in candidates if normalize(item[2].text) == wanted), None)
        chars += len(wanted)
        if match is not None:
            found += 1
        else:
            best = normalize(candidates[0][2].text) if candidates else ""
            errors += min(len(wanted), levenshtein(wanted, best)) if best else len(wanted)
        if word.pii:
            pii += 1
            if match is not None:
                pii_found += 1
                width, height = _intersection(word.box, match[3])
                if _area(word.box) and width * height / _area(word.box) >= COVERAGE_MIN:
                    covered += 1
    overreach: list[int] = []
    if page.upright:
        # Only when the page is upright: the neighboring line is unambiguous.
        for truth_index, word in enumerate(page.words):
            for index, (_ocr, box) in enumerate(boxes):
                own = line_of.get(index)
                if own is None or own == word.line:
                    continue
                width, height = _intersection(word.box, box)
                if width >= 2 and height >= 2:
                    overreach.append(truth_index)
                    break
    return PageResult(
        page=page.id,
        words=len(truth),
        words_found=found,
        pii=pii,
        pii_found=pii_found,
        pii_covered=covered,
        chars=chars,
        char_errors=errors,
        overreach=overreach,
        seconds=round(seconds, 3),
    )


def readback(sheet: ocr_corpus.CodeSheet, backend: Any, layout: str) -> int:
    """Same as export: code sheet (Vision) or page with white background (Tesseract)."""

    sys.path.insert(0, str(ocr_corpus.REPO / "sidecar" / "src"))
    from PIL import Image

    from presidio_sidecar.exporting import _read_code_sheet, _scan_readback_matches

    pad = 4
    boxes = [
        (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad) for _value, box in sheet.codes
    ]
    if layout == "sheet":
        read = _read_code_sheet(
            [sheet.image.crop(box) for box in boxes], backend=backend, languages=("deu", "eng")
        )
    else:
        only = Image.new("RGB", sheet.image.size, (255, 255, 255))
        for box in boxes:
            only.paste(sheet.image.crop(box), box[:2])
        words = backend.recognize(only, languages=("deu", "eng"))
        read = []
        for box in boxes:
            hits = [
                word.text
                for word in words
                if all(
                    _intersection(
                        box, (word.left, word.top, word.left + word.width, word.top + word.height)
                    )
                )
            ]
            read.append(" ".join(hits))
    return sum(
        _scan_readback_matches(text, value)
        for text, (value, _box) in zip(read, sheet.codes, strict=True)
    )


def _backend(engine: str, correction: bool) -> tuple[Any, dict[str, Any], str]:
    sys.path.insert(0, str(ocr_corpus.REPO / "sidecar" / "src"))
    from presidio_sidecar.extraction.ocr import TesseractOcrBackend, readback_layout_of

    if engine == "apple-vision":
        from presidio_sidecar.extraction.ocr_vision import VisionOcrBackend, vision_helper_info

        info = vision_helper_info()
        backend: Any = VisionOcrBackend(language_correction=correction)
        description = info.engine().as_dict()
    elif engine == "tesseract":
        backend = TesseractOcrBackend()
        description = {"name": "tesseract", "version": None, "readback_layout": "page"}
    else:
        raise SystemExit(f"Unbekannte Engine: {engine}")
    return backend, description, readback_layout_of(backend)


def baseline_key(engine: dict[str, Any], profile: str) -> str:
    if engine["name"] == "apple-vision":
        major = str(engine.get("os_version") or "0").split(".")[0]
        return f"apple-vision/{engine['request']}-r{engine['revision']}/macos{major}/{profile}"
    return f"tesseract/{engine.get('version') or 'unbekannt'}/{profile}"


def run(engine: str, *, correction: bool, profile: str) -> dict[str, Any]:
    backend, description, layout = _backend(engine, correction)
    results: list[PageResult] = []
    for page in ocr_corpus.pages():
        started = time.perf_counter()
        words = tuple(backend.recognize(page.image, languages=("deu", "eng")))
        results.append(evaluate_page(page, words, time.perf_counter() - started))
    codes = read = 0
    for sheet in ocr_corpus.code_sheets():
        codes += len(sheet.codes)
        read += readback(sheet, backend, layout)
    if engine == "tesseract":
        description["version"] = getattr(backend.engine, "version", None)
    total = {
        name: sum(getattr(item, name) for item in results)
        for name in (
            "words",
            "words_found",
            "pii",
            "pii_found",
            "pii_covered",
            "chars",
            "char_errors",
        )
    }
    # The first page includes compilation overhead on a cold Vision start.
    timings = [item.seconds for item in results[1:]] or [0.0]
    metrics = {
        "pii_word_recall": round(total["pii_found"] / total["pii"], 4),
        "word_recall": round(total["words_found"] / total["words"], 4),
        "cer": round(total["char_errors"] / total["chars"], 4),
        "pii_box_coverage": round(total["pii_covered"] / max(1, total["pii_found"]), 4),
        "overreach": sum(len(item.overreach) for item in results),
        "readback_rate": round(read / codes, 4),
        "seconds_per_page_median": round(statistics.median(timings), 3),
    }
    return {
        "schema": REPORT_SCHEMA,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "engine": description,
        "language_correction": correction,
        "profile": profile,
        "key": baseline_key(description, profile),
        "host": _host(),
        "corpus": {"version": ocr_corpus.VERSION, "pages": len(results), "codes": codes},
        "metrics": metrics,
        "pages": [
            {
                "page": item.page,
                "pii_found": item.pii_found,
                "pii": item.pii,
                "words_found": item.words_found,
                "words": item.words,
                "overreach": item.overreach,
                "seconds": item.seconds,
            }
            for item in results
        ],
    }


def _host() -> dict[str, Any]:
    """Device used for measurement – real hardware counts for acceptance."""

    host: dict[str, Any] = {"machine": platform.machine(), "system": platform.system()}
    if sys.platform == "darwin":
        import subprocess

        try:
            chip, memory = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string", "hw.memsize"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            ).stdout.split("\n")[:2]
            host |= {"chip": chip.strip(), "memory_gib": round(int(memory) / 1024**3)}
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        host["os"] = platform.mac_ver()[0]
    return host


def baseline_path(engine: str) -> Path:
    return BASELINE_DIR / f"baseline.ocr.{engine}.json"


def check(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Check regressions against the stored baseline; empty means pass."""

    entry = baseline.get("baselines", {}).get(report["key"])
    if entry is None:
        return [f"Keine Baseline fuer {report['key']}; neue Baseline nur per Commit."]
    if entry.get("corpus_version") != report["corpus"]["version"]:
        return ["Die Baseline gehoert zu einer anderen Korpusversion."]
    tolerance = TOLERANCES[report["profile"]]
    old = entry["metrics"]
    new = report["metrics"]
    failures = []
    for name in ("pii_word_recall", "word_recall", "pii_box_coverage", "readback_rate"):
        if new[name] < old[name] - tolerance["rate"]:
            failures.append(f"{name}: {new[name]} < Baseline {old[name]}")
    if new["cer"] > old["cer"] + tolerance["cer"]:
        failures.append(f"cer: {new['cer']} > Baseline {old['cer']}")
    if new["overreach"] > old["overreach"] + tolerance["overreach"]:
        failures.append(f"overreach: {new['overreach']} > Baseline {old['overreach']}")
    return failures


def compare(vision: dict[str, Any], tesseract: dict[str, Any]) -> list[str]:
    """Switch criterion (U4); empty means satisfied."""

    failures = []
    v, t = vision["metrics"], tesseract["metrics"]
    for name in ("pii_word_recall", "pii_box_coverage"):
        if v[name] < t[name]:
            failures.append(f"{name}: Vision {v[name]} < Tesseract {t[name]}")
    if v["readback_rate"] < READBACK_MIN:
        failures.append(f"readback_rate: Vision {v['readback_rate']} < {READBACK_MIN}")
    tesseract_overreach = {
        (page["page"], index) for page in tesseract["pages"] for index in page["overreach"]
    }
    extra = sorted(
        (page["page"], index)
        for page in vision["pages"]
        for index in page["overreach"]
        if (page["page"], index) not in tesseract_overreach
    )
    if extra:
        failures.append(f"Vision greift {len(extra)}-mal in die Nachbarzeile, Tesseract nicht.")
    return failures


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OCR-Qualitaets-Gate (Phase U)")
    commands = parser.add_subparsers(dest="command", required=True)
    measure = commands.add_parser("run", help="Engine auf dem Korpus messen")
    measure.add_argument("--engine", choices=("apple-vision", "tesseract"), required=True)
    measure.add_argument("--correction", choices=("off", "on"), default="off")
    measure.add_argument("--profile", choices=tuple(TOLERANCES), default="hardware")
    measure.add_argument("--output", type=Path, required=True)
    measure.add_argument(
        "--update-baseline",
        action="store_true",
        help="Ergebnis als Baseline seines Schluessels schreiben (danach committen)",
    )
    gate = commands.add_parser("check", help="Bericht gegen die eigene Baseline pruefen")
    gate.add_argument("--report", type=Path, required=True)
    gate.add_argument("--baseline", type=Path)
    gate.add_argument(
        "--allow-missing-baseline",
        action="store_true",
        help="Ohne Baseline fuer den Schluessel nur warnen (erster Lauf auf einem neuen Runner)",
    )
    versus = commands.add_parser("compare", help="Umschaltkriterium Vision gegen Tesseract")
    versus.add_argument("--vision", type=Path, required=True)
    versus.add_argument("--tesseract", type=Path, required=True)
    versus.add_argument(
        "--correction-on",
        type=Path,
        help="Vision-Bericht mit Sprachkorrektur (E-U2) fuer den Abnahmebericht",
    )
    versus.add_argument("--output", type=Path, help="Abnahmebericht, z. B. acceptance/ocr-mac.json")
    args = parser.parse_args(argv)

    if args.command == "run":
        report = run(args.engine, correction=args.correction == "on", profile=args.profile)
        _write(args.output, report)
        print(json.dumps({"key": report["key"], **report["metrics"]}, indent=2))
        if args.update_baseline:
            path = baseline_path(args.engine)
            baseline = _read(path) if path.is_file() else {"schema": 1, "baselines": {}}
            baseline["baselines"][report["key"]] = {
                "corpus_version": report["corpus"]["version"],
                "measured_at": report["created_at"],
                "host": report["host"],
                "language_correction": report["language_correction"],
                "metrics": report["metrics"],
            }
            baseline["baselines"] = dict(sorted(baseline["baselines"].items()))
            _write(path, baseline)
            print(f"Baseline geschrieben: {path}")
        return 0
    if args.command == "check":
        report = _read(args.report)
        path = args.baseline or baseline_path(report["engine"]["name"])
        baseline = _read(path)
        if args.allow_missing_baseline and report["key"] not in baseline.get("baselines", {}):
            print(
                f"::warning::Keine OCR-Baseline fuer {report['key']}. Den Bericht pruefen und "
                "als Baseline committen (run --update-baseline).",
            )
            return 0
        failures = check(report, baseline)
        for failure in failures:
            print(f"OCR-Gate: {failure}", file=sys.stderr)
        if not failures:
            print(f"OCR-Gate bestanden: {report['key']}")
        return 1 if failures else 0
    vision, tesseract = _read(args.vision), _read(args.tesseract)
    failures = compare(vision, tesseract)
    for failure in failures:
        print(f"Umschaltkriterium: {failure}", file=sys.stderr)
    if not failures:
        print("Umschaltkriterium erfuellt: Vision mindestens so gut wie Tesseract.")
    if args.output is not None:
        summary: dict[str, Any] = {
            "schema": REPORT_SCHEMA,
            "tool": "scripts/quality/ocr_gate.py",
            "measured_at": vision["created_at"],
            "device": vision["host"],
            "corpus": vision["corpus"],
            "switch_criterion": {"met": not failures, "findings": failures},
            "engines": {
                vision["key"]: {"engine": vision["engine"], "metrics": vision["metrics"]},
                tesseract["key"]: {"engine": tesseract["engine"], "metrics": tesseract["metrics"]},
            },
        }
        if args.correction_on is not None:
            corrected = _read(args.correction_on)
            summary["language_correction_on"] = corrected["metrics"]
        _write(args.output, summary)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
