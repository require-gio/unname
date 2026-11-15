"""translation table, names & terms, templates, retention periods, model inventory."""

# The ``api`` fixture is imported from ``test_api_v2``; as a parameter it hides the import.
# ruff: noqa: F811

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from presidio_sidecar import templates, vault_backup, vocabulary
from presidio_sidecar.analysis import PlainTextAnalyzer
from presidio_sidecar.base_profile import base_profile
from presidio_sidecar.config import Settings
from presidio_sidecar.detection import DetectionPipeline, FindingSource
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.masking import MaskingSpec
from presidio_sidecar.profiles import load_profiles
from presidio_sidecar.registry import MasterDataRegistry
from presidio_sidecar.registry_crypto import generate_registry_key
from presidio_sidecar.services import PhaseOneServices
from presidio_sidecar.vault import TokenVault
from presidio_sidecar.vocabulary import (
    CustomPatternRecognizer,
    Pattern,
    PatternIn,
    PatternTimeoutError,
    TermIn,
    VocabularyStore,
    VocabularyValidationError,
    compile_pattern,
    parse_name_list,
)
from presidio_sidecar.workers import WorkerCapacity
from test_api_v2 import AUTH, REVERSIBLE, Harness, api  # noqa: F401

PASSWORD = "ein langes Passwort"


# -- Vault: stats, clearing, retention -----------------------------------------


def _vault(path: Path) -> TokenVault:
    return TokenVault(path)


def test_stats_report_mappings_and_entries(tmp_path: Path) -> None:
    vault = _vault(tmp_path / "state")
    try:
        vault.commit([vault.prepare_token("PERSON", "Sabine Hofer")], entry_id="e1")
        vault.commit(
            [vault.prepare_token("PERSON", "Hofer"), vault.prepare_token("LOCATION", "Wien")],
            entry_id="e2",
        )
        status = vault.status()
        assert (status.mapping_count, status.entry_count) == (3, 2)
        assert status.as_dict()["entry_count"] == 2
    finally:
        vault.close()


def test_clear_requires_confirmation_and_keeps_key(tmp_path: Path) -> None:
    vault = _vault(tmp_path / "state")
    try:
        pending = vault.prepare_token("PERSON", "Sabine Hofer")
        vault.commit([pending], entry_id="e1")
        fingerprint = vault.status().fingerprint
        with pytest.raises(ValueError):
            vault.clear(confirm=False)
        assert vault.status().mapping_count == 1

        status = vault.clear(confirm=True)

        assert (status.mapping_count, status.entry_count) == (0, 0)
        assert status.fingerprint == fingerprint
        assert vault.lookup({pending.token}) == {}
        # The same name continues to receive the same code.
        assert vault.prepare_token("PERSON", "Sabine Hofer").token == pending.token
    finally:
        vault.close()


def test_retention_deletes_only_long_unused(tmp_path: Path) -> None:
    vault = _vault(tmp_path / "state")
    now = datetime.now(UTC)
    try:
        alt = vault.prepare_token("PERSON", "Alt Bekannt")
        new = vault.prepare_token("PERSON", "Neu Bekannt")
        vault.commit([alt], entry_id="e1", used_at=now - timedelta(days=400))
        vault.commit([new], entry_id="e2", used_at=now - timedelta(days=10))
        gebraucht = vault.prepare_token("PERSON", "Oft Gebraucht")
        vault.commit([gebraucht], entry_id="e3", used_at=now - timedelta(days=400))
        vault.touch({gebraucht.token}, used_at=now - timedelta(days=1))

        removed = vault.purge_unused(older_than=now - timedelta(days=365))

        assert removed == 1
        assert set(vault.lookup({alt.token, new.token, gebraucht.token})) == {
            new.token,
            gebraucht.token,
        }
        assert vault.status().entry_count == 2
    finally:
        vault.close()


# -- Vault: backup -----------------------------------------------------------


def test_backup_includes_table_and_key(tmp_path: Path) -> None:
    source_vault = _vault(tmp_path / "quelle")
    target_vault = _vault(tmp_path / "ziel")
    try:
        pending = source_vault.prepare_token("PERSON", "Sabine Hofer")
        source_vault.commit([pending], entry_id="e1")
        surrogate = source_vault.prepare_surrogate("PERSON", "Renate Wimmer")
        source_vault.commit([surrogate], entry_id="e1")
        payload = source_vault.export_backup(PASSWORD)
        assert b"Sabine" not in payload and b"Hofer" not in payload
        target_vault.commit([target_vault.prepare_token("PERSON", "Nur Hier")], entry_id="x")
        with pytest.raises(ValueError):
            target_vault.import_backup(payload, PASSWORD, confirm=False)

        status = target_vault.import_backup(payload, PASSWORD, confirm=True)

        assert status.fingerprint == source_vault.status().fingerprint
        assert (status.mapping_count, status.entry_count) == (2, 1)
        record = target_vault.lookup({pending.token})[pending.token]
        assert record.canonical == "Sabine Hofer"
        assert record.origins[0][0] == "e1"
        # Same key: the new device generates the identical token.
        assert target_vault.prepare_token("PERSON", "Sabine Hofer").token == pending.token
        assert target_vault.surrogate_index()
    finally:
        source_vault.close()
        target_vault.close()
    # After a restart the target still reads its table.
    reopened = _vault(tmp_path / "ziel")
    try:
        assert reopened.status().mapping_count == 2
    finally:
        reopened.close()


def test_wrong_password_and_modified_file_are_rejected(tmp_path: Path) -> None:
    vault = _vault(tmp_path / "state")
    try:
        vault.commit([vault.prepare_token("PERSON", "Sabine Hofer")], entry_id="e1")
        payload = vault.export_backup(PASSWORD)
        fingerprint = vault.status().fingerprint
        with pytest.raises(vault_backup.VaultBackupError) as wrong:
            vault.import_backup(payload, "ein anderes Passwort", confirm=True)
        assert wrong.value.code == "wrong_password"
        corrupted = bytearray(payload)
        corrupted[-5] ^= 0x01
        with pytest.raises(vault_backup.VaultBackupError) as modified:
            vault.import_backup(bytes(corrupted), PASSWORD, confirm=True)
        assert modified.value.code == "wrong_password"
        with pytest.raises(vault_backup.VaultBackupError) as foreign:
            vault.import_backup(b"PK\x03\x04" + payload, PASSWORD, confirm=True)
        assert foreign.value.code == "invalid_backup"
        with pytest.raises(vault_backup.VaultBackupError) as short:
            vault.export_backup("kurz")
        assert short.value.code == "password_too_short"
        # None of this altered the vault.
        assert vault.status().fingerprint == fingerprint
        assert vault.status().mapping_count == 1
    finally:
        vault.close()


def test_backup_with_foreign_scrypt_params_not_opened() -> None:
    payload = bytearray(vault_backup.seal(b"\x01" * 32, b"db", PASSWORD))
    # log2(N) set to 30: otherwise an attacker could request gigabytes.
    payload[len(vault_backup.HEADER) + vault_backup.SALT_SIZE] = 30
    with pytest.raises(vault_backup.VaultBackupError) as error:
        vault_backup.open_backup(bytes(payload), PASSWORD)
    assert error.value.code == "invalid_backup"


def test_vault_api_clear_backup_import(api: Harness) -> None:
    vault = api.services.vault()
    pending = vault.prepare_token("PERSON", "Sabine Hofer")
    vault.commit([pending], entry_id="e1")

    status = api.client.get("/vault/status", headers=AUTH).json()
    assert status["mapping_count"] == 1 and status["entry_count"] == 1
    assert status["state"] == "ready" and status["created_at"]

    backup = api.client.post("/vault/backup", headers=AUTH, json={"password": PASSWORD})
    assert backup.status_code == 200
    assert backup.headers["content-type"] == "application/octet-stream"
    short = api.client.post("/vault/backup", headers=AUTH, json={"password": "kurz"})
    assert short.status_code == 422 and short.json()["detail"]["code"] == "password_too_short"

    unconfirmed = api.client.post("/vault/clear", headers=AUTH, json={})
    assert unconfirmed.status_code == 422
    cleared = api.client.post("/vault/clear", headers=AUTH, json={"confirm": True})
    assert cleared.status_code == 200 and cleared.json()["mapping_count"] == 0

    encoded = base64.b64encode(backup.content).decode()
    without = api.client.post(
        "/vault/import", headers=AUTH, json={"password": PASSWORD, "backup": encoded}
    )
    assert without.status_code == 422
    wrong = api.client.post(
        "/vault/import",
        headers=AUTH,
        json={"password": "falsches Passwort", "backup": encoded, "confirm": True},
    )
    assert wrong.status_code == 422 and wrong.json()["detail"]["code"] == "wrong_password"
    imported_response = api.client.post(
        "/vault/import",
        headers=AUTH,
        json={"password": PASSWORD, "backup": encoded, "confirm": True},
    )
    assert imported_response.status_code == 200, imported_response.text
    assert imported_response.json()["mapping_count"] == 1
    assert vault.lookup({pending.token})[pending.token].canonical == "Sabine Hofer"


# -- Retention periods take effect immediately -----------------------------------------------------


def test_shorter_retention_cleans_up_immediately(api: Harness) -> None:
    services = api.services
    store = services.entries()
    entry = store.create(
        kind="text", masking=MaskingSpec.model_validate(REVERSIBLE), title="Text", status="copied"
    )
    alt = datetime.now(UTC) - timedelta(days=40)
    store._entries[entry.id] = entry.model_copy(update={"updated_at": alt})  # noqa: SLF001
    vault = services.vault()
    vault.commit(
        [vault.prepare_token("PERSON", "Alt Bekannt")],
        entry_id=entry.id,
        used_at=datetime.now(UTC) - timedelta(days=200),
    )

    response = api.client.put(
        "/settings",
        headers=AUTH,
        json={"history": {"retention_days": 30}, "vault": {"unused_retention_months": 6}},
    )

    assert response.status_code == 200, response.text
    assert store.list() == []
    assert vault.status().mapping_count == 0


# -- Patterns and Terms --------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("POL-(", "pattern_invalid"),
        (r"\d*", "pattern_empty"),
        (r"(a+)+$", "pattern_slow"),
        (r"\w+", "pattern_broad"),
        (r"[\s\S]+", "pattern_broad"),
    ],
)
def test_unsuitable_patterns_are_rejected(source: str, code: str) -> None:
    with pytest.raises(VocabularyValidationError) as error:
        compile_pattern(source, index=3)
    assert error.value.code == code
    assert error.value.index == 3


def test_fixed_formats_pass_check() -> None:
    for source in (r"POL-\d{6,8}", r"SCH-\d{4}-\d{5}", r"\bAZ\s?\d{1,4}/\d{2}\b"):
        compile_pattern(source)


def test_pattern_with_timeout_causes_detection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vocabulary, "PATTERN_TIMEOUT_SECONDS", 0.01)
    recognizer = CustomPatternRecognizer(
        Pattern(id="p", name="schlecht", regex=r"(a+)+$", entity_type="OTHER")
    )
    with pytest.raises(PatternTimeoutError):
        recognizer.analyze("a" * 3000 + "!", entities=["OTHER"])


def test_name_lists_from_csv_json_and_text() -> None:
    csv = b"Name;Ort\nSabine Hofer;Wien\nJosef Brandstetter;Graz\nAl;x\n"
    assert parse_name_list("kunden.csv", csv, "persons") == ("Sabine Hofer", "Josef Brandstetter")
    data = json.dumps({"firmen": ["Holzbau Gruber", "Holzbau Gruber"]}).encode()
    assert parse_name_list("lieferanten.json", data, "organizations") == ("Holzbau Gruber",)
    text = "﻿Renate  Wimmer\n\n Petra Novak \n".encode()
    assert parse_name_list("liste.txt", text, "persons") == ("Renate Wimmer", "Petra Novak")
    for name, content, code in (
        ("liste.xlsx", b"x", "list_unsupported"),
        ("leer.txt", b"\n \n", "list_empty"),
        ("kaputt.txt", b"\xff\xfe\x00", "list_encoding"),
    ):
        with pytest.raises(VocabularyValidationError) as error:
            parse_name_list(name, content, "persons")
        assert error.value.code == code


def test_store_is_encrypted_and_survives_restart(tmp_path: Path) -> None:
    store = VocabularyStore(tmp_path / "vocabulary.enc", tmp_path / "vocabulary.key")
    store.add_list(kind="persons", source_name="/geheim/kunden.csv", values=["Sabine Hofer"])
    store.set_terms([TermIn(text="Projekt  Falke", entity_type="OTHER")])
    store.set_patterns([PatternIn(name="Polizze", regex=r"POL-\d{6,8}")])
    raw = (tmp_path / "vocabulary.enc").read_bytes()
    assert b"Hofer" not in raw and b"Falke" not in raw and b"POL-" not in raw

    again = VocabularyStore(tmp_path / "vocabulary.enc", tmp_path / "vocabulary.key")

    (name_list,) = again.lists()
    assert name_list.source_name == "kunden.csv"
    assert name_list.values == ("Sabine Hofer",)
    assert [term.text for term in again.terms()] == ["Projekt Falke"]
    assert again.patterns()[0].regex == r"POL-\d{6,8}"


def test_terms_keep_id_and_are_at_least_three_chars(tmp_path: Path) -> None:
    store = VocabularyStore(tmp_path / "v.enc", tmp_path / "v.key")
    (first,) = store.set_terms([TermIn(text="Projekt Falke", entity_type="OTHER")])
    terms = store.set_terms(
        [
            TermIn(text="projekt falke", entity_type="OTHER"),
            TermIn(text="Projekt Falke", entity_type="OTHER"),
            TermIn(text="Hofer & Partner", entity_type="ORGANIZATION"),
        ]
    )
    assert [term.text for term in terms] == ["projekt falke", "Hofer & Partner"]
    assert terms[0].id == first.id
    with pytest.raises(VocabularyValidationError) as error:
        store.set_terms([TermIn(text="Ab", entity_type="OTHER")])
    assert error.value.code == "term_too_short"


# -- Detection ----------------------------------------------------------------


class EmptyNer:
    detector_name = "test-empty"

    def detect(self, text: str):  # noqa: ANN201
        return ()


def test_lists_terms_and_patterns_affect_detection(tmp_path: Path) -> None:
    settings = Settings(token="t", state_directory=tmp_path / "state")
    registry = MasterDataRegistry(tmp_path / "registry.enc", generate_registry_key())
    services = PhaseOneServices(
        settings,
        DiagnosticsRecorder(),
        registry=registry,
        analyzer=PlainTextAnalyzer(registry, semantic_pipeline=DetectionPipeline(ner=EmptyNer())),
        profiles=load_profiles(),
        capacity=WorkerCapacity(1, 0, 1, 8 * 1024**3),
    )
    store = services.vocabulary()
    store.add_list(kind="organizations", source_name="lieferanten.csv", values=["Holzbau Gruber"])
    store.set_terms([TermIn(text="Projekt Falke", entity_type="OTHER")])
    store.set_patterns(
        [
            PatternIn(name="Polizze", regex=r"POL-\d{6,8}", entity_type="CUSTOMER_NUMBER"),
            PatternIn(name="aus", regex=r"SCH-\d{4}-\d{5}", enabled=False),
        ]
    )
    text = "Schaden SCH-2026-00417 zur Polizze POL-4471920 (Projekt Falke, Holzbau Gruber)."

    result = services.analyzer_for_profile(base_profile()).analyze(text, progress=lambda _: None)

    found = {(finding.text, finding.entity_type, finding.source) for finding in result.confirmed}
    assert ("POL-4471920", "CUSTOMER_NUMBER", FindingSource.RULE) in found
    assert ("Projekt Falke", "OTHER", FindingSource.MASTER_DATA) in found
    assert ("Holzbau Gruber", "ORGANIZATION", FindingSource.MASTER_DATA) in found
    assert not any("SCH-2026" in (finding.text or "") for finding in result.confirmed)
    services.close()


# -- API: lists, terms, patterns ---------------------------------------------


def test_list_api_create_update_remove(api: Harness) -> None:
    alt = int((datetime.now(UTC) - timedelta(days=9)).timestamp() * 1000)
    created = api.client.post(
        f"/lists?kind=persons&modified={alt}",
        headers=AUTH,
        files=[("file", ("kunden.csv", b"Name\nSabine Hofer\nJosef Brandstetter\n", "text/csv"))],
    )
    assert created.status_code == 201, created.text
    (name_list,) = created.json()["lists"]
    assert (name_list["source_name"], name_list["count"], name_list["stale"]) == (
        "kunden.csv",
        2,
        True,
    )
    assert "values" not in name_list and created.json()["configured"] is None
    assert created.json()["created"] == name_list["id"]

    refreshed = api.client.post(
        f"/lists/{name_list['id']}/refresh",
        headers=AUTH,
        files=[("file", ("kunden.csv", b"Name\nSabine Hofer\n", "text/csv"))],
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["lists"][0]["count"] == 1
    assert refreshed.json()["lists"][0]["stale"] is False

    wrong = api.client.post(
        "/lists?kind=animals", headers=AUTH, files=[("file", ("x.txt", b"Bello", "text/plain"))]
    )
    assert wrong.status_code == 422
    no_name = api.client.post(
        "/lists?kind=persons", headers=AUTH, files=[("file", ("x.pdf", b"%PDF", "x"))]
    )
    assert no_name.status_code == 422
    assert no_name.json()["detail"]["code"] == "list_unsupported"
    assert api.client.post("/lists?kind=persons", headers=AUTH, json={}).status_code == 415

    assert api.client.delete(f"/lists/{name_list['id']}", headers=AUTH).status_code == 204
    assert api.client.get("/lists", headers=AUTH).json()["lists"] == []
    assert api.client.delete(f"/lists/{name_list['id']}", headers=AUTH).status_code == 404


def test_terms_and_patterns_api_try_out(api: Harness) -> None:
    terms = api.client.put(
        "/terms",
        headers=AUTH,
        json={"terms": [{"text": "Projekt Falke", "entity_type": "OTHER"}]},
    )
    assert terms.status_code == 200, terms.text
    assert terms.json()["terms"][0]["text"] == "Projekt Falke"

    invalid = api.client.put(
        "/patterns",
        headers=AUTH,
        json={
            "patterns": [
                {"name": "Polizze", "regex": r"POL-\d{6,8}"},
                {"name": "Alles", "regex": r"\w+"},
            ]
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == {
        "code": "pattern_broad",
        "message": "Das Muster trifft fast jeden Text.",
        "index": 1,
    }
    saved = api.client.put(
        "/patterns",
        headers=AUTH,
        json={
            "patterns": [
                {"name": "Polizze", "regex": r"POL-\d{6,8}", "entity_type": "CUSTOMER_NUMBER"},
                {"name": "Schaden", "regex": r"SCH-\d{4}-\d{5}", "entity_type": "CASE_NUMBER"},
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    ids = [pattern["id"] for pattern in saved.json()["patterns"]]
    again = api.client.put(
        "/patterns",
        headers=AUTH,
        json={
            "patterns": [
                {**pattern, "enabled": pattern["name"] != "Schaden"}
                for pattern in saved.json()["patterns"]
            ]
        },
    )
    assert [pattern["id"] for pattern in again.json()["patterns"]] == ids

    text = "Schaden SCH-2026-00417 zur Polizze POL-4471920, besprochen im Projekt Falke. 😀"
    test = api.client.post("/patterns/test", headers=AUTH, json={"text": text})
    assert test.status_code == 200, test.text
    hits = test.json()["hits"]
    assert [(hit["label"], hit["source"]) for hit in hits] == [
        ("[Kundennummer]", "pattern"),
        ("[Angabe]", "term"),
    ]
    utf16 = text.encode("utf-16-le")
    assert utf16[hits[0]["start"] * 2 : hits[0]["end"] * 2].decode("utf-16-le") == "POL-4471920"

    draft = api.client.post(
        "/patterns/test",
        headers=AUTH,
        json={"text": text, "patterns": [{"name": "neu", "regex": r"SCH-\d{4}-\d{5}"}]},
    )
    assert [hit["source"] for hit in draft.json()["hits"]] == ["pattern", "term"]
    assert draft.json()["hits"][0]["label"] == "[Kundennummer]"
    # The draft has not been saved.
    stored = api.client.get("/patterns", headers=AUTH).json()["patterns"]
    assert [pattern["name"] for pattern in stored] == ["Polizze", "Schaden"]


# -- Templates -----------------------------------------------------------------


def test_example_templates_from_profiles() -> None:
    examples = {item.title: item for item in templates.example_templates(load_profiles())}
    assert set(examples) == {
        "Kanzlei und Beratung (DE/AT)",
        "LLM-Upload",
        "Rechnung und Beleg (DE/AT)",
        "Versicherung",
    }
    llm = examples["LLM-Upload"].masking
    assert llm is not None and llm.method == "reversible"
    assert len(llm.categories) == len(templates.CATEGORIES)
    invoice = examples["Rechnung und Beleg (DE/AT)"].masking
    assert invoice is not None and invoice.method == "irreversible"
    assert invoice.style == "placeholder"
    assert [pattern.id for pattern in examples["Versicherung"].patterns] == ["pol", "sch"]


def test_template_renders_and_parses_back() -> None:
    (insurance,) = [
        item for item in templates.example_templates(()) if item.title == "Versicherung"
    ]
    text = templates.render(insurance)
    data = yaml.safe_load(text)
    assert (data["format"], data["version"]) == ("unname-vorlage", 4)
    parsed = templates.parse(text)
    assert [(item.name, item.regex) for item in parsed.patterns] == [
        (item.name, item.regex) for item in insurance.patterns
    ]
    for bad, code in (
        ("format: unname-vorlage\nversion: 3\ntitle: x\n", "template_version"),
        ("- nur eine Liste", "template_invalid"),
        ("format: unname-vorlage\nversion: 4\ntitle: x\nunbekannt: 1\n", "template_invalid"),
        (
            "format: unname-vorlage\nversion: 4\ntitle: x\npatterns:\n"
            "  - {name: alles, regex: '.+'}\n",
            "pattern_broad",
        ),
        ("{{{", "template_invalid"),
    ):
        with pytest.raises(VocabularyValidationError) as error:
            templates.parse(bad)
        assert error.value.code == code


def test_templates_api_save_export_import_select(api: Harness) -> None:
    listing = api.client.get("/templates", headers=AUTH).json()
    assert listing["active"] is None
    insurance = next(item for item in listing["templates"] if item["title"] == "Versicherung")
    assert insurance["example"] is True and insurance["patterns"] == 2

    chosen = api.client.post(f"/templates/{insurance['id']}/apply", headers=AUTH)
    assert chosen.status_code == 200, chosen.text
    body = chosen.json()
    assert body["templates"]["active"] == insurance["id"]
    assert [pattern["name"] for pattern in body["patterns"]] == [
        "Polizzennummer (Beispiel)",
        "Schadennummer (Beispiel)",
    ]
    assert body["settings"]["names_terms"]["template"] == insurance["id"]

    api.client.put(
        "/terms", headers=AUTH, json={"terms": [{"text": "Projekt Falke", "entity_type": "OTHER"}]}
    )
    example = api.client.post("/templates", headers=AUTH, json={"title": "versicherung"})
    assert example.status_code == 422
    custom = api.client.post("/templates", headers=AUTH, json={"title": "Team Kfz"})
    assert custom.status_code == 201, custom.text
    team = next(item for item in custom.json()["templates"] if item["title"] == "Team Kfz")
    assert (team["example"], team["terms"], team["patterns"]) == (False, 1, 2)
    assert custom.json()["active"] == team["id"]

    export = api.client.post("/templates/export", headers=AUTH, json={"id": team["id"]})
    assert export.status_code == 200
    assert export.json()["name"] == "Team Kfz.unname-vorlage.yaml"
    content = export.json()["content"]
    assert "Projekt Falke" in content and "masking:" in content

    # On a different state: clear and reload.
    api.client.put("/terms", headers=AUTH, json={"terms": []})
    api.client.put("/patterns", headers=AUTH, json={"patterns": []})
    imported = api.client.post(
        "/templates/import",
        headers=AUTH,
        json={"content": content.replace("Team Kfz", "Team Kfz (Kopie)")},
    )
    assert imported.status_code == 201, imported.text
    assert [term["text"] for term in imported.json()["terms"]] == ["Projekt Falke"]
    assert len(imported.json()["patterns"]) == 2
    titles = [item["title"] for item in imported.json()["templates"]["templates"]]
    assert "Team Kfz (Kopie)" in titles

    current = api.client.post("/templates/export", headers=AUTH, json={"title": "Stand"})
    assert yaml.safe_load(current.json()["content"])["title"] == "Stand"
    unknown = api.client.post("/templates/nope/apply", headers=AUTH)
    assert unknown.status_code == 404
    broken = api.client.post("/templates/import", headers=AUTH, json={"content": "{{{"})
    assert broken.status_code == 422


# -- Models & Storage --------------------------------------------------------


def test_model_inventory_lists_all_components(api: Harness) -> None:
    # Since phases I, J, and K: components as listed in the shell manifest.
    response = api.client.get("/models", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["components"]] == ["ner", "de", "en", "ocr", "classifier"]
    assert all(item["state"] in {"ready", "missing"} for item in body["components"])
    # ``system``: OCR with Apple Vision (phase U).
    assert all(
        item["source"] in {"download", "bundle", "local", "system"} for item in body["components"]
    )
    assert body["total_bytes"] == sum(item["size_bytes"] or 0 for item in body["components"])
