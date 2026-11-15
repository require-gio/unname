"""Precision/Recall measurement and fail-closed recall regression gate.

Usage examples::

    python -m scripts.quality.gate validate --catalog testdata/quality/synthetic-v1/catalog.json
    python -m scripts.quality.gate evaluate --catalog ... --predictions ... --config ...

Prediction files are located under ``<predictions>/<profile>/<condition>.jsonl``.
They intentionally contain no document text or hit values.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .corpus import (
    CONDITIONS,
    CorpusCatalog,
    FindingSpan,
    ProfileCorpus,
    QualityDataError,
    canonical_json_bytes,
    load_catalog,
    load_predictions,
)

REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Counts:
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0

    def __add__(self, other: Counts) -> Counts:
        return Counts(
            self.true_positive + other.true_positive,
            self.false_positive + other.false_positive,
            self.false_negative + other.false_negative,
        )

    def as_dict(self) -> dict[str, int | float]:
        predicted = self.true_positive + self.false_positive
        expected = self.true_positive + self.false_negative
        precision = self.true_positive / predicted if predicted else 1.0
        recall = self.true_positive / expected if expected else 1.0
        return {
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "precision": precision,
            "recall": recall,
        }


def evaluate_profile(
    corpus: ProfileCorpus,
    predictions: dict[str, tuple[FindingSpan, ...]],
) -> dict[str, Any]:
    """Counts exact ``(entity, start, end)`` hits at a micro-aggregated level."""

    totals = Counts()
    by_entity: defaultdict[str, Counts] = defaultdict(Counts)
    documents_by_id = {document.document_id: document for document in corpus.documents}
    for document_id, predicted_items in predictions.items():
        gold = set(documents_by_id[document_id].annotations)
        predicted = set(predicted_items)
        true_positive = gold & predicted
        false_positive = predicted - gold
        false_negative = gold - predicted
        counts = Counts(len(true_positive), len(false_positive), len(false_negative))
        totals += counts
        for item in true_positive:
            by_entity[item.entity_type] += Counts(true_positive=1)
        for item in false_positive:
            by_entity[item.entity_type] += Counts(false_positive=1)
        for item in false_negative:
            by_entity[item.entity_type] += Counts(false_negative=1)

    return {
        "overall": totals.as_dict(),
        "by_entity": {
            entity_type: counts.as_dict() for entity_type, counts in sorted(by_entity.items())
        },
    }


def build_report(
    catalog: CorpusCatalog,
    predictions_directory: Path,
    *,
    runner_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile_reports: dict[str, Any] = {}
    for profile in catalog.profiles:
        conditions: dict[str, Any] = {}
        for condition in CONDITIONS:
            prediction_path = predictions_directory / profile.profile_id / f"{condition}.jsonl"
            predictions = load_predictions(prediction_path, profile)
            conditions[condition] = evaluate_profile(profile, predictions)
        with_recall = conditions["with_master_data"]["overall"]["recall"]
        without_recall = conditions["without_master_data"]["overall"]["recall"]
        profile_reports[profile.profile_id] = {
            "profile_version": profile.profile_version,
            "document_count": len(profile.documents),
            "annotation_count": sum(len(document.annotations) for document in profile.documents),
            "conditions": conditions,
            "master_data_recall_delta": with_recall - without_recall,
        }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus": {
            "id": catalog.corpus_id,
            "version": catalog.version,
            "classification": catalog.classification,
        },
        "runner": runner_metadata or {"engine": "external", "verified": False},
        "profiles": profile_reports,
    }


def apply_gate(
    report: dict[str, Any],
    config: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> tuple[str, ...]:
    """Returns all gate violations; an empty tuple means approval."""

    _validate_gate_config(config)
    failures: list[str] = []
    classification = report.get("corpus", {}).get("classification")
    if classification != config["required_classification"]:
        failures.append(
            f"Korpusklassifikation {classification!r}, gefordert "
            f"{config['required_classification']!r}"
        )
    engine = report.get("runner", {}).get("engine")
    if engine != config["required_runner_engine"]:
        failures.append(f"Runner-Engine {engine!r}, gefordert {config['required_runner_engine']!r}")

    if baseline is not None:
        if baseline.get("schema_version") != REPORT_SCHEMA_VERSION:
            failures.append("Baseline.schema_version ist ungueltig")
        if baseline.get("corpus") != report.get("corpus"):
            failures.append("Baseline gehoert nicht zu exakt dieser Korpusversion")
        if baseline.get("runner", {}).get("engine") != engine:
            failures.append("Baseline wurde mit einer anderen Runner-Engine erzeugt")

    report_profiles = report.get("profiles")
    if not isinstance(report_profiles, dict):
        return (*failures, "Report.profiles fehlt oder ist ungueltig")
    minimum_documents = config["minimum_documents_per_profile"]
    max_drop = float(config["maximum_recall_drop"])
    require_baseline = config["require_baseline"]
    if require_baseline and baseline is None:
        failures.append("Das Gate verlangt einen versionierten Baseline-Report")

    for profile_id in config["required_profiles"]:
        profile_report = report_profiles.get(profile_id)
        if not isinstance(profile_report, dict):
            failures.append(f"Pflichtprofil {profile_id!r} fehlt im Report")
            continue
        if profile_report.get("document_count", -1) < minimum_documents:
            failures.append(
                f"{profile_id}: nur {profile_report.get('document_count')} Dokumente, "
                f"mindestens {minimum_documents} erforderlich"
            )
        condition_reports = profile_report.get("conditions", {})
        for condition in CONDITIONS:
            try:
                recall = float(condition_reports[condition]["overall"]["recall"])
            except (KeyError, TypeError, ValueError):
                failures.append(f"{profile_id}/{condition}: Recall fehlt")
                continue
            minimum_recall = float(config["conditions"][condition]["minimum_recall"])
            if recall + 1e-12 < minimum_recall:
                failures.append(
                    f"{profile_id}/{condition}: Recall {recall:.6f} liegt unter "
                    f"{minimum_recall:.6f}"
                )
            if baseline is not None:
                try:
                    previous = float(
                        baseline["profiles"][profile_id]["conditions"][condition]["overall"][
                            "recall"
                        ]
                    )
                except (KeyError, TypeError, ValueError):
                    failures.append(f"Baseline fuer {profile_id}/{condition} fehlt")
                    continue
                drop = previous - recall
                if drop > max_drop + 1e-12:
                    failures.append(
                        f"{profile_id}/{condition}: Recall-Rueckgang {drop:.6f} "
                        f"ueber erlaubten {max_drop:.6f}"
                    )
    return tuple(failures)


def _validate_gate_config(config: Any) -> None:
    expected = {
        "schema_version",
        "required_classification",
        "required_runner_engine",
        "required_profiles",
        "minimum_documents_per_profile",
        "maximum_recall_drop",
        "require_baseline",
        "conditions",
    }
    if not isinstance(config, dict) or set(config) != expected:
        raise QualityDataError("Gate-Konfiguration hat unbekannte oder fehlende Felder.")
    if config["schema_version"] != 1:
        raise QualityDataError("Gate.schema_version muss 1 sein.")
    if config["required_classification"] not in {"synthetic", "real_pseudonymized"}:
        raise QualityDataError("Gate.required_classification ist ungueltig.")
    if (
        not isinstance(config["required_runner_engine"], str)
        or not config["required_runner_engine"].strip()
    ):
        raise QualityDataError("Gate.required_runner_engine fehlt.")
    profiles = config["required_profiles"]
    if not isinstance(profiles, list) or not profiles or len(set(profiles)) != len(profiles):
        raise QualityDataError("Gate.required_profiles muss eine eindeutige Liste sein.")
    minimum_documents = config["minimum_documents_per_profile"]
    if isinstance(minimum_documents, bool) or not isinstance(minimum_documents, int):
        raise QualityDataError("Gate.minimum_documents_per_profile muss ganzzahlig sein.")
    if minimum_documents < 1:
        raise QualityDataError("Gate.minimum_documents_per_profile muss positiv sein.")
    max_drop = config["maximum_recall_drop"]
    if (
        isinstance(max_drop, bool)
        or not isinstance(max_drop, (int, float))
        or not 0 <= max_drop <= 1
    ):
        raise QualityDataError("Gate.maximum_recall_drop muss zwischen 0 und 1 liegen.")
    if not isinstance(config["require_baseline"], bool):
        raise QualityDataError("Gate.require_baseline muss boolesch sein.")
    conditions = config["conditions"]
    if not isinstance(conditions, dict) or set(conditions) != set(CONDITIONS):
        raise QualityDataError("Gate.conditions muss beide Stammdatenbedingungen enthalten.")
    for condition, rule in conditions.items():
        if not isinstance(rule, dict) or set(rule) != {"minimum_recall"}:
            raise QualityDataError(f"Gate.conditions.{condition} ist ungueltig.")
        value = rule["minimum_recall"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise QualityDataError(f"Gate.conditions.{condition}.minimum_recall ist ungueltig.")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QualityDataError(f"{path} ist kein lesbares UTF-8-JSON.") from error
    if not isinstance(payload, dict):
        raise QualityDataError(f"{path} muss ein JSON-Objekt enthalten.")
    return payload


def _runner_metadata(predictions_directory: Path) -> dict[str, Any]:
    metadata_path = predictions_directory / "run.json"
    payload = _read_json(metadata_path)
    if set(payload) != {"schema_version", "engine", "corpus_id", "corpus_version"}:
        raise QualityDataError(f"{metadata_path} hat unbekannte oder fehlende Felder.")
    if payload["schema_version"] != 1:
        raise QualityDataError("Prediction-run.schema_version muss 1 sein.")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Korpus und Hashes validieren")
    validate.add_argument("--catalog", type=Path, required=True)
    validate.add_argument("--minimum-documents", type=int, default=0)
    validate.add_argument(
        "--require-real",
        action="store_true",
        help="Synthetische Korpora fail-closed ablehnen",
    )

    evaluate = commands.add_parser("evaluate", help="Messen und Recall-Gate anwenden")
    evaluate.add_argument("--catalog", type=Path, required=True)
    evaluate.add_argument("--predictions", type=Path, required=True)
    evaluate.add_argument("--config", type=Path, required=True)
    evaluate.add_argument("--baseline", type=Path)
    evaluate.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            catalog = load_catalog(
                args.catalog,
                minimum_documents=args.minimum_documents,
                require_classification="real_pseudonymized" if args.require_real else None,
            )
            print(
                f"Korpus {catalog.corpus_id}@{catalog.version}: "
                f"{len(catalog.profiles)} Profile validiert ({catalog.classification})."
            )
            return 0

        config = _read_json(args.config)
        catalog = load_catalog(
            args.catalog,
            minimum_documents=config.get("minimum_documents_per_profile", 0),
            require_classification=config.get("required_classification"),
        )
        metadata = _runner_metadata(args.predictions.resolve())
        if (
            metadata["corpus_id"] != catalog.corpus_id
            or metadata["corpus_version"] != catalog.version
        ):
            raise QualityDataError("Prediction-run.json gehoert nicht zum geladenen Korpus.")
        report = build_report(catalog, args.predictions.resolve(), runner_metadata=metadata)
        baseline = _read_json(args.baseline) if args.baseline else None
        failures = apply_gate(report, config, baseline)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_json_bytes(report))
        for profile_id, profile_report in report["profiles"].items():
            with_metrics = profile_report["conditions"]["with_master_data"]["overall"]
            without_metrics = profile_report["conditions"]["without_master_data"]["overall"]
            print(
                f"{profile_id}: mit Stammdaten P={with_metrics['precision']:.4f} "
                f"R={with_metrics['recall']:.4f} FP={with_metrics['false_positive']} "
                f"FN={with_metrics['false_negative']}; ohne Stammdaten "
                f"P={without_metrics['precision']:.4f} R={without_metrics['recall']:.4f} "
                f"FP={without_metrics['false_positive']} FN={without_metrics['false_negative']}"
            )
        if failures:
            print("Recall-Gate fehlgeschlagen:", file=sys.stderr)
            for failure in failures:
                print(f"- {failure}", file=sys.stderr)
            return 1
        print("Recall-Gate bestanden.")
        return 0
    except QualityDataError as error:
        print(f"Qualitaetsdaten ungueltig: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
