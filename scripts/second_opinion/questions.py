"""Step 2: the real sidecar detection creates the questions from the corpus.

For each document the app follows the exact same path (base profile, document
languages DE+EN, countries DE+AT, GLiNER or spaCy fallback). Each finding is
saved with its gold label:

* ``frage``: whether the app shows it as a question (suspected or uncertain),
* ``gold``: ``person``/``organization_or_product``/``location`` when it overlaps a
  placeholder value, otherwise ``term``,
* ``eingabe``: what the classifier sees (``classifier_input``).

Correctly detected findings are not questions but help the encoder learn name
patterns; training weights them lower.

Run from repository root:

    uv run --project sidecar python -m scripts.second_opinion.questions \\
        --corpus testdata/zweitmeinung/korpus.jsonl \\
        --output testdata/zweitmeinung/questions.jsonl
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path


def gold_label(start: int, end: int, gold: list[dict]) -> str:
    """Return the gold label with the largest overlap, or ``term`` if none."""

    best, best_overlap = "term", 0
    for spanne in gold:
        overlap = min(end, spanne["end"]) - max(start, spanne["start"])
        if overlap > best_overlap:
            best, best_overlap = spanne["label"], overlap
    return best


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("testdata/zweitmeinung/korpus.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("testdata/zweitmeinung/fragen.jsonl"))
    args = parser.parse_args(argv)

    from presidio_sidecar.base_profile import base_profile
    from presidio_sidecar.config import Settings
    from presidio_sidecar.detection.second_opinion import classifier_input
    from presidio_sidecar.diagnostics import DiagnosticsRecorder
    from presidio_sidecar.findings_view import detection_for_profile, question_ids
    from presidio_sidecar.processing import FindingKind, finding_id
    from presidio_sidecar.services import PhaseOneServices

    documents = [
        json.loads(line)
        for line in args.corpus.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    documents.sort(key=lambda item: item["id"])
    profile = base_profile()
    beginn = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="zweitmeinung-") as state_dir:
        services = PhaseOneServices(
            Settings(state_directory=Path(state_dir)), DiagnosticsRecorder()
        )
        runner = services.analyzer_for_profile(profile)
        print("Erkennung:", services.nlp_status(), flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as file:
            total_questions = 0
            for index, document in enumerate(documents, 1):
                text = document["text"]
                detection_result = detection_for_profile(
                    runner.analyze(text, progress=lambda _phase: None), profile
                )
                questions = question_ids(detection_result, profile)
                places = []
                for kind, entries in (
                    (FindingKind.CONFIRMED, detection_result.confirmed),
                    (FindingKind.SUSPICIOUS, detection_result.suspicious),
                ):
                    for finding in entries:
                        identifier = finding_id(finding, kind=kind)
                        places.append(
                            {
                                "id": identifier,
                                "kind": kind.value,
                                "typ": finding.entity_type,
                                "start": finding.start,
                                "end": finding.end,
                                "wert": text[finding.start : finding.end],
                                "score": round(finding.score, 4),
                                "quelle": str(finding.source),
                                "detektor": finding.detector,
                                "frage": identifier in questions,
                                "gold": gold_label(finding.start, finding.end, document["gold"]),
                                "eingabe": classifier_input(
                                    text,
                                    finding.start,
                                    finding.end,
                                    entity_type=finding.entity_type,
                                    suspicion=kind is FindingKind.SUSPICIOUS,
                                ),
                            }
                        )
                total_questions += sum(1 for place in places if place["frage"])
                file.write(
                    json.dumps(
                        {
                            "id": document["id"],
                            "sprache": document["sprache"],
                            "stellen": places,
                            # Gold spans that were not touched by any finding –
                            # completely missed by detection.
                            "uebersehen": [
                                spanne
                                for spanne in document["gold"]
                                if not any(
                                    place["start"] < spanne["end"]
                                    and spanne["start"] < place["end"]
                                    for place in places
                                )
                            ],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
                if index % 50 == 0:
                    duration = time.monotonic() - beginn
                    print(
                        f"  {index}/{len(documents)} · {total_questions} Fragen · "
                        f"{duration / index:.2f} s je Dokument",
                        flush=True,
                    )
    print(f"fertig: {len(documents)} Dokumente, {total_questions} Fragen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
