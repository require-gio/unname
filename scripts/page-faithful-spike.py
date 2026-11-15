"""Spike S0: Measure office engines against Word.

For each DOCX in a corpus the result DOCX with codes is created first –
the same path as when saving (``docx_result_bytes``). Each engine then
produces a PDF; it is compared against the reference ``<name>.word.pdf``
(Word-PDF export of the same result DOCX, see ``--write-references``):

- **SSIM** per page, rendered with PyMuPDF at 150 DPI (as in the Oxi method),
  averaged over pages; missing pages count as 0.
- **Page count** must match the reference.
- **Code placement**: proportion of codes that ``paged_layout`` finds in the
  text layer (target 100 %).
- **Time** per conversion and **peak memory** of the engine (``wait4``),
  plus the size of the engine file.

Decision rule (recommendation from spec): an engine passes with at least
85 % page-count matches, average SSIM over 0.85, and 100 % code placement.

Codes: without detection models the script replaces every seventh word of
four or more letters with a code ``<<PERSON_…>>``, deterministic so all
engines produce the same file. The codes are longer than the words; this
shifts line breaks like in the real result.

Invocation from the repository:
    uv run --project sidecar python scripts/page-faithful-spike.py KORPUS \\
        --engine office2pdf=office-engine/target/release/unname-office-engine \\
        --engine docxide=office-engine/target-docxide/release/unname-office-engine \\
        [--libreoffice /Applications/LibreOffice.app/Contents/MacOS/soffice] \\
        [--report folder] [--write-references folder]

``--write-references`` stores the result DOCX so it can be exported to
PDF with Word on a machine (``<name>.word.pdf`` placed alongside the
original DOCX in the corpus).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "sidecar" / "src"))

#: Decision rule (recommendation from spec).
MIN_PAGE_HITS = 0.85
MIN_SSIM = 0.85
MIN_CODES = 1.0
DPI = 150
#: Every seventh word of four or more letters becomes a code.
CODE_INTERVAL = 7


@dataclass
class Measurement:
    file: str
    engine: str
    ok: bool
    sekunden: float = 0.0
    memory_mb: float = 0.0
    pages: int = 0
    reference_pages: int | None = None
    ssim: float | None = None
    codes: int = 0
    codes_found: int = 0
    error_msg_spike: str = ""


@dataclass
class Engine:
    name: str
    command: list[str]
    file: Path | None
    art: str = "unname"
    measurements: list[Measurement] = field(default_factory=list)


def result_with_codes(document_obj_spike: Any) -> Any:
    """Small-scale result: replace every seventh word of four or more letters with a code."""

    from presidio_sidecar.layout import docx_unit_segments
    from presidio_sidecar.processing import (
        AppliedFinding,
        FindingKind,
        ProcessingMode,
        Reversibility,
    )

    text = document_obj_spike.text
    places: list[tuple[int, int]] = []
    counter = 0
    for segment in docx_unit_segments(document_obj_spike):
        for hit in re.finditer(r"[^\W\d_]{4,}", text[segment.start : segment.end]):
            counter += 1
            if counter % CODE_INTERVAL == 0:
                places.append((segment.start + hit.start(), segment.start + hit.end()))
    output, cursor, angewandt = "", 0, []
    for number, (start, segment_end) in enumerate(places, 1):
        output += text[cursor:start]
        segment_start = len(output)
        output += f"<<PERSON_{number:08x}>>"
        cursor = segment_end
        angewandt.append(
            AppliedFinding(
                finding_id=f"s{number}",
                kind=FindingKind.CONFIRMED,
                entity_type="PERSON",
                source="spike",
                score=1.0,
                mode=ProcessingMode.TOKEN_DETERMINISTIC,
                reversibility=Reversibility.VAULT,
                original_start=_utf16(text, start),
                original_end=_utf16(text, segment_end),
                output_start=_utf16(output, segment_start),
                output_end=_utf16(output, len(output)),
            )
        )
    output += text[cursor:]
    return SimpleNamespace(text=output, applied_findings=tuple(angewandt))


def _utf16(text: str, offset: int) -> int:
    return offset + sum(1 for char in text[:offset] if ord(char) > 0xFFFF)


def umwandeln(engine: Engine, docx: bytes) -> tuple[bytes, float, float]:
    """PDF size, duration in seconds, and peak memory (MB) of a conversion."""

    with tempfile.TemporaryDirectory() as folder:
        input_path = Path(folder) / "ergebnis.docx"
        input_path.write_bytes(docx)
        output = Path(folder) / "ergebnis.pdf"
        if engine.art == "libreoffice":
            profile = Path(folder) / "profil"
            command = [
                *engine.command,
                f"-env:UserInstallation={profile.as_uri()}",
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                folder,
                str(input_path),
            ]
            stdin, stdout = subprocess.DEVNULL, subprocess.DEVNULL
        else:
            command = [*engine.command, "docx-to-pdf"]
            stdin, stdout = input_path.open("rb"), output.open("wb")
        beginn = time.perf_counter()
        prozess = subprocess.Popen(
            command, stdin=stdin, stdout=stdout, stderr=subprocess.DEVNULL
        )
        _, status, usage = os.wait4(prozess.pid, 0)
        duration = time.perf_counter() - beginn
        prozess.returncode = os.waitstatus_to_exitcode(status)
        for file in (stdin, stdout):
            if hasattr(file, "close"):
                file.close()  # type: ignore[union-attr]
        if prozess.returncode != 0 or not output.is_file():
            raise RuntimeError(f"Exit-Code {prozess.returncode}")
        # macOS reports bytes, Linux kilobytes.
        memory = usage.ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
        return output.read_bytes(), duration, memory


def page_images(pdf: bytes) -> list[Any]:
    import fitz
    import numpy as np

    document_obj_spike = fitz.open(stream=pdf, filetype="pdf")
    images = []
    for page in document_obj_spike:
        pixmap = page.get_pixmap(dpi=DPI, colorspace=fitz.csGRAY, alpha=False)
        images.append(
            np.frombuffer(pixmap.samples, dtype=np.uint8)
            .reshape(pixmap.height, pixmap.width)
            .astype(np.float64)
        )
    return images


def ssim(a: Any, b: Any) -> float:
    """SSIM (Wang et al. 2004) with 11-pixel Gaussian window, σ=1.5; resize b to size of a."""

    import numpy as np
    from PIL import Image

    if a.shape != b.shape:
        b = np.asarray(
            Image.fromarray(b.astype(np.uint8)).resize((a.shape[1], a.shape[0])),
            dtype=np.float64,
        )
    kern = np.exp(-((np.arange(11) - 5) ** 2) / (2 * 1.5**2))
    kern /= kern.sum()

    def glatt(image: Any) -> Any:
        image = np.apply_along_axis(
            lambda line: np.convolve(line, kern, mode="valid"), 1, image
        )
        return np.apply_along_axis(
            lambda column: np.convolve(column, kern, mode="valid"), 0, image
        )

    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = glatt(a), glatt(b)
    var_a = glatt(a * a) - mu_a**2
    var_b = glatt(b * b) - mu_b**2
    kov = glatt(a * b) - mu_a * mu_b
    layout_map_spike = ((2 * mu_a * mu_b + c1) * (2 * kov + c2)) / (
        (mu_a**2 + mu_b**2 + c1) * (var_a + var_b + c2)
    )
    return float(layout_map_spike.mean())


def measure(file: Path, engines: list[Engine], references: Path | None) -> None:
    from presidio_sidecar.document_workflow import docx_result_bytes
    from presidio_sidecar.extraction import DocumentExtractor
    from presidio_sidecar.paged_docx import paged_layout, side_units

    document_obj_spike = DocumentExtractor().extract_bytes(
        file.read_bytes(), filename=file.name
    )
    result = result_with_codes(document_obj_spike)
    docx = docx_result_bytes(
        file,
        document=document_obj_spike,
        source_text=document_obj_spike.text,
        processing=result,
    )
    if references is not None:
        references.mkdir(parents=True, exist_ok=True)
        (references / f"{file.stem}.ergebnis.docx").write_bytes(docx)
    units = side_units(document_obj_spike, document_obj_spike.text, "result", result)
    reference_pdf = file.with_name(f"{file.stem}.word.pdf")
    reference = (
        page_images(reference_pdf.read_bytes()) if reference_pdf.is_file() else None
    )

    for engine in engines:
        measurement = Measurement(file=file.name, engine=engine.name, ok=False)
        measurement.codes = len(result.applied_findings)
        try:
            pdf, measurement.sekunden, measurement.memory_mb = umwandeln(engine, docx)
        except (OSError, RuntimeError) as error_msg_spike:
            measurement.error_msg_spike = str(error_msg_spike)
            engine.measurements.append(measurement)
            continue
        measurement.ok = True
        images = page_images(pdf)
        measurement.pages = len(images)
        layout_map_spike = paged_layout(
            pdf, units, document_obj_spike.text, max_image_pixels=10**9
        )
        measurement.codes_found = sum(
            1
            for place in result.applied_findings
            if any(
                word[5] < place.original_end and word[6] > place.original_start
                for word in layout_map_spike["words"]
            )
        )
        if reference is not None:
            measurement.reference_pages = len(reference)
            values = [
                ssim(reference[i], images[i]) if i < len(images) else 0.0
                for i in range(len(reference))
            ]
            measurement.ssim = sum(values) / len(values) if values else None
        engine.measurements.append(measurement)
        print(
            f"{file.name} · {engine.name}: {measurement.pages} S., "
            f"{measurement.codes_found}/{measurement.codes} Codes, {measurement.sekunden:.2f} s"
            + (f", SSIM {measurement.ssim:.3f}" if measurement.ssim is not None else "")
        )


def zusammenfassen(engine: Engine) -> dict[str, Any]:
    successful = [m for m in engine.measurements if m.ok]
    with_reference = [m for m in successful if m.reference_pages is not None]
    codes = sum(m.codes for m in successful)
    summary: dict[str, Any] = {
        "engine": engine.name,
        "groesse_mb": round(engine.file.stat().st_size / 1e6, 1)
        if engine.file and engine.file.is_file()
        else None,
        "dokumente": len(engine.measurements),
        "fehlgeschlagen": len(engine.measurements) - len(successful),
        "codes_gefunden": (sum(m.codes_found for m in successful) / codes)
        if codes
        else None,
        "sekunden_median": sorted(m.sekunden for m in successful)[len(successful) // 2]
        if successful
        else None,
        "speicher_mb_max": max((m.memory_mb for m in successful), default=None),
        "mit_referenz": len(with_reference),
        "seiten_treffer": (
            sum(m.pages == m.reference_pages for m in with_reference)
            / len(with_reference)
            if with_reference
            else None
        ),
        "ssim_mittel": (
            sum(m.ssim or 0.0 for m in with_reference) / len(with_reference)
            if with_reference
            else None
        ),
    }
    if with_reference and len(with_reference) == len(engine.measurements):
        summary["besteht"] = (
            summary["seiten_treffer"] >= MIN_PAGE_HITS
            and summary["ssim_mittel"] > MIN_SSIM
            and (summary["codes_gefunden"] or 0) >= MIN_CODES
        )
    else:
        # No judgment without a Word reference for each document.
        summary["besteht"] = None
    return summary


def report_text(summaries: list[dict[str, Any]]) -> str:
    def format_number(val: Any, format_: str) -> str:
        return "–" if val is None else format(val, format_)

    lines = [
        (
            "| Engine | Größe | Dokumente | Fehler | Codes verortet | Median s | Spitze MB "
            "| mit Referenz | Seitenzahl = Word | SSIM | besteht |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for z in summaries:
        judgment = {True: "ja", False: "nein", None: "offen (Referenz fehlt)"}[
            z["besteht"]
        ]
        lines.append(
            f"| {z['engine']} | {format_number(z['groesse_mb'], '.1f')} MB | {z['dokumente']} "
            f"| {z['fehlgeschlagen']} | {format_number(z['codes_gefunden'], '.0%')} "
            f"| {format_number(z['sekunden_median'], '.2f')} | {format_number(z['speicher_mb_max'], '.0f')} "
            f"| {z['mit_referenz']} | {format_number(z['seiten_treffer'], '.0%')} "
            f"| {format_number(z['ssim_mittel'], '.3f')} | {judgment} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "corpus", type=Path, help="Ordner mit DOCX (und <name>.word.pdf)"
    )
    parser.add_argument(
        "--engine",
        action="append",
        default=[],
        metavar="NAME=PFAD",
        help="unname-office-engine (mehrfach)",
    )
    parser.add_argument("--libreoffice", type=Path, help="soffice als Referenz-Engine")
    parser.add_argument(
        "--report", type=Path, help="Ordner fuer spike.json und spike.md"
    )
    parser.add_argument(
        "--write-references",
        type=Path,
        help="Ergebnis-DOCX ablegen, um sie in Word als PDF zu exportieren",
    )
    argumente = parser.parse_args()

    engines: list[Engine] = []
    for entry in argumente.engine:
        name, _, file_path_spike = entry.partition("=")
        file = Path(file_path_spike).resolve()
        engines.append(Engine(name=name, command=[str(file)], file=file))
    if argumente.libreoffice:
        engines.append(
            Engine(
                name="libreoffice",
                command=[str(argumente.libreoffice)],
                file=None,
                art="libreoffice",
            )
        )
    if not engines:
        parser.error("Mindestens eine Engine angeben (--engine oder --libreoffice).")
    files = sorted(
        p
        for p in argumente.corpus.glob("*.docx")
        if not p.name.endswith(".ergebnis.docx")
    )
    if not files:
        parser.error(f"Keine DOCX in {argumente.corpus}.")

    for file in files:
        try:
            measure(file, engines, argumente.write_references)
        except Exception as error_msg_spike:  # noqa: BLE001 – a broken document does not stop the spike
            print(f"{file.name}: uebersprungen ({type(error_msg_spike).__name__})")

    summaries = [zusammenfassen(engine) for engine in engines]
    table = report_text(summaries)
    print()
    print(table)
    if argumente.report:
        argumente.report.mkdir(parents=True, exist_ok=True)
        (argumente.report / "spike.json").write_text(
            json.dumps(
                {
                    "regel": {
                        "seiten_treffer": MIN_PAGE_HITS,
                        "ssim": MIN_SSIM,
                        "codes": MIN_CODES,
                        "dpi": DPI,
                    },
                    "engines": summaries,
                    "messungen": [asdict(m) for e in engines for m in e.measurements],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (argumente.report / "spike.md").write_text(table, encoding="utf-8")
    if shutil.which("soffice") is None and argumente.libreoffice is None:
        print("Hinweis: ohne --libreoffice fehlt die LibreOffice-Referenz.")


if __name__ == "__main__":
    main()
