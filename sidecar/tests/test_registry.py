"""Stufe 0: getrennte Stammdatenlisten als sichere Presidio-Deny-Lists."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from presidio_analyzer import PatternRecognizer

from presidio_sidecar.registry import (
    DENY_LIST_SCORE,
    ORGANIZATION_ENTITY,
    PERSON_ENTITY,
    MasterDataRegistry,
    NormalizedDenyListRecognizer,
    RegistryImportError,
    RegistrySources,
    RegistryStorageError,
    normalize_registry_value,
)
from presidio_sidecar.registry_crypto import generate_registry_key

IMPORTED_AT = datetime(2026, 7, 30, 3, 0, tzinfo=UTC)
SOURCE_UPDATED_AT = datetime(2026, 7, 29, 2, 0, tzinfo=UTC)


def _write_sources(
    directory: Path,
    *,
    persons: str = "name\nJosef Brandstetter\nAl\nJOSEF BRANDSTETTER\n",
    organizations: object = ("Tischlerei Josef Brandstetter e.U.", "Acme GmbH"),
) -> RegistrySources:
    persons_path = directory / "personen.csv"
    organizations_path = directory / "firmen.json"
    persons_path.write_text(persons, encoding="utf-8")
    organizations_path.write_text(
        json.dumps({"organizations": list(organizations)}, ensure_ascii=False),
        encoding="utf-8",
    )
    source_timestamp = SOURCE_UPDATED_AT.timestamp()
    os.utime(persons_path, (source_timestamp, source_timestamp))
    os.utime(organizations_path, (source_timestamp, source_timestamp))
    return RegistrySources(persons=persons_path, organizations=organizations_path)


def _registry(
    directory: Path,
    *,
    key: bytes | None = None,
    nightly_sources: RegistrySources | None = None,
) -> MasterDataRegistry:
    return MasterDataRegistry(
        directory / "registry.enc",
        key or generate_registry_key(),
        nightly_sources=nightly_sources,
        clock=lambda: IMPORTED_AT,
    )


def _recognizer(
    registry: MasterDataRegistry,
    entity: str,
) -> PatternRecognizer:
    return next(
        recognizer
        for recognizer in registry.recognizers()
        if recognizer.supported_entities == [entity]
    )


def test_csv_json_import_stays_separated_by_entity(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    status = registry.reimport(_write_sources(tmp_path))

    assert status.as_dict() == {
        "persons_count": 1,
        "organizations_count": 2,
        "total_count": 3,
        "updated_at": "2026-07-29T02:00:00+00:00",
        "imported_at": "2026-07-30T03:00:00+00:00",
    }

    person = _recognizer(registry, PERSON_ENTITY)
    organization = _recognizer(registry, ORGANIZATION_ENTITY)
    assert isinstance(person, PatternRecognizer)
    assert person.deny_list == ["josef brandstetter"]
    assert "tischlerei josef brandstetter e.u." not in person.deny_list
    assert "tischlerei josef brandstetter e.u." in organization.deny_list
    assert all(pattern.score == DENY_LIST_SCORE for pattern in person.patterns)
    assert all(pattern.score == DENY_LIST_SCORE for pattern in organization.patterns)


def test_deny_list_finds_name_in_company_name_with_score_one(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.reimport(_write_sources(tmp_path))
    recognizer = _recognizer(registry, PERSON_ENTITY)
    text = "Rechnung der Tischlerei JOSEF BRANDSTETTER e.U."

    results = recognizer.analyze(text, entities=[PERSON_ENTITY])

    assert len(results) == 1
    result = results[0]
    assert text[result.start : result.end] == "JOSEF BRANDSTETTER"
    assert result.entity_type == PERSON_ENTITY
    assert result.score == 1.0
    assert result.recognition_metadata["source"] == "master_data"


def test_nfkc_casefold_match_keeps_original_offsets() -> None:
    recognizer = NormalizedDenyListRecognizer(
        supported_entity=PERSON_ENTITY,
        deny_list=["Straße Café"],
        name="test",
    )
    # Fullwidth ASCII is normalized via NFKC; the decomposed é checks that
    # the back-calculation covers the longer original span.
    text = "Vor ＳＴＲＡＳＳＥ CAFE\u0301 nach"

    results = recognizer.analyze(text, entities=[PERSON_ENTITY])

    assert len(results) == 1
    assert text[results[0].start : results[0].end] == "ＳＴＲＡＳＳＥ CAFE\u0301"


def test_nfkc_and_casefold_normalization() -> None:
    assert normalize_registry_value("  ＨＡＮＳ Straße  ") == "hans strasse"


def test_minimum_length_three_is_enforced(tmp_path: Path) -> None:
    sources = _write_sources(
        tmp_path,
        persons="name\nA\nÖl\nMax\n",
        organizations=("AG", "ÖBB"),
    )
    registry = _registry(tmp_path)

    status = registry.reimport(sources)

    assert status.persons_count == 1
    assert status.organizations_count == 1
    assert _recognizer(registry, PERSON_ENTITY).deny_list == ["max"]
    assert _recognizer(registry, ORGANIZATION_ENTITY).deny_list == ["öbb"]
    with pytest.raises(ValueError):
        MasterDataRegistry(tmp_path / "other.enc", generate_registry_key(), min_length=2)


def test_registry_file_is_encrypted_and_created_with_0600_permissions(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    registry.reimport(_write_sources(tmp_path))

    payload = (tmp_path / "registry.enc").read_bytes()
    assert b"Josef" not in payload
    assert b"Tischlerei" not in payload
    assert (tmp_path / "registry.enc").stat().st_mode & 0o777 == 0o600


def test_encrypted_state_is_loaded_after_restart(tmp_path: Path) -> None:
    key = generate_registry_key()
    first = _registry(tmp_path, key=key)
    first.reimport(_write_sources(tmp_path))

    loaded = _registry(tmp_path, key=key)

    assert loaded.status() == first.status()
    assert _recognizer(loaded, PERSON_ENTITY).deny_list == ["josef brandstetter"]


def test_wrong_key_does_not_start_with_empty_registry(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.reimport(_write_sources(tmp_path))

    with pytest.raises(RegistryStorageError):
        _registry(tmp_path, key=generate_registry_key())


def test_failed_reimport_keeps_previous_state(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    valid_sources = _write_sources(tmp_path)
    old_status = registry.reimport(valid_sources)
    old_payload = (tmp_path / "registry.enc").read_bytes()

    invalid_orgs = tmp_path / "ungueltig.json"
    invalid_orgs.write_text("{kaputt", encoding="utf-8")
    with pytest.raises(RegistryImportError):
        registry.manual_reimport(
            persons_path=valid_sources.persons,
            organizations_path=invalid_orgs,
        )

    assert registry.status() == old_status
    assert (tmp_path / "registry.enc").read_bytes() == old_payload


def test_configured_nightly_path_and_manual_reimport(tmp_path: Path) -> None:
    nightly_directory = tmp_path / "nightly"
    nightly_directory.mkdir()
    nightly = _write_sources(
        nightly_directory,
        persons="name\nNightly Person\n",
        organizations=("Nightly GmbH",),
    )
    registry = _registry(tmp_path, nightly_sources=nightly)

    registry.reimport_nightly()
    assert _recognizer(registry, PERSON_ENTITY).deny_list == ["nightly person"]

    manual_directory = tmp_path / "manual"
    manual_directory.mkdir()
    manual = _write_sources(
        manual_directory,
        persons="name\nManual Person\n",
        organizations=("Manual GmbH",),
    )
    registry.manual_reimport(
        persons_path=manual.persons,
        organizations_path=manual.organizations,
    )
    assert _recognizer(registry, PERSON_ENTITY).deny_list == ["manual person"]

    registry.reimport_nightly()
    assert _recognizer(registry, PERSON_ENTITY).deny_list == ["nightly person"]


def test_nightly_reimport_without_configuration_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RegistryImportError, match="naechtlichen"):
        _registry(tmp_path).reimport_nightly()


def test_one_json_file_can_carry_both_separated_lists(tmp_path: Path) -> None:
    combined = tmp_path / "master-data.json"
    combined.write_text(
        json.dumps(
            {
                "persons": [{"name": "Ada Lovelace"}],
                "organizations": [{"company_name": "Analytical Engines Ltd."}],
            }
        ),
        encoding="utf-8",
    )
    registry = _registry(tmp_path)

    status = registry.manual_reimport(
        persons_path=combined,
        organizations_path=combined,
    )

    assert status.total_count == 2
    assert _recognizer(registry, PERSON_ENTITY).deny_list == ["ada lovelace"]
    assert _recognizer(registry, ORGANIZATION_ENTITY).deny_list == ["analytical engines ltd."]


def test_status_distinguishes_source_state_from_import_time(tmp_path: Path) -> None:
    combined = tmp_path / "master-data.json"
    combined.write_text(
        json.dumps(
            {
                "persons": ["Ada Lovelace"],
                "organizations": ["Analytical Engines Ltd."],
            }
        ),
        encoding="utf-8",
    )
    source_timestamp = datetime(2026, 6, 1, 3, 0, tzinfo=UTC).timestamp()
    os.utime(combined, (source_timestamp, source_timestamp))
    registry = _registry(tmp_path)

    status = registry.manual_reimport(
        persons_path=combined,
        organizations_path=combined,
    )

    assert status.updated_at == datetime(2026, 6, 1, 3, 0, tzinfo=UTC)
    assert status.imported_at == IMPORTED_AT


def test_changed_combined_source_is_atomically_rejected_and_read_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(tmp_path)
    previous = registry.reimport(_write_sources(tmp_path))
    combined = tmp_path / "master-data.json"
    combined.write_text(
        json.dumps(
            {
                "persons": ["Alter Personenstand"],
                "organizations": ["Alte Organisation"],
            }
        ),
        encoding="utf-8",
    )
    original_json_load = json.load
    load_calls = 0

    def replace_after_read(stream: object) -> object:
        nonlocal load_calls
        load_calls += 1
        payload = original_json_load(stream)  # type: ignore[arg-type]
        combined.write_text(
            json.dumps(
                {
                    "persons": ["Neuer Personenstand"],
                    "organizations": ["Neue und viel längere Organisation GmbH"],
                }
            ),
            encoding="utf-8",
        )
        return payload

    monkeypatch.setattr(
        "presidio_sidecar.registry.json.load",
        replace_after_read,
    )

    with pytest.raises(RegistryImportError, match="waehrend"):
        registry.manual_reimport(
            persons_path=combined,
            organizations_path=combined,
        )

    assert load_calls == 1
    assert registry.status() == previous


@pytest.mark.parametrize(
    "payload",
    [
        ["Meier Spielwaren GmbH"],
        {"names": ["Meier Spielwaren GmbH"]},
        {"persons": ["Ada Lovelace"], "names": ["Meier Spielwaren GmbH"]},
    ],
)
def test_shared_json_source_needs_two_explicit_containers(
    tmp_path: Path,
    payload: object,
) -> None:
    combined = tmp_path / "master-data.json"
    combined.write_text(json.dumps(payload), encoding="utf-8")
    registry = _registry(tmp_path)
    previous = registry.reimport(_write_sources(tmp_path))

    with pytest.raises(RegistryImportError, match="getrennte"):
        registry.manual_reimport(
            persons_path=combined,
            organizations_path=combined,
        )

    assert registry.status() == previous
    assert _recognizer(registry, PERSON_ENTITY).deny_list == ["josef brandstetter"]
    assert _recognizer(registry, ORGANIZATION_ENTITY).deny_list == [
        "tischlerei josef brandstetter e.u.",
        "acme gmbh",
    ]


def test_semicolon_csv_with_multiple_columns(tmp_path: Path) -> None:
    persons = tmp_path / "personen.csv"
    persons.write_text("id;personenname;aktiv\n1;Erika Musterfrau;ja\n", encoding="utf-8")
    organizations = tmp_path / "firmen.csv"
    organizations.write_text("id;firmenname\n1;Musterfirma GmbH\n", encoding="utf-8")
    registry = _registry(tmp_path)

    registry.manual_reimport(
        persons_path=persons,
        organizations_path=organizations,
    )

    assert _recognizer(registry, PERSON_ENTITY).deny_list == ["erika musterfrau"]
    assert _recognizer(registry, ORGANIZATION_ENTITY).deny_list == ["musterfirma gmbh"]


def test_empty_registry_returns_no_unusable_recognizers(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    assert registry.status().total_count == 0
    assert registry.recognizers() == ()
