"""Step 5: Questions and recall with and without classifier (milestone K).

Measured on the test split (25 % of documents, never trained, never calibrated) against the gold
from step 1 – not against the teacher. A question is considered masked when it overlaps a
placeholder value (person, company/product, location).

Per condition:

* ``offen``: questions that still need review,
* ``recall``: proportion of mask-eligible questions that are masked when the user leaves all open
  items untouched (“when in doubt, mask”) – the worst-case scenario,
* ``precision``: proportion of masked questions that should be masked,
* ``ki_fehler``: mask-eligible questions that the model kept.

Conditions: without classifier, ``protect``, ``protect_and_keep`` (95 %), ``suggest`` – and
“accept all model suggestions” when ``suggest``.

Run from repository root:

    uv run --project sidecar python -m scripts.second_opinion.measure \\
        --model sidecar/models/second-opinion --output acceptance/second-opinion.json
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from . import LABELS
from .teacher import assessable
from .train import part


def _quote(counter: int, nenner: int) -> float | None:
    return round(counter / nenner, 4) if nenner else None


def condition(question_list: list[dict], decisions: list[str | None]) -> dict:
    """``decisions``: per question ``accept``/``reject`` from the model or ``None`` (open)."""

    positiv = [question["gold"] != "term" for question in question_list]
    masked = [decision != "reject" for decision in decisions]
    tp = sum(1 for p, m in zip(positiv, masked, strict=True) if p and m)
    return {
        "questions": len(question_list),
        "open": sum(1 for decision in decisions if decision is None),
        "ai_masked": sum(1 for decision in decisions if decision == "accept"),
        "ai_kept": sum(1 for decision in decisions if decision == "reject"),
        "recall": _quote(tp, sum(positiv)),
        "precision": _quote(tp, sum(masked)),
        "ai_errors": sum(1 for p, e in zip(positiv, decisions, strict=True) if p and e == "reject"),
        "ai_masked_unnecessarily": sum(
            1 for p, e in zip(positiv, decisions, strict=True) if not p and e == "accept"
        ),
    }


def _ece(wahrscheinlichkeiten: list[float], richtig: list[bool], subjects: int = 10) -> float:
    total = 0.0
    for subject in range(subjects):
        lower, upper = subject / subjects, (subject + 1) / subjects
        selection = [
            (p, r)
            for p, r in zip(wahrscheinlichkeiten, richtig, strict=True)
            if lower < p <= upper or (subject == 0 and p == 0)
        ]
        if selection:
            mittel = sum(p for p, _ in selection) / len(selection)
            hits = sum(1 for _, r in selection if r) / len(selection)
            total += len(selection) / len(richtig) * abs(mittel - hits)
    return round(total, 4)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions", type=Path, default=Path("testdata/zweitmeinung/fragen.jsonl")
    )
    parser.add_argument("--teacher", type=Path, default=Path("testdata/zweitmeinung/lehrer.jsonl"))
    parser.add_argument("--model", type=Path, default=Path("sidecar/models/second-opinion"))
    parser.add_argument("--output", type=Path, default=Path("acceptance/second-opinion.json"))
    args = parser.parse_args(argv)

    from presidio_sidecar.detection.second_opinion import SecondOpinionModel, assess

    teacher_by_id: dict[str, str] = {}
    if args.teacher.exists():
        for line in args.teacher.read_text(encoding="utf-8").splitlines():
            if line.strip():
                teacher_by_id.update(json.loads(line)["labels"])
    documents = [
        json.loads(line) for line in args.questions.read_text(encoding="utf-8").splitlines()
    ]
    test = [document for document in documents if part(document["id"]) == "test"]
    question_list = [
        {**place, "sprache": document["sprache"]}
        for document in test
        for place in document["stellen"]
        if assessable(place)
    ]
    total_questions = sum(1 for d in test for place in d["stellen"] if place["frage"])

    model_instance = SecondOpinionModel(args.model, threads=4)
    beginn = time.monotonic()
    wahrscheinlichkeiten = model_instance.probabilities(
        [question["eingabe"] for question in question_list]
    )
    duration = time.monotonic() - beginn

    def apply_policy(policy_str: str) -> list[str | None]:
        result: list[str | None] = []
        for values in wahrscheinlichkeiten:
            assessment = assess(
                values,
                policy=policy_str,
                keep_threshold=0.95,
                model=model_instance.version,  # type: ignore[arg-type]
            )
            result.append(assessment.suggestion if assessment.decided else None)
        return result

    suggestions = [
        assess(values, policy="suggest", keep_threshold=0.95, model="").suggestion
        for values in wahrscheinlichkeiten
    ]
    conditions = {
        "without_classifier": condition(question_list, [None] * len(question_list)),
        "protect": condition(question_list, apply_policy("protect")),
        "protect_and_keep_95": condition(question_list, apply_policy("protect_and_keep")),
        "suggest": condition(question_list, apply_policy("suggest")),
        "suggest_all_accepted": condition(question_list, suggestions),
    }
    per_language = {
        language: {
            name: condition(
                [f for f in question_list if f["sprache"] == language],
                [e for f, e in zip(question_list, values, strict=True) if f["sprache"] == language],
            )
            for name, values in (
                ("without_classifier", [None] * len(question_list)),
                ("protect", apply_policy("protect")),
                ("protect_and_keep_95", apply_policy("protect_and_keep")),
            )
        }
        for language in ("de", "en")
    }

    vorhersage = [LABELS[max(range(4), key=values.__getitem__)] for values in wahrscheinlichkeiten]
    gold = [question["gold"] for question in question_list]
    with_teacher = [
        (index, teacher_by_id[question["id"]])
        for index, question in enumerate(question_list)
        if question["id"] in teacher_by_id
    ]
    confusion = Counter(f"{g}→{v}" for g, v in zip(gold, vorhersage, strict=True) if g != v)
    ai_error_examples = [
        {"wert": question["wert"], "gold": question["gold"], "eingabe": question["eingabe"]}
        for question, decision in zip(question_list, apply_policy("protect_and_keep"), strict=True)
        if question["gold"] != "term" and decision == "reject"
    ][:20]
    protection = [sum(values[:3]) for values in wahrscheinlichkeiten]
    direction = [max(s, 1 - s) for s in protection]
    direction_correct = [(s >= 0.5) == (g != "term") for s, g in zip(protection, gold, strict=True)]
    config = json.loads((args.model / "zweitmeinung.json").read_text(encoding="utf-8"))

    report = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": config["version"],
        "encoder": config["encoder"],
        "quantization": config["quantization"],
        "corpus": {
            "documents": len(documents),
            "test_documents": len(test),
            "questions_in_test": total_questions,
            "assessable_questions": len(question_list),
            "gold_distribution": dict(Counter(gold)),
        },
        "conditions": conditions,
        "per_language": per_language,
        "classifier": {
            "accuracy_gold_4_labels": _quote(
                sum(1 for g, v in zip(gold, vorhersage, strict=True) if g == v), len(gold)
            ),
            "accuracy_gold_protect_yes_no": _quote(sum(direction_correct), len(gold)),
            "calibration_ece_protect": _ece(direction, direction_correct),
            "confusions": dict(confusion.most_common()),
            "agreement_with_teacher": _quote(
                sum(1 for i, label in with_teacher if vorhersage[i] == label), len(with_teacher)
            ),
            "ms_per_question_cpu": round(1000 * duration / max(1, len(question_list)), 2),
        },
        "teacher": {
            "accuracy_gold_4_labels": _quote(
                sum(1 for i, label in with_teacher if gold[i] == label), len(with_teacher)
            ),
            "accuracy_gold_protect_yes_no": _quote(
                sum(1 for i, label in with_teacher if (label == "term") == (gold[i] == "term")),
                len(with_teacher),
            ),
        },
        "ai_kept_despite_needing_mask": ai_error_examples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for name, values in conditions.items():
        print(
            f"{name:26} offen {values['open']:5}/{values['questions']} · "
            f"Recall {values['recall']} · Precision {values['precision']} · "
            f"KI-Fehler {values['ai_errors']}"
        )
    print(json.dumps(report["classifier"], ensure_ascii=False))
    print(json.dumps(report["teacher"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
