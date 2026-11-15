from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from scripts.quality.corpus import (
    Document,
    FindingSpan,
    ProfileCorpus,
    QualityDataError,
    canonical_json_bytes,
    load_catalog,
    sha256_file,
)
from scripts.quality.gate import apply_gate, evaluate_profile


class CorpusContractTests(unittest.TestCase):
    def test_hash_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog_path, documents_path = _minimal_catalog(Path(temporary))
            load_catalog(catalog_path, minimum_documents=1)
            documents_path.write_text("manipuliert\n", encoding="utf-8")

            with self.assertRaisesRegex(QualityDataError, "SHA-256"):
                load_catalog(catalog_path)

    def test_synthetic_corpus_cannot_pass_require_real(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog_path, _ = _minimal_catalog(Path(temporary))

            with self.assertRaisesRegex(QualityDataError, "gefordert"):
                load_catalog(catalog_path, require_classification="real_pseudonymized")


class MeasurementTests(unittest.TestCase):
    def test_precision_recall_fp_and_fn_are_counted_exactly(self) -> None:
        document = Document(
            "doc-1",
            "Ada ada@example.at Wien",
            (
                FindingSpan("PERSON", 0, 3),
                FindingSpan("EMAIL_ADDRESS", 4, 18),
            ),
        )
        corpus = ProfileCorpus(
            corpus_id="test",
            corpus_version="1.0.0",
            profile_id="profile",
            profile_version="1.0.0",
            classification="synthetic",
            manifest_path=Path("manifest.json"),
            documents=(document,),
            persons_path=Path("persons.json"),
            organizations_path=Path("organizations.json"),
            persons=(),
            organizations=(),
        )
        predictions = {
            "doc-1": (
                FindingSpan("PERSON", 0, 3),
                FindingSpan("LOCATION", 19, 23),
            )
        }

        metrics = evaluate_profile(corpus, predictions)["overall"]

        self.assertEqual(metrics["true_positive"], 1)
        self.assertEqual(metrics["false_positive"], 1)
        self.assertEqual(metrics["false_negative"], 1)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)

    def test_recall_regression_over_half_percentage_point_fails(self) -> None:
        report = _report(0.90)
        baseline = _report(0.906)
        failures = apply_gate(report, _gate_config(), baseline)

        self.assertTrue(any("Recall-Rueckgang" in failure for failure in failures))

    def test_recall_regression_at_half_percentage_point_passes(self) -> None:
        report = _report(0.90)
        baseline = _report(0.905)

        self.assertEqual(apply_gate(report, _gate_config(), baseline), ())


def _minimal_catalog(root: Path) -> tuple[Path, Path]:
    profile = root / "profile"
    master = profile / "master-data"
    master.mkdir(parents=True)
    document = {
        "document_id": "doc-1",
        "text": "testperson",
        "annotations": [{"entity_type": "PERSON", "start": 0, "end": 10}],
    }
    documents = profile / "documents.jsonl"
    documents.write_bytes(canonical_json_bytes(document))
    persons = master / "persons.json"
    organizations = master / "organizations.json"
    persons.write_bytes(canonical_json_bytes(["testperson"]))
    organizations.write_bytes(canonical_json_bytes([]))
    manifest = {
        "schema_version": 1,
        "corpus_id": "test-corpus",
        "corpus_version": "1.0.0",
        "profile_id": "profile",
        "profile_version": "1.0.0",
        "classification": "synthetic",
        "offset_encoding": "unicode_codepoints",
        "provenance": {
            "kind": "synthetic",
            "generator": "unit-test",
            "description": "Frei erfundener Test.",
            "contains_real_personal_data": False,
        },
        "documents": {
            "path": "documents.jsonl",
            "count": 1,
            "sha256": sha256_file(documents),
        },
        "master_data": {
            "persons": {
                "path": "master-data/persons.json",
                "count": 1,
                "sha256": sha256_file(persons),
            },
            "organizations": {
                "path": "master-data/organizations.json",
                "count": 0,
                "sha256": sha256_file(organizations),
            },
        },
    }
    manifest_path = profile / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    catalog = {
        "schema_version": 1,
        "corpus_id": "test-corpus",
        "version": "1.0.0",
        "classification": "synthetic",
        "profiles": [
            {
                "profile_id": "profile",
                "manifest": "profile/manifest.json",
                "sha256": sha256_file(manifest_path),
            }
        ],
    }
    catalog_path = root / "catalog.json"
    catalog_path.write_bytes(canonical_json_bytes(catalog))
    return catalog_path, documents


def _report(recall: float) -> dict[str, object]:
    condition = {"overall": {"recall": recall}}
    return {
        "schema_version": 1,
        "corpus": {
            "id": "test-corpus",
            "version": "1.0.0",
            "classification": "synthetic",
        },
        "runner": {"engine": "model-free"},
        "profiles": {
            "profile": {
                "document_count": 200,
                "conditions": {
                    "with_master_data": copy.deepcopy(condition),
                    "without_master_data": copy.deepcopy(condition),
                },
            }
        },
    }


def _gate_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "required_classification": "synthetic",
        "required_runner_engine": "model-free",
        "required_profiles": ["profile"],
        "minimum_documents_per_profile": 200,
        "maximum_recall_drop": 0.005,
        "require_baseline": True,
        "conditions": {
            "with_master_data": {"minimum_recall": 0.0},
            "without_master_data": {"minimum_recall": 0.0},
        },
    }


if __name__ == "__main__":
    unittest.main()
