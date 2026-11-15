"""Import only already pseudonymized real corpora.

The tool does not perform pseudonymization itself. A role-based attestation in the
import spec is required; only then are the three profiles atomically added to the
versioned manifest structure and fully validated again.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from .corpus import QualityDataError, canonical_json_bytes, load_catalog, sha256_file

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_REQUIRED_PROFILES = {
    "de-at-rechnung-v3",
    "kanzlei-beratung-v1",
    "llm-upload-v1",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        catalog = import_real_corpus(args.spec.resolve(), args.output.resolve())
        print(
            f"Realer, vorab pseudonymisierter Korpus {catalog.corpus_id}@{catalog.version} "
            f"mit {len(catalog.profiles)} Profilen importiert."
        )
        return 0
    except (QualityDataError, OSError, ValueError) as error:
        print(f"Import abgelehnt: {error}", file=sys.stderr)
        return 2


def import_real_corpus(spec_path: Path, output: Path):  # type: ignore[no-untyped-def]
    spec = _read_spec(spec_path)
    if output.exists():
        raise QualityDataError(
            f"Ziel {output} existiert bereits; Korpusversionen sind unveraenderlich."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        catalog_entries = []
        for profile in spec["profiles"]:
            profile_id = profile["profile_id"]
            destination = temporary / profile_id
            master_destination = destination / "master-data"
            master_destination.mkdir(parents=True)
            documents_path = destination / "documents.jsonl"
            persons_path = master_destination / "persons.json"
            organizations_path = master_destination / "organizations.json"
            _copy_private_source(spec_path.parent, profile["documents"], documents_path)
            _copy_private_source(spec_path.parent, profile["persons"], persons_path)
            _copy_private_source(
                spec_path.parent,
                profile["organizations"],
                organizations_path,
            )
            document_count = _jsonl_count(documents_path)
            persons_count = _json_list_count(persons_path)
            organizations_count = _json_list_count(organizations_path)
            manifest = {
                "schema_version": 1,
                "corpus_id": spec["corpus_id"],
                "corpus_version": spec["version"],
                "profile_id": profile_id,
                "profile_version": profile["profile_version"],
                "classification": "real_pseudonymized",
                "offset_encoding": "unicode_codepoints",
                "provenance": profile["provenance"],
                "documents": {
                    "path": "documents.jsonl",
                    "count": document_count,
                    "sha256": sha256_file(documents_path),
                },
                "master_data": {
                    "persons": {
                        "path": "master-data/persons.json",
                        "count": persons_count,
                        "sha256": sha256_file(persons_path),
                    },
                    "organizations": {
                        "path": "master-data/organizations.json",
                        "count": organizations_count,
                        "sha256": sha256_file(organizations_path),
                    },
                },
            }
            manifest_path = destination / "manifest.json"
            manifest_path.write_bytes(canonical_json_bytes(manifest))
            os.chmod(manifest_path, 0o600)
            catalog_entries.append(
                {
                    "profile_id": profile_id,
                    "manifest": f"{profile_id}/manifest.json",
                    "sha256": sha256_file(manifest_path),
                }
            )
        catalog_payload = {
            "schema_version": 1,
            "corpus_id": spec["corpus_id"],
            "version": spec["version"],
            "classification": "real_pseudonymized",
            "profiles": catalog_entries,
        }
        catalog_path = temporary / "catalog.json"
        catalog_path.write_bytes(canonical_json_bytes(catalog_payload))
        os.chmod(catalog_path, 0o600)
        # Final validation runs before the atomic visibility step.
        load_catalog(
            catalog_path,
            minimum_documents=200,
            require_classification="real_pseudonymized",
        )
        temporary.rename(output)
        return load_catalog(
            output / "catalog.json",
            minimum_documents=200,
            require_classification="real_pseudonymized",
        )
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _read_spec(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            spec = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QualityDataError(f"Import-Spec {path} ist kein lesbares UTF-8-JSON.") from error
    expected = {"schema_version", "corpus_id", "version", "profiles"}
    if not isinstance(spec, dict) or set(spec) != expected or spec["schema_version"] != 1:
        raise QualityDataError("Import-Spec hat unbekannte/fehlende Felder oder falsche Version.")
    if not isinstance(spec["profiles"], list) or not spec["profiles"]:
        raise QualityDataError("Import-Spec.profiles muss eine nicht-leere Liste sein.")
    expected_profile = {
        "profile_id",
        "profile_version",
        "documents",
        "persons",
        "organizations",
        "provenance",
    }
    for index, profile in enumerate(spec["profiles"]):
        if not isinstance(profile, dict) or set(profile) != expected_profile:
            raise QualityDataError(f"Import-Spec.profiles[{index}] ist unvollstaendig.")
        profile_id = profile["profile_id"]
        if not isinstance(profile_id, str) or not _SAFE_ID.fullmatch(profile_id):
            raise QualityDataError(
                f"Import-Spec.profiles[{index}].profile_id ist keine sichere ID."
            )
    profile_ids = [profile["profile_id"] for profile in spec["profiles"]]
    if len(set(profile_ids)) != len(profile_ids):
        raise QualityDataError("Import-Spec enthaelt doppelte Profil-IDs.")
    if set(profile_ids) != _REQUIRED_PROFILES:
        raise QualityDataError(
            "Import-Spec muss exakt die drei ausgelieferten Startprofile enthalten."
        )
    return spec


def _copy_private_source(base: Path, raw_path: Any, destination: Path) -> None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise QualityDataError("Import-Quellpfad muss eine nicht-leere Zeichenkette sein.")
    source = Path(raw_path)
    if not source.is_absolute():
        source = (base / source).resolve()
    if not source.is_file():
        raise QualityDataError(f"Importquelle {source} fehlt oder ist keine Datei.")
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o600)


def _jsonl_count(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                raise QualityDataError(f"{path}:{line_number}: Leerzeilen sind verboten.")
            count += 1
    return count


def _json_list_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QualityDataError(f"{path} ist kein gueltiges UTF-8-JSON.") from error
    if not isinstance(payload, list):
        raise QualityDataError(f"{path} muss eine JSON-Liste sein.")
    return len(payload)


if __name__ == "__main__":
    raise SystemExit(main())
