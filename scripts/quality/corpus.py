"""Strict, data-sparse file contracts for quality corpora.

The corpus stores only the text and gold offsets needed for a span measurement.
Source paths, original values, replacement tables and free-text provenance per
document are not part of the format. All referenced files are bound to a
versioned manifest via SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Literal

SCHEMA_VERSION: Final = 1
CONDITIONS: Final = ("with_master_data", "without_master_data")
CLASSIFICATIONS: Final = frozenset({"synthetic", "real_pseudonymized"})
_ENTITY_ALIASES: Final = {"ORG": "ORGANIZATION"}
_ENTITY_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ID_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_MAX_JSONL_LINE_BYTES: Final = 10 * 1024 * 1024


class QualityDataError(ValueError):
    """A corpus or prediction file violates the fail-closed contract."""


@dataclass(frozen=True, slots=True, order=True)
class FindingSpan:
    entity_type: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Document:
    document_id: str
    text: str
    annotations: tuple[FindingSpan, ...]


@dataclass(frozen=True, slots=True)
class FileReference:
    path: Path
    count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ProfileCorpus:
    corpus_id: str
    corpus_version: str
    profile_id: str
    profile_version: str
    classification: Literal["synthetic", "real_pseudonymized"]
    manifest_path: Path
    documents: tuple[Document, ...]
    persons_path: Path
    organizations_path: Path
    persons: tuple[str, ...]
    organizations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CorpusCatalog:
    corpus_id: str
    version: str
    classification: Literal["synthetic", "real_pseudonymized"]
    catalog_path: Path
    profiles: tuple[ProfileCorpus, ...]

    def profile(self, profile_id: str) -> ProfileCorpus:
        for profile in self.profiles:
            if profile.profile_id == profile_id:
                return profile
        raise KeyError(profile_id)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def load_catalog(
    path: str | Path,
    *,
    minimum_documents: int = 0,
    require_classification: str | None = None,
) -> CorpusCatalog:
    """Load catalog, manifests and data with integrity check."""

    catalog_path = Path(path).resolve()
    payload = _read_json(catalog_path, "Katalog")
    _exact_keys(
        payload,
        {"schema_version", "corpus_id", "version", "classification", "profiles"},
        "Katalog",
    )
    _schema_version(payload, "Katalog")
    corpus_id = _identifier(payload["corpus_id"], "Katalog.corpus_id")
    version = _nonempty_string(payload["version"], "Katalog.version")
    classification = _classification(payload["classification"], "Katalog.classification")
    if require_classification is not None and classification != require_classification:
        raise QualityDataError(
            f"Katalog ist {classification!r}, gefordert ist {require_classification!r}."
        )
    entries = payload["profiles"]
    if not isinstance(entries, list) or not entries:
        raise QualityDataError("Katalog.profiles muss eine nicht-leere Liste sein.")

    profiles: list[ProfileCorpus] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"Katalog.profiles[{index}]"
        _exact_keys(entry, {"profile_id", "manifest", "sha256"}, label)
        profile_id = _identifier(entry["profile_id"], f"{label}.profile_id")
        if profile_id in seen:
            raise QualityDataError(f"Profil {profile_id!r} steht mehrfach im Katalog.")
        seen.add(profile_id)
        manifest_path = _safe_relative_file(
            catalog_path.parent,
            entry["manifest"],
            f"{label}.manifest",
        )
        expected_hash = _sha256(entry["sha256"], f"{label}.sha256")
        _verify_hash(manifest_path, expected_hash)
        profiles.append(
            _load_profile_manifest(
                manifest_path,
                expected_profile_id=profile_id,
                expected_corpus_id=corpus_id,
                expected_corpus_version=version,
                expected_classification=classification,
                minimum_documents=minimum_documents,
            )
        )

    return CorpusCatalog(
        corpus_id=corpus_id,
        version=version,
        classification=classification,
        catalog_path=catalog_path,
        profiles=tuple(profiles),
    )


def load_predictions(path: str | Path, corpus: ProfileCorpus) -> dict[str, tuple[FindingSpan, ...]]:
    """Load text-free prediction JSONL and require exactly one result per document."""

    prediction_path = Path(path).resolve()
    expected_documents = {document.document_id: document for document in corpus.documents}
    predictions: dict[str, tuple[FindingSpan, ...]] = {}
    for line_number, payload in _iter_jsonl(prediction_path):
        label = f"{prediction_path}:{line_number}"
        _exact_keys(payload, {"document_id", "findings"}, label)
        document_id = _identifier(payload["document_id"], f"{label}.document_id")
        document = expected_documents.get(document_id)
        if document is None:
            raise QualityDataError(f"{label}: unbekannte document_id {document_id!r}.")
        if document_id in predictions:
            raise QualityDataError(f"{label}: document_id {document_id!r} ist doppelt.")
        raw_findings = payload["findings"]
        if not isinstance(raw_findings, list):
            raise QualityDataError(f"{label}.findings muss eine Liste sein.")
        findings = tuple(
            _prediction_span(item, document, f"{label}.findings[{index}]")
            for index, item in enumerate(raw_findings)
        )
        if len(set(findings)) != len(findings):
            raise QualityDataError(f"{label}.findings enthaelt doppelte Treffer.")
        predictions[document_id] = findings

    missing = sorted(set(expected_documents) - set(predictions))
    if missing:
        preview = ", ".join(missing[:3])
        raise QualityDataError(
            f"{prediction_path}: {len(missing)} Dokumentergebnisse fehlen ({preview})."
        )
    return predictions


def _load_profile_manifest(
    path: Path,
    *,
    expected_profile_id: str,
    expected_corpus_id: str,
    expected_corpus_version: str,
    expected_classification: str,
    minimum_documents: int,
) -> ProfileCorpus:
    payload = _read_json(path, f"Manifest {path}")
    _exact_keys(
        payload,
        {
            "schema_version",
            "corpus_id",
            "corpus_version",
            "profile_id",
            "profile_version",
            "classification",
            "offset_encoding",
            "provenance",
            "documents",
            "master_data",
        },
        f"Manifest {path}",
    )
    _schema_version(payload, f"Manifest {path}")
    corpus_id = _identifier(payload["corpus_id"], "Manifest.corpus_id")
    corpus_version = _nonempty_string(payload["corpus_version"], "Manifest.corpus_version")
    profile_id = _identifier(payload["profile_id"], "Manifest.profile_id")
    profile_version = _nonempty_string(payload["profile_version"], "Manifest.profile_version")
    classification = _classification(payload["classification"], "Manifest.classification")
    if (
        corpus_id != expected_corpus_id
        or corpus_version != expected_corpus_version
        or profile_id != expected_profile_id
        or classification != expected_classification
    ):
        raise QualityDataError(f"Manifest {path} widerspricht seinem Katalogeintrag.")
    if payload["offset_encoding"] != "unicode_codepoints":
        raise QualityDataError("Manifest.offset_encoding muss 'unicode_codepoints' sein.")
    _validate_provenance(payload["provenance"], classification)

    documents_ref = _file_reference(payload["documents"], path.parent, "Manifest.documents")
    if documents_ref.count < minimum_documents:
        raise QualityDataError(
            f"Profil {profile_id}: {documents_ref.count} Dokumente, gefordert sind "
            f"mindestens {minimum_documents}."
        )
    documents = _load_documents(documents_ref, profile_id)
    if not any(document.annotations for document in documents):
        raise QualityDataError(f"Profil {profile_id} enthaelt keine einzige Gold-Annotation.")

    master_data = payload["master_data"]
    _exact_keys(master_data, {"persons", "organizations"}, "Manifest.master_data")
    persons_ref = _file_reference(master_data["persons"], path.parent, "master_data.persons")
    orgs_ref = _file_reference(
        master_data["organizations"], path.parent, "master_data.organizations"
    )
    persons = _load_string_list(persons_ref, "master_data.persons")
    organizations = _load_string_list(orgs_ref, "master_data.organizations")

    return ProfileCorpus(
        corpus_id=corpus_id,
        corpus_version=corpus_version,
        profile_id=profile_id,
        profile_version=profile_version,
        classification=classification,
        manifest_path=path,
        documents=documents,
        persons_path=persons_ref.path,
        organizations_path=orgs_ref.path,
        persons=persons,
        organizations=organizations,
    )


def _load_documents(reference: FileReference, profile_id: str) -> tuple[Document, ...]:
    _verify_hash(reference.path, reference.sha256)
    documents: list[Document] = []
    seen_ids: set[str] = set()
    for line_number, payload in _iter_jsonl(reference.path):
        label = f"{reference.path}:{line_number}"
        _exact_keys(payload, {"document_id", "text", "annotations"}, label)
        document_id = _identifier(payload["document_id"], f"{label}.document_id")
        if document_id in seen_ids:
            raise QualityDataError(f"{label}: document_id {document_id!r} ist doppelt.")
        seen_ids.add(document_id)
        text = payload["text"]
        if not isinstance(text, str) or not text or "\x00" in text:
            raise QualityDataError(f"{label}.text muss nicht leer und NUL-frei sein.")
        raw_annotations = payload["annotations"]
        if not isinstance(raw_annotations, list):
            raise QualityDataError(f"{label}.annotations muss eine Liste sein.")
        shell = Document(document_id=document_id, text=text, annotations=())
        annotations = tuple(
            _gold_span(item, shell, f"{label}.annotations[{index}]")
            for index, item in enumerate(raw_annotations)
        )
        if len(set(annotations)) != len(annotations):
            raise QualityDataError(f"{label}.annotations enthaelt doppelte Spans.")
        documents.append(Document(document_id, text, annotations))
    if len(documents) != reference.count:
        raise QualityDataError(
            f"{reference.path}: Manifest nennt {reference.count}, Datei enthaelt "
            f"{len(documents)} Dokumente fuer {profile_id}."
        )
    return tuple(documents)


def _gold_span(payload: Any, document: Document, label: str) -> FindingSpan:
    _exact_keys(payload, {"entity_type", "start", "end"}, label)
    return _span(payload, document, label)


def _prediction_span(payload: Any, document: Document, label: str) -> FindingSpan:
    if not isinstance(payload, dict):
        raise QualityDataError(f"{label} muss ein Objekt sein.")
    allowed = {"entity_type", "start", "end", "score", "source"}
    required = {"entity_type", "start", "end"}
    unknown = set(payload) - allowed
    missing = required - set(payload)
    if unknown or missing:
        raise QualityDataError(
            f"{label}: unbekannte Felder {sorted(unknown)}, fehlende Felder {sorted(missing)}."
        )
    if "score" in payload:
        score = payload["score"]
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
            or not 0 <= float(score) <= 1
        ):
            raise QualityDataError(f"{label}.score muss zwischen 0 und 1 liegen.")
    if "source" in payload:
        _nonempty_string(payload["source"], f"{label}.source")
    return _span(payload, document, label)


def _span(payload: dict[str, Any], document: Document, label: str) -> FindingSpan:
    entity_type = _entity_type(payload["entity_type"], f"{label}.entity_type")
    start = _integer(payload["start"], f"{label}.start", minimum=0)
    end = _integer(payload["end"], f"{label}.end", minimum=1)
    if end <= start or end > len(document.text):
        raise QualityDataError(
            f"{label}: Span [{start}, {end}) liegt ausserhalb des Dokuments "
            f"mit {len(document.text)} Codepoints."
        )
    return FindingSpan(entity_type, start, end)


def _validate_provenance(payload: Any, classification: str) -> None:
    label = "Manifest.provenance"
    if classification == "synthetic":
        _exact_keys(
            payload,
            {"kind", "generator", "description", "contains_real_personal_data"},
            label,
        )
        if payload["kind"] != "synthetic" or payload["contains_real_personal_data"] is not False:
            raise QualityDataError(
                "Synthetische Provenienz muss kind='synthetic' und "
                "contains_real_personal_data=false ausweisen."
            )
        _nonempty_string(payload["generator"], f"{label}.generator")
        _nonempty_string(payload["description"], f"{label}.description")
        return

    _exact_keys(
        payload,
        {
            "kind",
            "source_category",
            "pseudonymization_method",
            "pseudonymized_before_inclusion",
            "privacy_reviewed",
            "contains_reidentification_key",
            "approved_by_role",
            "approval_date",
        },
        label,
    )
    required_flags = (
        payload["kind"] == "real_pseudonymized"
        and payload["pseudonymized_before_inclusion"] is True
        and payload["privacy_reviewed"] is True
        and payload["contains_reidentification_key"] is False
    )
    if not required_flags:
        raise QualityDataError(
            "Reale Korpora brauchen bestaetigte Vorab-Pseudonymisierung und Datenschutzpruefung; "
            "ein Reidentifikationsschluessel ist verboten."
        )
    for field in (
        "source_category",
        "pseudonymization_method",
        "approved_by_role",
    ):
        _nonempty_string(payload[field], f"{label}.{field}")
    raw_date = _nonempty_string(payload["approval_date"], f"{label}.approval_date")
    try:
        approval_date = date.fromisoformat(raw_date)
    except ValueError as error:
        raise QualityDataError(f"{label}.approval_date muss YYYY-MM-DD sein.") from error
    if approval_date > datetime.now(UTC).date():
        raise QualityDataError(f"{label}.approval_date darf nicht in der Zukunft liegen.")


def _file_reference(payload: Any, base: Path, label: str) -> FileReference:
    _exact_keys(payload, {"path", "count", "sha256"}, label)
    return FileReference(
        path=_safe_relative_file(base, payload["path"], f"{label}.path"),
        count=_integer(payload["count"], f"{label}.count", minimum=0),
        sha256=_sha256(payload["sha256"], f"{label}.sha256"),
    )


def _load_string_list(reference: FileReference, label: str) -> tuple[str, ...]:
    _verify_hash(reference.path, reference.sha256)
    payload = _read_json(reference.path, label)
    if not isinstance(payload, list):
        raise QualityDataError(f"{label} muss eine JSON-Liste sein.")
    values = tuple(
        _nonempty_string(value, f"{label}[{index}]") for index, value in enumerate(payload)
    )
    normalized = [value.casefold().strip() for value in values]
    if len(set(normalized)) != len(values):
        raise QualityDataError(f"{label} enthaelt doppelte Werte.")
    if len(values) != reference.count:
        raise QualityDataError(
            f"{label}: Manifest nennt {reference.count}, Datei enthaelt {len(values)} Werte."
        )
    return values


def _iter_jsonl(path: Path):  # type: ignore[no-untyped-def]
    try:
        with path.open("rb") as stream:
            for line_number, raw_line in enumerate(stream, 1):
                if len(raw_line) > _MAX_JSONL_LINE_BYTES:
                    raise QualityDataError(f"{path}:{line_number}: JSONL-Zeile ist zu gross.")
                if not raw_line.strip():
                    raise QualityDataError(f"{path}:{line_number}: Leerzeilen sind verboten.")
                try:
                    payload = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise QualityDataError(
                        f"{path}:{line_number}: ungueltiges UTF-8-JSON."
                    ) from error
                if not isinstance(payload, dict):
                    raise QualityDataError(
                        f"{path}:{line_number}: JSONL-Eintrag muss ein Objekt sein."
                    )
                yield line_number, payload
    except OSError as error:
        raise QualityDataError(f"Datei {path} konnte nicht gelesen werden.") from error


def _read_json(path: Path, label: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QualityDataError(f"{label} ist kein lesbares UTF-8-JSON.") from error


def _verify_hash(path: Path, expected: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise QualityDataError(
            f"SHA-256 fuer {path} stimmt nicht: erwartet {expected}, erhalten {actual}."
        )


def _safe_relative_file(base: Path, value: Any, label: str) -> Path:
    raw = _nonempty_string(value, label)
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise QualityDataError(f"{label} muss ein relativer Pfad ohne '..' sein.")
    base_resolved = base.resolve()
    candidate = (base_resolved / relative).resolve()
    if not candidate.is_relative_to(base_resolved) or not candidate.is_file():
        raise QualityDataError(
            f"{label} verweist nicht auf eine Datei innerhalb von {base_resolved}."
        )
    return candidate


def _exact_keys(payload: Any, expected: set[str], label: str) -> None:
    if not isinstance(payload, dict):
        raise QualityDataError(f"{label} muss ein Objekt sein.")
    unknown = set(payload) - expected
    missing = expected - set(payload)
    if unknown or missing:
        raise QualityDataError(
            f"{label}: unbekannte Felder {sorted(unknown)}, fehlende Felder {sorted(missing)}."
        )


def _schema_version(payload: dict[str, Any], label: str) -> None:
    if payload["schema_version"] != SCHEMA_VERSION:
        raise QualityDataError(
            f"{label}.schema_version muss {SCHEMA_VERSION} sein, ist {payload['schema_version']!r}."
        )


def _identifier(value: Any, label: str) -> str:
    result = _nonempty_string(value, label)
    if not _ID_RE.fullmatch(result):
        raise QualityDataError(f"{label} ist keine sichere kleingeschriebene ID: {result!r}.")
    return result


def _entity_type(value: Any, label: str) -> str:
    result = _nonempty_string(value, label).upper()
    result = _ENTITY_ALIASES.get(result, result)
    if not _ENTITY_RE.fullmatch(result):
        raise QualityDataError(f"{label} ist kein gueltiger Entitaetstyp: {result!r}.")
    return result


def _classification(value: Any, label: str) -> Literal["synthetic", "real_pseudonymized"]:
    result = _nonempty_string(value, label)
    if result not in CLASSIFICATIONS:
        raise QualityDataError(f"{label} muss einer von {sorted(CLASSIFICATIONS)} sein.")
    return result  # type: ignore[return-value]


def _sha256(value: Any, label: str) -> str:
    result = _nonempty_string(value, label)
    if not _SHA256_RE.fullmatch(result):
        raise QualityDataError(f"{label} muss ein kleingeschriebener SHA-256 sein.")
    return result


def _integer(value: Any, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise QualityDataError(f"{label} muss eine Ganzzahl >= {minimum} sein.")
    return value


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QualityDataError(f"{label} muss eine nicht-leere Zeichenkette sein.")
    return value.strip()
