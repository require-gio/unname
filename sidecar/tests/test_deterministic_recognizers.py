"""Tests for level 1 of the detection pipeline (spec §2)."""

from __future__ import annotations

from collections.abc import Iterable

import pytest
from presidio_analyzer import EntityRecognizer, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.predefined_recognizers import (
    DateRecognizer,
    DePlzRecognizer,
    DeVatIdRecognizer,
    PhoneRecognizer,
)

from presidio_sidecar.deterministic_recognizers import (
    BUILT_IN_ENTITY_TYPES,
    CUSTOM_ENTITY_TYPES,
    ApiKeyRecognizer,
    AtCompanyRegisterRecognizer,
    AtSocialSecurityRecognizer,
    AtUidRecognizer,
    BicRecognizer,
    CaseNumberRecognizer,
    CustomerNumberRecognizer,
    OfflineEmailRecognizer,
    SecretRecognizer,
    create_deterministic_recognizers,
    register_deterministic_recognizers,
)


def _recognizer_for(recognizers: Iterable[EntityRecognizer], entity_type: str) -> EntityRecognizer:
    return next(
        recognizer for recognizer in recognizers if entity_type in recognizer.supported_entities
    )


def _matched_texts(recognizer: EntityRecognizer, text: str) -> list[str]:
    results = recognizer.analyze(text, recognizer.supported_entities)
    return [text[result.start : result.end] for result in results]


def test_factory_enables_all_requested_types_except_plz() -> None:
    recognizers = create_deterministic_recognizers()
    entities = {entity for recognizer in recognizers for entity in recognizer.supported_entities}

    assert entities >= BUILT_IN_ENTITY_TYPES | CUSTOM_ENTITY_TYPES
    assert "DE_PLZ" not in entities


def test_phone_recognizer_configured_only_for_de_and_at() -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "PHONE_NUMBER")
    assert isinstance(recognizer, PhoneRecognizer)
    assert recognizer.supported_regions == ("DE", "AT")

    assert _matched_texts(recognizer, "DE +49 30 123456, AT +43 1 2345678")


def test_phone_regions_filter_explicit_international_numbers() -> None:
    recognizer = _recognizer_for(
        create_deterministic_recognizers(phone_regions=("US",)),
        "PHONE_NUMBER",
    )

    assert _matched_texts(
        recognizer,
        "+43 664 1234567 / +1 212-555-1234",
    ) == ["+1 212-555-1234"]


def test_cv_date_ranges_are_not_phone_numbers() -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "PHONE_NUMBER")

    assert _matched_texts(recognizer, "Software Engineer 05/2024 – 07/2025 Technische") == []
    assert _matched_texts(recognizer, "(Freelance) 02/2023 – 02/2024 Entwicklung") == []
    assert _matched_texts(recognizer, "(sehr gut)) 10/2018 – 01/2022 Schwerpunkt") == []
    assert _matched_texts(recognizer, "Tel. 0511 2024567") == ["0511 2024567"]


def test_valid_number_with_country_code_needs_no_context() -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "PHONE_NUMBER")
    text = "giorgi@example.com +49 177 2830361 Wien, Festnetz 0511 2024567"

    scores = {
        text[result.start : result.end]: result.score
        for result in recognizer.analyze(text, ["PHONE_NUMBER"])
    }

    assert scores["+49 177 2830361"] >= 0.92
    assert scores["0511 2024567"] < 0.92


def test_academic_degrees_are_not_web_addresses() -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "URL")

    assert _matched_texts(
        recognizer,
        "M.Sc. Informatik, B.Sc. Mathe; example.com, WWW.EXAMPLE.DE, https://Foo.Example.Org",
    ) == ["https://Foo.Example.Org", "example.com", "WWW.EXAMPLE.DE"]


def test_email_recognizer_remains_offline_with_empty_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def network_extract_was_called(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Das modulweite tldextract darf nicht aufgerufen werden")

    monkeypatch.setattr("tldextract.extract", network_extract_was_called)
    recognizer = _recognizer_for(create_deterministic_recognizers(), "EMAIL_ADDRESS")

    assert isinstance(recognizer, OfflineEmailRecognizer)
    assert recognizer.name == "EmailRecognizer"
    assert _matched_texts(recognizer, "E-Mail: info@example.at") == ["info@example.at"]


def test_date_time_uses_presidios_model_free_recognizer() -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "DATE_TIME")

    assert isinstance(recognizer, DateRecognizer)
    assert recognizer.supported_language == "de"
    assert {
        "datum",
        "geburtsdatum",
        "lieferdatum",
        "rechnungsdatum",
        "termin",
        "zeitpunkt",
    } <= set(recognizer.context)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Rechnungsdatum: 03.04.2026", "03.04.2026"),
        ("Lieferdatum: 3.4.26", "3.4.26"),
        ("Termin: 2026-04-03", "2026-04-03"),
        ("Archivdatum: 2026/04/03", "2026/04/03"),
        ("Zeitpunkt: 2026-04-03T14:30:00Z", "2026-04-03T14:30:00Z"),
        ("Abrechnungsmonat: 04/2026", "04/2026"),
        ("Am 03.04.2026 von Petra freigegeben", "03.04.2026"),
    ],
)
def test_date_time_detects_german_and_iso_writing_styles(
    text: str,
    expected: str,
) -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "DATE_TIME")

    assert _matched_texts(recognizer, text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "Ungültiger Tag: 32.04.2026",
        "Ungültiger Monat: 03.13.2026",
        "Null ist kein Tag: 00.04.2026",
        "Null ist kein Monat: 03.00.2026",
        "Kompakte Artikelnummer: 20260403",
        "Softwareversion: 3.8.0",
        "In einem Wort: X03.04.2026",
        "In einem Wort: 03.04.2026x",
    ],
)
def test_date_time_rejects_invalid_and_embedded_values(text: str) -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "DATE_TIME")

    assert _matched_texts(recognizer, text) == []


def test_de_plz_is_opt_in_and_keeps_base_score_005() -> None:
    recognizers = create_deterministic_recognizers(enable_de_plz=True)
    recognizer = _recognizer_for(recognizers, "DE_PLZ")

    assert isinstance(recognizer, DePlzRecognizer)
    assert [pattern.score for pattern in recognizer.patterns] == [0.05]
    results = recognizer.analyze("PLZ 10115", ["DE_PLZ"])
    assert len(results) == 1
    assert results[0].score == pytest.approx(0.05)


def test_de_plz_detected_only_with_address_context_in_opt_in() -> None:
    recognizer = _recognizer_for(
        create_deterministic_recognizers(enable_de_plz=True),
        "DE_PLZ",
    )

    assert _matched_texts(recognizer, "Bestellmenge 12345 Stück") == []
    assert _matched_texts(
        recognizer,
        "Adresse: Musterstraße 1, 10115 Berlin",
    ) == ["10115"]


def test_registry_installation_replaces_same_named_recognizer() -> None:
    registry = RecognizerRegistry(supported_languages=["de"])
    first = register_deterministic_recognizers(registry)
    second = register_deterministic_recognizers(registry)

    assert len(first) == len(second) == len(registry.recognizers)
    assert len({(item.name, item.supported_language) for item in registry.recognizers}) == len(
        registry.recognizers
    )


def test_registry_installation_removes_previously_activated_plz() -> None:
    registry = RecognizerRegistry(supported_languages=["de"])
    register_deterministic_recognizers(registry, enable_de_plz=True)
    assert any("DE_PLZ" in item.supported_entities for item in registry.recognizers)

    register_deterministic_recognizers(registry)
    assert all("DE_PLZ" not in item.supported_entities for item in registry.recognizers)


@pytest.mark.parametrize(
    ("recognizer", "valid_text", "invalid_text", "expected"),
    [
        (AtUidRecognizer(), "UID: ATU13585627", "UID: ATU13585626", "ATU13585627"),
        (
            AtSocialSecurityRecognizer(),
            "SVNR: 7829 28 07 55",
            "SVNR: 7828 28 07 55",
            "7829 28 07 55",
        ),
    ],
)
def test_check_digits(
    recognizer: PatternRecognizer,
    valid_text: str,
    invalid_text: str,
    expected: str,
) -> None:
    assert _matched_texts(recognizer, valid_text) == [expected]
    assert _matched_texts(recognizer, invalid_text) == []


@pytest.mark.parametrize(
    ("recognizer", "text", "expected"),
    [
        (AtUidRecognizer(), "ATU13585627 von Petra", "ATU13585627"),
        (AtSocialSecurityRecognizer(), "7829 28 07 55 von Petra", "7829 28 07 55"),
        (AtCompanyRegisterRecognizer(), "FN 33209 m von Petra", "FN 33209 m"),
        (BicRecognizer(), "BIC: BKAUATWW von Petra", "BKAUATWW"),
        (
            CustomerNumberRecognizer(),
            "Kundennummer: KD-4711 von Petra",
            "KD-4711",
        ),
        (
            SecretRecognizer(),
            "sk-abcdefghijklmnopqrstuvwxyz1234 von Petra",
            "sk-abcdefghijklmnopqrstuvwxyz1234",
        ),
        (DeVatIdRecognizer(), "DE136695976 von Petra", "DE136695976"),
    ],
)
def test_custom_patterns_do_not_swallow_following_words(
    recognizer: EntityRecognizer,
    text: str,
    expected: str,
) -> None:
    assert _matched_texts(recognizer, text) == [expected]


@pytest.mark.parametrize(
    ("recognizer", "text"),
    [
        (AtUidRecognizer(), "XATU13585627"),
        (AtSocialSecurityRecognizer(), "X7829280755"),
        # Timestamp date and hour; the check digit happens to be valid by chance.
        (AtSocialSecurityRecognizer(), "2026-03-27 20"),
        (AtCompanyRegisterRecognizer(), "XFN 100233 i"),
        (BicRecognizer(), "RECHNUNG"),
        (CustomerNumberRecognizer(), "KD-4711"),
        (SecretRecognizer(), "sk-short"),
        (DeVatIdRecognizer(), "XDE136695976"),
    ],
)
def test_custom_patterns_respect_boundaries_and_context(
    recognizer: EntityRecognizer,
    text: str,
) -> None:
    assert _matched_texts(recognizer, text) == []


def test_customer_number_returns_only_value_without_field_name() -> None:
    recognizer = CustomerNumberRecognizer()
    assert _matched_texts(
        recognizer,
        "Kunden-Nr.: KD-4711, Customer ID = ACCT/2026-0042",
    ) == ["KD-4711", "ACCT/2026-0042"]


def test_case_number_returns_only_value_and_stops_before_follow_word() -> None:
    recognizer = CaseNumberRecognizer()

    assert _matched_texts(
        recognizer,
        "Az.: 12-C-345/26 von Petra; GZ: AB_2026-77.",
    ) == ["12-C-345/26", "AB_2026-77"]
    assert _matched_texts(recognizer, "Ohne Feldlabel 12-C-345/26") == []


def test_api_key_is_distinguished_from_general_secret() -> None:
    token = "sk-abcdefghijklmnopqrstuvwxyz1234"

    assert _matched_texts(ApiKeyRecognizer(), f"API-Key: {token} von Petra") == [token]
    assert _matched_texts(ApiKeyRecognizer(), "Passwort: 'Sehr-Geheim-2026!'") == []


def test_crypto_recognizer_is_active_in_factory() -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "CRYPTO")
    address = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"

    assert _matched_texts(recognizer, f"Bitcoin: {address}") == [address]


def test_customer_number_supports_german_sentence_syntax() -> None:
    recognizer = CustomerNumberRecognizer()

    assert _matched_texts(
        recognizer,
        "Ihre Kundennummer lautet KD-4711; die Debitorennummer ist 4711.",
    ) == ["KD-4711", "4711"]


def test_company_register_check_letter_is_validated_by_modulo_17() -> None:
    recognizer = AtCompanyRegisterRecognizer()

    assert _matched_texts(recognizer, "Firmenbuchnummer: FN 33209 m") == ["FN 33209 m"]
    assert _matched_texts(recognizer, "Firmenbuchnummer: FN 033209 m") == ["FN 033209 m"]
    assert _matched_texts(recognizer, "Firmenbuchnummer: FN 33209 c") == []
    assert _matched_texts(recognizer, "Firmenbuchnummer: FN 1234567 d") == []


@pytest.mark.parametrize(
    "text",
    [
        "Die Abholung erfolgt morgen.",
        "ARTIKELN",
        "BKAUATWW",
    ],
)
def test_bic_requires_field_label_due_to_missing_check_digit(text: str) -> None:
    assert _matched_texts(BicRecognizer(), text) == []


def test_bic_and_swift_code_field_labels_are_recognized() -> None:
    recognizer = BicRecognizer()

    assert _matched_texts(recognizer, "BIC-Code: BKAUATWW") == ["BKAUATWW"]
    assert _matched_texts(recognizer, "BIC: A1B2ATWW") == ["A1B2ATWW"]
    assert _matched_texts(recognizer, "SWIFT Code = DEUTDEFF500") == ["DEUTDEFF500"]


@pytest.mark.parametrize(
    ("text", "token"),
    [
        ("äAKIAABCDEFGHIJKLMNOPö", "AKIAABCDEFGHIJKLMNOP"),
        ("äghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAö", "ghp_"),
        ("äsk-abcdefghijklmnopqrstö", "sk-"),
        ("äeyJaaaaaaaa.bbbbbbbb.ccccccccö", "eyJ"),
    ],
)
def test_secret_respects_boundaries_with_unicode_neighbors(
    text: str,
    token: str,
) -> None:
    assert token in text
    assert _matched_texts(SecretRecognizer(), text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "api_key = sk-abcdefghijklmnopqrstuvwxyz1234",
            "sk-abcdefghijklmnopqrstuvwxyz1234",
        ),
        ("Passwort: 'Sehr-Geheim-2026!'", "Sehr-Geheim-2026!"),
        ("AWS: AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    ],
)
def test_secret_varianten(text: str, expected: str) -> None:
    assert expected in _matched_texts(SecretRecognizer(), text)


def test_builtin_iban_recognizer_stops_before_follow_word() -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "IBAN_CODE")
    text = "AT61 1904 3002 3457 3201 von Petra"
    assert _matched_texts(recognizer, text) == ["AT61 1904 3002 3457 3201"]


@pytest.mark.parametrize(
    "text",
    [
        "sudo systemctl edit ollama.service",
        "FROM ./model-q4_K_M.gguf",
        "/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg",
        "sudo tee /etc/apt/sources.list.d/nvidia.list",
    ],
)
def test_url_not_matched_inside_file_or_path_names(text: str) -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "URL")

    assert _matched_texts(recognizer, text) == []


def test_url_as_whole_word_remains_recognized() -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "URL")

    assert _matched_texts(recognizer, "Doku: https://docs.ollama.com/gpu oder huggingface.co.") == [
        "https://docs.ollama.com/gpu",
        "huggingface.co",
    ]


@pytest.mark.parametrize(
    "ip_address", ["127.0.0.1", "0.0.0.0", "255.255.255.255", "::1", "224.0.0.1"]
)
def test_ip_without_device_reference_is_not_recognized(ip_address: str) -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "IP_ADDRESS")

    assert _matched_texts(recognizer, f"Adresse: {ip_address} Port 11434") == []


@pytest.mark.parametrize("ip_address", ["192.168.178.24", "85.12.4.200", "2001:db8::42"])
def test_ip_with_device_reference_remains_detected(ip_address: str) -> None:
    recognizer = _recognizer_for(create_deterministic_recognizers(), "IP_ADDRESS")

    assert ip_address in _matched_texts(recognizer, f"Zugriff von {ip_address} um 10:42")
