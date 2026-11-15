"""Erzeugt den klar als synthetisch markierten 200er-CI-Smoke-Korpus."""

from __future__ import annotations

import argparse
from pathlib import Path

from .corpus import canonical_json_bytes, sha256_file

CORPUS_ID = "presidio-startprofile-synthetic"
CORPUS_VERSION = "1.0.0"
PROFILE_VERSIONS = {
    "de-at-rechnung-v3": "3.0.0",
    "kanzlei-beratung-v1": "1.0.0",
    "llm-upload-v1": "1.0.0",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error(f"Ziel {output} ist nicht leer; bestehende Korpora werden nie ueberschrieben.")
    output.mkdir(parents=True, exist_ok=True)

    catalog_entries = []
    for profile_id, profile_version in PROFILE_VERSIONS.items():
        profile_directory = output / profile_id
        master_directory = profile_directory / "master-data"
        master_directory.mkdir(parents=True)
        persons: list[str] = []
        organizations: list[str] = []
        documents: list[dict[str, object]] = []
        for number in range(1, 201):
            suffix = f"{number:04d}"
            person = f"testperson{suffix}"
            organization = f"testfirma{suffix}"
            email = f"qualitaet{suffix}@example.at"
            text = (
                f"kontakt: {person}\n"
                f"organisation: {organization}\n"
                f"email: {email}\n"
                f"notiz: synthetischer qualitaetstest {suffix}"
            )
            persons.append(person)
            organizations.append(organization)
            documents.append(
                {
                    "document_id": f"synthetic-{suffix}",
                    "text": text,
                    "annotations": [
                        {
                            "entity_type": "PERSON",
                            "start": text.index(person),
                            "end": text.index(person) + len(person),
                        },
                        {
                            "entity_type": "ORGANIZATION",
                            "start": text.index(organization),
                            "end": text.index(organization) + len(organization),
                        },
                        {
                            "entity_type": "EMAIL_ADDRESS",
                            "start": text.index(email),
                            "end": text.index(email) + len(email),
                        },
                    ],
                }
            )

        persons_path = master_directory / "persons.json"
        organizations_path = master_directory / "organizations.json"
        documents_path = profile_directory / "documents.jsonl"
        persons_path.write_bytes(canonical_json_bytes(persons))
        organizations_path.write_bytes(canonical_json_bytes(organizations))
        with documents_path.open("wb") as stream:
            for document in documents:
                stream.write(canonical_json_bytes(document))

        manifest = {
            "schema_version": 1,
            "corpus_id": CORPUS_ID,
            "corpus_version": CORPUS_VERSION,
            "profile_id": profile_id,
            "profile_version": profile_version,
            "classification": "synthetic",
            "offset_encoding": "unicode_codepoints",
            "provenance": {
                "kind": "synthetic",
                "generator": "scripts/quality/generate_synthetic.py",
                "description": (
                    "Deterministische, frei erfundene Modellfrei-Fixtures; keine realen Dokumente."
                ),
                "contains_real_personal_data": False,
            },
            "documents": {
                "path": "documents.jsonl",
                "count": len(documents),
                "sha256": sha256_file(documents_path),
            },
            "master_data": {
                "persons": {
                    "path": "master-data/persons.json",
                    "count": len(persons),
                    "sha256": sha256_file(persons_path),
                },
                "organizations": {
                    "path": "master-data/organizations.json",
                    "count": len(organizations),
                    "sha256": sha256_file(organizations_path),
                },
            },
        }
        manifest_path = profile_directory / "manifest.json"
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        catalog_entries.append(
            {
                "profile_id": profile_id,
                "manifest": f"{profile_id}/manifest.json",
                "sha256": sha256_file(manifest_path),
            }
        )

    catalog = {
        "schema_version": 1,
        "corpus_id": CORPUS_ID,
        "version": CORPUS_VERSION,
        "classification": "synthetic",
        "profiles": catalog_entries,
    }
    (output / "catalog.json").write_bytes(canonical_json_bytes(catalog))
    print(f"Synthetischer Korpus unter {output} erzeugt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
