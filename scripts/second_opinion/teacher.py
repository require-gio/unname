# ruff: noqa: E501
"""Step 3: a large local LLM labels each question once (distillation).

The teacher sees the whole document and a numbered list of questions
(value and line) and chooses one of four fixed labels per question.
The answer schema limits responses to these labels. The gold from
step 1 is hidden – this lets us measure the teacher's accuracy before
the student learns from them.

Only positions judged by the classifier are labeled
(``ASSESSED_TYPES``): suspicious cases and uncertain names, companies, locations.

Run from the repository root; it resumes where it left off:

    uv run --project sidecar python -m scripts.second_opinion.teacher \\
        --model qwen3.8:27b-mlx \\
        --questions testdata/zweitmeinung/fragen.jsonl \\
        --corpus testdata/zweitmeinung/korpus.jsonl \\
        --output testdata/zweitmeinung/lehrer.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import LABELS
from .ollama import chat, check_local

SYSTEM = """Du prüfst Stellen in Geschäftsdokumenten für ein Programm, das personenbezogene und vertrauliche Angaben maskiert.
Für jede nummerierte Stelle wählst du genau ein Label:
- person: Name einer Person (auch nur Vor- oder Nachname, auch mit Titel oder Anrede wie „Herr", „Dr", „Ms")
- organization_or_product: Name einer bestimmten Firma, Organisation, Marke oder eines bestimmten Produkts
- location: Name eines bestimmten Orts, einer Stadt, Region, Straße oder eines Landes
- term: alles andere – Fachbegriffe, Überschriften, Abteilungen, Funktionen, Gesetze, Normen, Produktgattungen, Anreden und Grußformeln, Satzanfänge
Enthält eine Stelle einen Namen und zusätzlich andere Wörter, wähle das Label des Namens.
Antworte nur mit JSON."""


def assessable(place: dict) -> bool:
    """A question that the classifier evaluates."""

    from presidio_sidecar.detection.second_opinion import ASSESSED_TYPES

    return bool(place["frage"]) and place["typ"] in ASSESSED_TYPES


def _line(text: str, start: int, end: int) -> str:
    segment_start_teacher = text.rfind("\n", 0, start) + 1
    segment_end_teacher = text.find("\n", end)
    line = text[
        segment_start_teacher : segment_end_teacher if segment_end_teacher >= 0 else len(text)
    ].strip()
    return line if len(line) <= 220 else line[:220] + " …"


def schema(count: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer"},
                        "label": {"type": "string", "enum": list(LABELS)},
                    },
                    "required": ["n", "label"],
                },
            }
        },
        "required": ["labels"],
    }


def label_document(document: dict, text: str, model_name: str) -> dict:
    places = [place for place in document["stellen"] if assessable(place)]
    if not places:
        return {"id": document["id"], "modell": model_name, "labels": {}}
    list_text = "\n".join(
        f"{number}. «{place['wert']}» – Zeile: {_line(text, place['start'], place['end'])}"
        for number, place in enumerate(places, 1)
    )
    user = f"DOKUMENT:\n{text}\n\nSTELLEN:\n{list_text}"
    for versuch in range(3):
        roh = chat(
            model_name,
            SYSTEM,
            user,
            temperature=0.0,
            seed=versuch,
            json_format=schema(len(places)),
        )
        try:
            answer = json.loads(roh)["labels"]
            labels = {int(item["n"]): item["label"] for item in answer}
        except (ValueError, KeyError, TypeError):
            continue
        if set(labels) == set(range(1, len(places) + 1)) and set(labels.values()) <= set(LABELS):
            return {
                "id": document["id"],
                "modell": model_name,
                "labels": {place["id"]: labels[n] for n, place in enumerate(places, 1)},
            }
    raise RuntimeError(f"{document['id']}: keine gueltige Antwort")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.8:27b-mlx")
    parser.add_argument(
        "--questions", type=Path, default=Path("testdata/zweitmeinung/fragen.jsonl")
    )
    parser.add_argument("--corpus", type=Path, default=Path("testdata/zweitmeinung/korpus.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("testdata/zweitmeinung/lehrer.jsonl"))
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--only", type=int, default=0, help="nur die ersten n Dokumente (Probe)")
    args = parser.parse_args(argv)
    check_local(args.model)

    texts = {
        item["id"]: item["text"]
        for item in map(json.loads, args.corpus.read_text(encoding="utf-8").splitlines())
    }
    documents = [
        json.loads(line) for line in args.questions.read_text(encoding="utf-8").splitlines()
    ]
    if args.only:
        documents = documents[: args.only]
    done: set[str] = set()
    if args.output.exists():
        done = {
            json.loads(line)["id"]
            for line in args.output.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    pending = [document for document in documents if document["id"] not in done]
    print(f"{len(done)} vorhanden, {len(pending)} offen", flush=True)
    lock = threading.Lock()
    beginn = time.monotonic()
    with (
        ThreadPoolExecutor(args.parallel) as pool,
        args.output.open("a", encoding="utf-8") as output_file_handle,
    ):
        tasks = [
            pool.submit(label_document, document, texts[document["id"]], args.model)
            for document in pending
        ]
        for index, task in enumerate(as_completed(tasks), 1):
            try:
                result_obj = task.result()
            except Exception as ausnahme:  # noqa: BLE001
                print(f"  verworfen: {ausnahme}", file=sys.stderr, flush=True)
                continue
            with lock:
                output_file_handle.write(
                    json.dumps(result_obj, ensure_ascii=False, sort_keys=True) + "\n"
                )
                output_file_handle.flush()
            if index % 20 == 0:
                duration = time.monotonic() - beginn
                print(
                    f"  {index}/{len(pending)} · {duration / index:.1f} s je Dokument", flush=True
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
