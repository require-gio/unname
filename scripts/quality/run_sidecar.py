"""Run the real sidecar detection path on a quality corpus.

``model-free`` is the small deterministic CI smoke for rule and master-data detection.
``profile`` enables the regular local GLiNER/spaCy path and is intended for the real
domain corpus.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .corpus import CONDITIONS, QualityDataError, canonical_json_bytes, load_catalog


class _EmptyNer:
    detector_name = "quality-model-free"

    def detect(self, text: str):
        return ()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--profiles", type=Path, default=Path("profiles"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--engine",
        choices=("model-free", "profile"),
        default="profile",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        catalog = load_catalog(args.catalog)
        _run(catalog, args.profiles.resolve(), args.output.resolve(), args.engine)
        destination = args.output.resolve()
        print(f"Predictions fuer {len(catalog.profiles)} Profile erzeugt: {destination}")
        return 0
    except (QualityDataError, RuntimeError, ValueError) as error:
        print(f"Qualitaetslauf fehlgeschlagen: {error}", file=sys.stderr)
        return 2


def _run(catalog, profiles_directory: Path, output: Path, engine: str) -> None:  # type: ignore[no-untyped-def]
    # Imports stay here: corpus validation and metrics use only the standard library;
    # only live inference needs the sidecar.
    from presidio_sidecar.analysis import PlainTextAnalyzer
    from presidio_sidecar.detection import DetectionPipeline
    from presidio_sidecar.deterministic_recognizers import (
        create_deterministic_recognizers,
    )
    from presidio_sidecar.profiles import MasterDataConfig, load_profile
    from presidio_sidecar.registry import MasterDataRegistry, RegistrySources
    from presidio_sidecar.registry_crypto import generate_registry_key

    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise QualityDataError(
            f"Prediction-Ziel {output} ist nicht leer; vorhandene Laeufe werden nie vermischt."
        )
    output.mkdir(parents=True, exist_ok=True)
    run_payload = {
        "schema_version": 1,
        "engine": engine,
        "corpus_id": catalog.corpus_id,
        "corpus_version": catalog.version,
    }
    (output / "run.json").write_bytes(canonical_json_bytes(run_payload))

    with tempfile.TemporaryDirectory(prefix="presidio-quality-") as temporary:
        temporary_root = Path(temporary)
        for corpus in catalog.profiles:
            profile_path = profiles_directory / f"{corpus.profile_id}.yaml"
            profile = load_profile(profile_path)
            if profile.version != corpus.profile_version:
                raise QualityDataError(
                    f"{corpus.profile_id}: Profilversion {profile.version} passt nicht zur "
                    f"Korpusversion {corpus.profile_version}."
                )
            registry = MasterDataRegistry(
                temporary_root / corpus.profile_id / "registry.enc",
                generate_registry_key(),
                min_length=profile.master_data.min_length,
            )
            registry.reimport(
                RegistrySources(
                    corpus.persons_path,
                    corpus.organizations_path,
                )
            )
            for condition in CONDITIONS:
                enabled = condition == "with_master_data"
                master_config = MasterDataConfig(
                    enabled=enabled,
                    persons_file="quality/persons.json" if enabled else None,
                    orgs_file="quality/organizations.json" if enabled else None,
                    min_length=profile.master_data.min_length,
                    refresh=profile.master_data.refresh,
                )
                active_profile = profile.model_copy(update={"master_data": master_config})
                semantic_pipeline = (
                    DetectionPipeline(ner=_EmptyNer())
                    if engine == "model-free"
                    else DetectionPipeline()
                )
                analyzer = PlainTextAnalyzer(
                    registry,
                    deterministic_recognizers=create_deterministic_recognizers(
                        phone_regions=profile.phone_regions
                    ),
                    semantic_pipeline=semantic_pipeline,
                )
                destination = output / corpus.profile_id / f"{condition}.jsonl"
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("w", encoding="utf-8", newline="\n") as stream:
                    for document in corpus.documents:
                        result = analyzer.analyze_for_profile(
                            document.text,
                            active_profile,
                            progress=lambda _: None,
                        )
                        findings = [
                            {
                                "entity_type": finding.entity_type,
                                "start": finding.start,
                                "end": finding.end,
                                "score": finding.score,
                                "source": str(finding.source),
                            }
                            for finding in result.all_findings
                        ]
                        payload = {
                            "document_id": document.document_id,
                            "findings": findings,
                        }
                        stream.write(
                            json.dumps(
                                payload,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            + "\n"
                        )


if __name__ == "__main__":
    raise SystemExit(main())
