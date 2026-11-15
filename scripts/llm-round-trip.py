#!/usr/bin/env python3
"""LLM round-trip test for code formatting.

Question: Do the codes survive a pass through a language model – and which format works best? Each
task is a typical prompt for an LLM (rephrase, answer, translate, table, summarize) with a text
where names are replaced by codes. Metrics per format and model, under their keys in the report:

* ``exakt`` (exact): the code appears unchanged in the response,
* ``erkannt`` (found): the sidecar's tolerant matching (``restore.candidates``) finds it again;
  this is what the app restores,
* ``kern`` (core): ``TYP_checkvalue`` appears somewhere in the response (upper bound for any
  tolerant match of this format),
* ``beschaedigt`` (corrupted): something that looks like a code but matches no sent code
  (different check value or type); the app reports it as unknown,
* ``verloren`` (lost): the code is missing entirely (allowed when summarizing, not counted there).

The texts are synthetic and contain no personal data, only codes.
Only local models served via Ollama on 127.0.0.1 are used; models with ``:cloud`` in the name are
rejected because they would forward requests to the internet.

Run from the repository root:

    uv run --project sidecar python scripts/llm-round-trip.py \\
        --models qwen3:4b-instruct-2507-q4_K_M gpt-oss:20b \\
        --repeats 2 --output acceptance/llm-round-trip.json \\
        --raw-data /tmp/rundreise-antworten.json

With ``--scan`` the test processes a scanned PDF (layout example). The scan of the delivery-note
replica from ``scripts/layout-corpus.py`` is reversibly masked – pixels removed, code as text,
invisible text layer – and the text layer of the saved PDF is fed to the model. The test checks
whether restoration finds every code loss-free and whether the model reproduces every code
completely. OCR is provided by the mock ``SehendeOcr`` corpus (no Tesseract needed).

    uv run --project sidecar python scripts/llm-round-trip.py --scan \\
        --models qwen3:4b-instruct-2507-q4_K_M --output acceptance/llm-round-trip-scan.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import re
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from presidio_sidecar.restore import candidates

OLLAMA = "http://127.0.0.1:11434/api/chat"

FORMATS = {
    "double_angle": "<<{typ}_{check}>>",
    "square": "[{typ}_{check}]",
    "curly": "{{{{{typ}_{check}}}}}",
    "bare": "{typ}_{check}",
}

#: task → (prompt, template with placeholders, must all codes reappear?)
TASKS: dict[str, tuple[str, str, bool]] = {
    "rephrase": (
        "Bitte formuliere diese E-Mail freundlicher und etwas kürzer:",
        (
            "Sehr geehrte Frau {P1},\n\nIhre Rechnung zur Kundennummer {K1} ist seit drei Wochen "
            "offen. Überweisen Sie den Betrag bis Freitag auf das Konto {I1}, sonst übergeben wir "
            "die Sache an die {O1}. Rückfragen bitte an {P2}.\n\nGruß\n{P3}"
        ),
        True,
    ),
    "answer": (
        (
            "Schreibe eine kurze Antwort auf diese Nachricht. Bestätige den Termin und nenne "
            "Ansprechpartnerin und Telefonnummer noch einmal."
        ),
        (
            "Hallo, hier ist {P1} von der {O1}. Können wir den Termin am Dienstag in {L1} "
            "bestätigen? Ansprechpartnerin vor Ort ist {P2}, erreichbar unter {T1}.\n\n"
            "Viele Grüße\n{P1}"
        ),
        True,
    ),
    "translate": (
        "Übersetze ins Englische:",
        (
            "Sehr geehrter Herr {P1},\n\nanbei der Mietvertrag für die Wohnung in {L1}. Die "
            "Kaution überweisen Sie bitte auf {I1}. Für Fragen steht Ihnen {P2} zur Verfügung.\n\n"
            "Mit freundlichen Grüßen\n{P3}\n{O1}"
        ),
        True,
    ),
    "table": (
        (
            "Erstelle aus diesen Notizen eine Markdown-Tabelle mit den Spalten Name, Firma und "
            "Kundennummer:"
        ),
        (
            "- {P1} arbeitet bei {O1}, Kundennummer {K1}\n- {P2} ({O2}) hat die Kundennummer {K2}\n"
            "- {P3} ist ebenfalls bei {O1}, Kundennummer {K3}"
        ),
        True,
    ),
    "summarize": (
        "Fasse diese Nachricht in zwei Sätzen zusammen:",
        (
            "Liebe {P1},\n\nwie besprochen hat {P2} von der {O1} die Unterlagen geprüft. Die "
            "Zahlung über die Kundennummer {K1} ist eingegangen, der Termin in {L1} bleibt. "
            "Bitte melde dich bei {P3} unter {T1}, falls sich etwas ändert.\n\nBeste Grüße\n{P2}"
        ),
        False,
    ),
}

TYPES = {
    "P": "PERSON",
    "O": "ORGANIZATION",
    "K": "CUSTOMER_NUMBER",
    "I": "IBAN_CODE",
    "L": "LOCATION",
    "T": "PHONE_NUMBER",
}

_KERN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z_]*?)_([0-9A-Fa-f]{4,16})(?![A-Za-z0-9])")


@dataclass
class Run:
    model: str
    format: str
    task: str
    repeat: int
    codes: dict[str, str]
    response: str
    sekunden: float


def codes_for(text: str, zufall: random.Random) -> dict[str, tuple[str, str]]:
    """Placeholder → (type, check value), fresh each run."""

    found = sorted(set(re.findall(r"\{([A-Z]\d)\}", text)))
    return {name: (TYPES[name[0]], f"{zufall.getrandbits(32):08x}") for name in found}


def question(model: str, content: str, temperature_val: float) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
            "think": False,
            "options": {"temperature": temperature_val},
            "keep_alive": "2m",
        }
    ).encode()
    http_request = urllib.request.Request(
        OLLAMA, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(http_request, timeout=600) as response:
        data_json = json.load(response)
    return str(data_json.get("message", {}).get("content", ""))


def evaluate(run: Run, all_must: bool) -> dict[str, int]:
    gesendet = {f"{typ}_{check}".upper(): code for code, (typ, check) in run.codes.items()}
    response = run.response
    core_response = {f"{m.group(1)}_{m.group(2)}".upper() for m in _KERN.finditer(response)}
    recognized = {item.token[2:-2].upper() for item in candidates(response)}
    result = {"sent": 0, "exact": 0, "found": 0, "core": 0, "lost": 0}
    for kern, code in gesendet.items():
        if all_must:
            result["sent"] += 1
            result["exact"] += int(code in response)
            result["found"] += int(kern in recognized)
            result["core"] += int(kern in core_response)
            result["lost"] += int(kern not in core_response)
    # damaged: looks like one of our types but matches no code.
    known_types = {typ for typ, _ in run.codes.values()}
    result["corrupted"] = sum(
        1
        for m in _KERN.finditer(response)
        if m.group(1).upper() in known_types
        and f"{m.group(1)}_{m.group(2)}".upper() not in gesendet
    )
    return result


#: tasks for the AI using the text of a stored scan; all codes must reappear?
SCAN_TASKS: dict[str, tuple[str, bool]] = {
    "answer": (
        (
            "Schreibe eine kurze, freundliche Antwort an den Kunden aus diesem Lieferschein. "
            "Sprich ihn mit Namen an, bestätige die Lieferanschrift und nenne Telefon und "
            "E-Mail des Shops für Rückfragen."
        ),
        True,
    ),
    "table": (
        (
            "Erstelle aus diesem Lieferschein eine Markdown-Tabelle mit den Zeilen Kunde, "
            "Anschrift, Telefon des Shops und E-Mail des Shops."
        ),
        True,
    ),
    "summarize": ("Fasse diesen Lieferschein in zwei Sätzen zusammen.", False),
}


def _corpus() -> object:
    path = Path(__file__).resolve().parent / "layout-corpus.py"
    spec = importlib.util.spec_from_file_location("layout_corpus", path)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def mask_scan(zufall: random.Random) -> tuple[str, dict[str, tuple[str, str]], int]:
    """Mask the scan of the replica reversibly; returns text layer and codes.

    Each value gets a code ``<<TYP_check>>`` – the same code appears everywhere it occurs.
    The result is saved via the real export path with mandatory checking; the third return
    value is the word count of the invisible text layer.
    """

    import fitz

    from presidio_sidecar.document_workflow import (
        export_processed_document,
        pdf_page_geometry,
        plan_placements,
    )
    from presidio_sidecar.extraction import DocumentExtractor
    from presidio_sidecar.processing import (
        AppliedFinding,
        FindingKind,
        ProcessingMode,
        Reversibility,
    )

    corpus = _corpus()
    values_dict = {
        corpus.NAME: "PERSON",  # type: ignore[attr-defined]
        corpus.STREET: "ADDRESS",  # type: ignore[attr-defined]
        corpus.PHONE: "PHONE_NUMBER",  # type: ignore[attr-defined]
        corpus.EMAIL: "EMAIL_ADDRESS",  # type: ignore[attr-defined]
    }
    codes = {val: (typ, f"{zufall.getrandbits(32):08x}") for val, typ in values_dict.items()}
    with tempfile.TemporaryDirectory() as folder:
        path = corpus.generate(Path(folder), ("scan.pdf",))["scan.pdf"]  # type: ignore[attr-defined]
        ocr = corpus.SehendeOcr(path)  # type: ignore[attr-defined]
        document_obj2 = DocumentExtractor(ocr_backend=ocr, pdf_ocr_dpi=150).extract_bytes(
            path.read_bytes(), filename=path.name
        )
        text = document_obj2.text
        positions = sorted(
            (start, start + len(val), val)
            for val in values_dict
            for start in (m.start() for m in re.finditer(re.escape(val), text))
        )
        output_text, cursor, angewandt = "", 0, []
        for number, (start, end, val) in enumerate(positions):
            typ, check = codes[val]
            output_text += text[cursor:start]
            output_start_idx = len(output_text)
            output_text += f"<<{typ}_{check}>>"
            cursor = end
            angewandt.append(
                AppliedFinding(
                    finding_id=f"f-{number}",
                    kind=FindingKind.CONFIRMED,
                    entity_type=typ,
                    source="rundreise",
                    score=1.0,
                    mode=ProcessingMode.TOKEN_DETERMINISTIC,
                    reversibility=Reversibility.VAULT,
                    original_start=start,
                    original_end=end,
                    output_start=output_start_idx,
                    output_end=len(output_text),
                )
            )
        output_text += text[cursor:]

        @dataclass(frozen=True)
        class Result:
            text: str
            applied_findings: tuple[AppliedFinding, ...]

        result = Result(output_text, tuple(angewandt))
        placements = plan_placements(
            document_obj2, text, result, lambda page: pdf_page_geometry(path, page)
        )
        ocr.codes = corpus.code_areas(placements)  # type: ignore[attr-defined]
        target = Path(folder) / "scan_geschuetzt.pdf"
        export = export_processed_document(
            path,
            target,
            document=document_obj2,
            source_text=text,
            processing=result,  # type: ignore[arg-type]
            ocr_backend=ocr,
            pdf_ocr_dpi=150,
        )
        with fitz.open(target) as saved_doc:
            text_layer = "".join(page.get_text() for page in saved_doc)
    for val in values_dict:
        if val in text_layer:
            raise RuntimeError("Ein Originalwert steht in der Textebene.")
    gesendet = {f"<<{typ}_{check}>>": (typ, check) for typ, check in codes.values()}
    return text_layer, gesendet, export.verification.text_layer_words


def scan_main(args: argparse.Namespace) -> int:
    zufall = random.Random(args.seed)
    runs: list[Run] = []
    for model in args.models:
        for repeat in range(args.repeats):
            # Fresh codes each run: a model never sees the same code twice.
            text_layer, codes, layer = mask_scan(zufall)
            for task, (instruction, _) in SCAN_TASKS.items():
                start = time.monotonic()
                try:
                    response = question(model, f"{instruction}\n\n{text_layer}", args.temperature)
                except OSError as error:
                    print(f"{model} {task}: {error}", file=sys.stderr)
                    response = ""
                duration = time.monotonic() - start
                runs.append(Run(model, "scan", task, repeat, codes, response, duration))
                print(f"{model:32} scan {task:14} {duration:5.1f}s", flush=True)

    per_model: dict[str, dict[str, int]] = {}
    for run in runs:
        values_dict = evaluate(run, SCAN_TASKS[run.task][1])
        target = per_model.setdefault(
            run.model,
            {"sent": 0, "exact": 0, "found": 0, "core": 0, "lost": 0, "corrupted": 0},
        )
        for key, val in values_dict.items():
            target[key] += val
    report = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "purpose": (
            "A-09 with a scan: the text layer of a reversibly masked scan through a local "
            "language model"
        ),
        "models": args.models,
        "repeats": args.repeats,
        "temperature": args.temperature,
        "seed": args.seed,
        "tasks": {name: instruction for name, (instruction, _) in SCAN_TASKS.items()},
        "text_layer_words": layer,
        "per_model": per_model,
        # lossless: every code reproduced by the model is detected and none is damaged.
        # complete: the model also reproduces every code – depends on the task and model
        # understanding,
        # not on the code itself.
        "lossless": {
            model: values_dict["found"] == values_dict["core"] and not values_dict["corrupted"]
            for model, values_dict in per_model.items()
        },
        "complete": {
            model: values_dict["found"] == values_dict["sent"]
            for model, values_dict in per_model.items()
        },
        "runtime_seconds": round(sum(run.sekunden for run in runs), 1),
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.raw_data:
        args.raw_data.write_text(
            json.dumps([run.__dict__ for run in runs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    for model, values_dict in per_model.items():
        gesendet = max(values_dict["sent"], 1)
        print(
            f"{model:32} erkannt {values_dict['found']}/{values_dict['sent']} "
            f"({values_dict['found'] / gesendet:.0%}), exakt {values_dict['exact']}, "
            f"beschaedigt {values_dict['corrupted']}"
        )
    return 0 if all(report["lossless"].values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-data", type=Path)
    parser.add_argument("--scan", action="store_true", help="A-09 mit einem gescannten PDF")
    args = parser.parse_args()
    if any(":cloud" in model or "-cloud" in model for model in args.models):
        print("Cloud-Modelle sind ausgeschlossen: Sie schicken die Anfrage ins Netz.")
        return 2
    if args.scan:
        return scan_main(args)

    zufall = random.Random(args.seed)
    runs: list[Run] = []
    for model in args.models:
        for repeat in range(args.repeats):
            for format_name, pattern in FORMATS.items():
                for task, (instruction, template, _) in TASKS.items():
                    codes = codes_for(template, zufall)
                    text = template
                    for name, (typ, check) in codes.items():
                        text = text.replace("{" + name + "}", pattern.format(typ=typ, check=check))
                    start = time.monotonic()
                    try:
                        response = question(model, f"{instruction}\n\n{text}", args.temperature)
                    except OSError as error:
                        print(
                            f"{model} {format_name} {task}: {error}",
                            file=sys.stderr,
                        )
                        response = ""
                    duration = time.monotonic() - start
                    runs.append(
                        Run(
                            model,
                            format_name,
                            task,
                            repeat,
                            {
                                pattern.format(typ=typ, check=check): (typ, check)
                                for typ, check in codes.values()
                            },
                            response,
                            duration,
                        )
                    )
                    print(
                        f"{model:32} {format_name:14} {task:14} {duration:5.1f}s",
                        flush=True,
                    )

    summen: dict[str, dict[str, dict[str, int]]] = {}
    examples: dict[str, list[str]] = {}
    for run in runs:
        all_tasks = TASKS[run.task][2]
        values_dict = evaluate(run, all_tasks)
        target = summen.setdefault(run.model, {}).setdefault(
            run.format,
            {
                "sent": 0,
                "exact": 0,
                "found": 0,
                "core": 0,
                "lost": 0,
                "corrupted": 0,
            },
        )
        for key, val in values_dict.items():
            target[key] += val
        if values_dict["corrupted"]:
            gesendet = {f"{t}_{p}".upper() for t, p in run.codes.values()}
            examples.setdefault(run.format, []).extend(
                m.group(0)
                for m in _KERN.finditer(run.response)
                if f"{m.group(1)}_{m.group(2)}".upper() not in gesendet
            )

    totals: dict[str, dict[str, int]] = {}
    for je_format in summen.values():
        for format_name, values_dict in je_format.items():
            target = totals.setdefault(format_name, dict.fromkeys(values_dict, 0))
            for key, val in values_dict.items():
                target[key] += val

    report = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "purpose": "Code format after an LLM round trip",
        "models": args.models,
        "repeats": args.repeats,
        "temperature": args.temperature,
        "seed": args.seed,
        "tasks": {name: instruction for name, (instruction, _, _) in TASKS.items()},
        "formats": FORMATS,
        "per_model": summen,
        "total": totals,
        "corrupted_examples": {
            name: sorted(set(values_dict))[:10] for name, values_dict in examples.items()
        },
        "runtime_seconds": round(sum(run.sekunden for run in runs), 1),
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.raw_data:
        args.raw_data.write_text(
            json.dumps([run.__dict__ for run in runs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    for format_name, values_dict in totals.items():
        gesendet = max(values_dict["sent"], 1)
        print(
            f"{format_name:14} exakt {values_dict['exact'] / gesendet:6.1%}  "
            f"erkannt {values_dict['found'] / gesendet:6.1%}  "
            f"kern {values_dict['core'] / gesendet:6.1%}  "
            f"beschaedigt {values_dict['corrupted']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
