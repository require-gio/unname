"""Document languages and countries.

Language detection per paragraph (small measurement corpus), language packs in detection
and output, countries for numbers and IDs independent of language,
settings and API.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from faker.providers.person.en_GB import Provider as English
from fastapi.testclient import TestClient
from pydantic import ValidationError

from presidio_sidecar import resources
from presidio_sidecar.analysis import PlainTextAnalyzer
from presidio_sidecar.app import create_app
from presidio_sidecar.base_profile import base_profile
from presidio_sidecar.config import Settings
from presidio_sidecar.detection import (
    DetectionPipeline,
    DetectionResult,
    Finding,
    FindingSource,
    LanguageSegment,
)
from presidio_sidecar.detection.language import detect_language, score_languages, segment_languages
from presidio_sidecar.detection.ner import SpacyGermanNerAdapter
from presidio_sidecar.detection.wordlists import load_language_wordlists, normalize_terms
from presidio_sidecar.deterministic_recognizers import create_deterministic_recognizers
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.findings_view import serialize_detection
from presidio_sidecar.languages import PACKS, DocumentLanguageOptions
from presidio_sidecar.profiles import load_profiles
from presidio_sidecar.registry import MasterDataRegistry
from presidio_sidecar.registry_crypto import generate_registry_key
from presidio_sidecar.services import PhaseOneServices
from presidio_sidecar.settings_store import (
    AppSettings,
    DocumentLanguageSettings,
    SettingsStore,
    migrate,
)
from presidio_sidecar.vault import TokenVault
from presidio_sidecar.workers import WorkerCapacity

WORDS = {code: normalize_terms(pack.function_words) for code, pack in PACKS.items()}
TOKEN = "phase-j"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# -- Measurement corpus for language detection --------------------------------------

#: Typical sentences from letters, contracts, invoices and e-mails, each containing
#: names, numbers and technical terms – i.e. the content that challenges detection.
CORPUS_DE = (
    "Sehr geehrte Frau Hofer, vielen Dank für Ihre Anfrage vom Montag.",
    "Anbei erhalten Sie die Rechnung Nr. 2024-117 für die Lieferung.",
    "Der Mieter verpflichtet sich, die Wohnung in gutem Zustand zu halten.",
    "Bitte überweisen Sie den Betrag bis zum Monatsende auf unser Konto.",
    "Herr Brandstetter hat den Vertrag am Freitag in Wien unterschrieben.",
    "Die Kündigung muss schriftlich und unter Einhaltung der Frist erfolgen.",
    "Mit freundlichen Grüßen aus dem Büro der Tischlerei Gruber",
    "Wir können den Termin leider nicht wie geplant wahrnehmen.",
    "Für Rückfragen stehen wir Ihnen jederzeit gerne zur Verfügung.",
    "Das Gutachten wurde von einem unabhängigen Sachverständigen erstellt.",
    "Ich habe die Unterlagen gestern an die Versicherung geschickt.",
    "Gemäß Paragraph 4 sind alle Nebenkosten vom Mieter zu tragen.",
    "Der Schaden ist durch einen Wasserrohrbruch in der Küche entstanden.",
    "Die Zahlung erfolgt innerhalb von vierzehn Tagen ohne Abzug.",
    "Bei Fragen zur Rechnung wenden Sie sich bitte an Frau Novak.",
    "Unsere Kundin möchte den Auftrag um zwei Positionen erweitern.",
    "Es wurden keine weiteren Mängel bei der Abnahme festgestellt.",
    "Das Angebot gilt bis Ende des Monats und ist unverbindlich.",
    "Wir bestätigen den Eingang Ihres Schreibens vom 3. März.",
    "Die Sitzung wurde um 16 Uhr vom Vorsitzenden geschlossen.",
)
CORPUS_EN = (
    "Dear Ms Hofer, thank you for your enquiry of Monday.",
    "Please find attached the invoice no. 2024-117 for the delivery.",
    "The tenant shall keep the apartment in good condition at all times.",
    "Please transfer the amount to our account by the end of the month.",
    "Mr Brandstetter signed the agreement in Vienna on Friday.",
    "Notice of termination must be given in writing within the period.",
    "Kind regards from the office of Gruber Joinery",
    "Unfortunately we are not able to attend the meeting as planned.",
    "If you have any questions, please do not hesitate to contact us.",
    "The report was prepared by an independent expert in the field.",
    "I sent the documents to the insurance company yesterday.",
    "Pursuant to section 4 all ancillary costs shall be borne by the tenant.",
    "The damage was caused by a burst water pipe in the kitchen.",
    "Payment is due within fourteen days of the date of invoice.",
    "For questions about the invoice please contact Ms Novak.",
    "Our client would like to extend the order by two items.",
    "No further defects were found during the final inspection.",
    "This offer is valid until the end of the month and is not binding.",
    "We confirm receipt of your letter of 3 March.",
    "The meeting was closed by the chair at 4 pm.",
)


def test_language_detection_matches_every_line_of_the_measurement_corpus() -> None:
    wrong = [
        (expected_language, sentence)
        for expected_language, sentences in (("de", CORPUS_DE), ("en", CORPUS_EN))
        for sentence in sentences
        if detect_language(sentence, WORDS) != expected_language
    ]
    assert wrong == []


def test_short_lines_stay_undetermined() -> None:
    for short in ("Rechnung Nr. 4711", "Invoice", "Hofer & Partner GmbH", "12.03.2024", ""):
        assert detect_language(short, WORDS) is None


@pytest.mark.parametrize(
    "technical",
    [
        "<DID:4QK2AB26760|PLX:S|LCD:AT|/>",
        "from@example.at",
        "https://the.example.at",
        "www.the.example.at",
    ],
)
def test_technical_details_do_not_determine_the_language(technical: str) -> None:
    assert score_languages(technical, WORDS) == {"de": 0.0, "en": 0.0}
    text = technical + "\nWir senden die Rechnung und die Ware.\n"

    (segment,) = segment_languages(text, WORDS, default="en")

    assert (segment.start, segment.end, segment.language) == (0, len(text), "de")


def test_mixed_document_is_separated_paragraph_by_paragraph() -> None:
    german_text = "Sehr geehrte Frau Hofer,\nanbei die Unterlagen für den Vertrag.\n\n"
    title = "SERVICE AGREEMENT\n"
    english_text = (
        "This agreement is made between the parties named below.\n"
        "It shall be governed by the laws of Austria.\n"
    )
    text = german_text + title + english_text

    segments = segment_languages(text, WORDS, default="de")

    # The heading belongs to the following English paragraph.
    assert [(segment.language, text[segment.start : segment.end]) for segment in segments] == [
        ("de", german_text),
        ("en", title + english_text),
    ]
    assert segments[0].start == 0 and segments[-1].end == len(text)
    # No gaps and no overlap.
    assert all(a.end == b.start for a, b in zip(segments, segments[1:], strict=False))


def test_short_lines_in_the_middle_of_a_paragraph_keep_the_previous_language() -> None:
    text = (
        "Die Rechnung enthält folgende Positionen für den Auftrag.\n"
        "Pos. 1 Holz 120 EUR\n"
        "Bitte überweisen Sie den Betrag bis zum Monatsende.\n"
    )
    assert [s.language for s in segment_languages(text, WORDS, default="en")] == ["de"]


def test_without_a_determinable_line_the_whole_document_decides() -> None:
    text = "Rechnung\nNr. 4711\nBetrag 120 EUR"
    (segment,) = segment_languages(text, WORDS, default="en")
    assert (segment.language, segment.detected) == ("en", False)


def test_one_language_or_without_paragraph_detection_returns_one_segment() -> None:
    text = "Sehr geehrte Frau Hofer, vielen Dank.\nThe tenant shall pay the rent and the costs.\n"
    (only_de,) = segment_languages(text, {"de": WORDS["de"]}, default="de")
    assert (only_de.start, only_de.end, only_de.language) == (0, len(text), "de")
    (whole,) = segment_languages(text, WORDS, default="de", per_paragraph=False)
    assert whole.end == len(text)
    with pytest.raises(ValueError):
        segment_languages(text, {"en": WORDS["en"]}, default="de")


# -- Countries and language packs in the rules -------------------------------------


def _hits(text: str, **kwargs: Any) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for recognizer in create_deterministic_recognizers(**kwargs):
        for result in recognizer.analyze(text, list(recognizer.supported_entities)):
            found.add((result.entity_type, text[result.start : result.end]))
    return found


def test_countries_select_numbers_and_ids_independent_of_language() -> None:
    def entity_types(countries: tuple[str, ...]) -> set[str]:
        return {
            entity
            for recognizer in create_deterministic_recognizers(countries=countries)
            for entity in recognizer.supported_entities
        }

    only_at = entity_types(("AT",))
    only_de = entity_types(("DE",))
    assert {"AT_UID", "AT_SVNR", "AT_FIRMENBUCH"} <= only_at
    assert not {entity for entity in only_at if entity.startswith("DE_")}
    assert "DE_TAX_ID" in only_de and not {e for e in only_de if e.startswith("AT_")}
    # IBAN, BIC, credit cards and secret data apply everywhere.
    assert {"IBAN_CODE", "BIC", "CREDIT_CARD", "SECRET"} <= only_at & only_de
    phone = next(
        recognizer
        for recognizer in create_deterministic_recognizers(countries=("AT",))
        if "PHONE_NUMBER" in recognizer.supported_entities
    )
    assert phone.supported_regions == ("AT",)
    with pytest.raises(ValueError):
        create_deterministic_recognizers(countries=("CH",))
    with pytest.raises(ValueError):
        create_deterministic_recognizers(countries=())


def test_english_contract_from_vienna_detects_austrian_numbers() -> None:
    """Milestone J, rule part: English text, country Austria."""

    contract = (
        "SERVICE AGREEMENT\n"
        "between Gruber Joinery GmbH, FN 12345f, VAT number ATU12345675, Vienna,\n"
        "and the employee, social security number 1237 010180, phone +43 1 2345678.\n"
        "Signed in Vienna on 3 March 2024."
    )
    found = _hits(contract, countries=("AT",), languages=("en",))
    assert ("AT_FIRMENBUCH", "FN 12345f") in found
    assert ("AT_UID", "ATU12345675") in found
    assert ("AT_SVNR", "1237 010180") in found
    assert ("PHONE_NUMBER", "+43 1 2345678") in found
    assert ("DATE_TIME", "3 March 2024") in found


def test_month_names_from_language_packs() -> None:
    texts = {
        "am 3. März 2024": "3. März 2024",
        "seit 1. Jänner": "1. Jänner",
        "on March 3, 2024": "March 3, 2024",
        "on the 21st of December 2023": "21st of December 2023",
    }
    for text, expected_language in texts.items():
        assert ("DATE_TIME", expected_language) in _hits(text), text
    # Lowercase verb, no date; day outside month also not recognized.
    assert not {hit for hit in _hits("Section 3 may be terminated.") if hit[0] == "DATE_TIME"}
    assert not {hit for hit in _hits("am 45. März 2024") if hit[0] == "DATE_TIME"}
    # Only German month names when only German is enabled.
    only_de = _hits("on March 3, 2024", languages=("de",))
    assert ("DATE_TIME", "March 3, 2024") not in only_de


def test_field_identifiers_from_language_packs() -> None:
    english_text = _hits("Our ref: KL/2024/77, customer reference AB-12345", languages=("en",))
    assert ("CASE_NUMBER", "KL/2024/77") in english_text
    assert ("CUSTOMER_NUMBER", "AB-12345") in english_text
    german_text = _hits("Aktenzeichen 4 Ob 12/24, Kundennummer 4711-22", languages=("de",))
    assert ("CUSTOMER_NUMBER", "4711-22") in german_text
    assert ("CASE_NUMBER", "KL/2024/77") not in _hits("Our ref: KL/2024/77", languages=("de",))


def test_amounts_in_either_notation_are_not_numbers() -> None:
    """Amount formats: measured there are no false negatives – no separate filter needed."""

    for text in (
        "Betrag: EUR 1.234.567,00 netto",
        "Summe 12.345.678,90 €",
        "Total: $ 1,234,567.00",
        "amount of GBP 2,500,000.00",
    ):
        assert _hits(text) == set(), text


# -- Model selection by document language --------------------------------------------


class _Entity:
    def __init__(self, text: str, start: int, label: str) -> None:
        self.text, self.start_char, self.end_char, self.label_ = (
            text,
            start,
            start + len(text),
            label,
        )


class _Model:
    def __init__(self, name: str, calls: list[tuple[str, str]]) -> None:
        self.name, self.calls = name, calls

    def __call__(self, text: str) -> Any:
        self.calls.append((self.name, text))
        position = text.find("Hofer")
        ents = [_Entity("Hofer", position, "PER" if self.name == "de" else "PERSON")]
        return type("Doc", (), {"ents": ents if position >= 0 else []})()


def test_spacy_fallback_uses_the_model_of_the_language_per_segment() -> None:
    calls: list[tuple[str, str]] = []
    adapter = SpacyGermanNerAdapter(
        model_name="de",
        loader=lambda name: _Model(name, calls),
        language_models={"en": "en"},
    )
    text = "Frau Hofer zahlt.\nMs Hofer pays.\n"
    segments = (LanguageSegment(0, 18, "de"), LanguageSegment(18, len(text), "en"))

    findings = adapter.detect_segments(text, segments)

    assert [name for name, _ in calls] == ["de", "en"]
    assert [(f.text, f.start, f.detector) for f in findings] == [
        ("Hofer", 5, "de_core_news_lg"),
        ("Hofer", 21, "spacy_en"),
    ]


def test_without_language_pack_the_german_model_applies() -> None:
    calls: list[tuple[str, str]] = []
    adapter = SpacyGermanNerAdapter(
        model_name="de", loader=lambda name: _Model(name, calls), language_models={}
    )
    adapter.detect_segments("Ms Hofer pays.", (LanguageSegment(0, 14, "en"),))
    assert [name for name, _ in calls] == ["de"]


@pytest.mark.skipif(
    not resources.spacy_available("en"), reason="en_core_web_sm ist nicht installiert"
)
def test_real_english_language_pack_detects_names() -> None:
    adapter = SpacyGermanNerAdapter()
    text = "Sehr geehrte Damen und Herren,\nJohn Smith works for Acme Corporation in London.\n"
    start = text.index("John")
    findings = adapter.detect_segments(
        text, (LanguageSegment(0, start, "de"), LanguageSegment(start, len(text), "en"))
    )
    names = {(f.text, f.entity_type, f.detector) for f in findings if f.start >= start}
    assert ("John Smith", "PERSON", "spacy_en") in names


# -- Pipeline: suspicion, segments in result ------------------------------------


class _NoNer:
    detector_name = "test"

    def detect(self, text: str) -> tuple[Finding, ...]:
        return ()


def test_suspicion_check_uses_paragraph_language_starters() -> None:
    text = "Governing Law\nBitte Gebrüder Wachtelhuber anrufen.\n"
    segments = (LanguageSegment(0, 14, "en"), LanguageSegment(14, len(text), "de"))
    starters = {
        "de": normalize_terms(["Bitte"]),
        "en": normalize_terms(["Governing"]),
    }
    result = DetectionPipeline(ner=_NoNer(), business_terms=(), sentence_starters=()).analyze(
        text, languages=segments, sentence_starters_by_language=starters
    )
    assert [f.text for f in result.suspicious] == ["Gebrüder Wachtelhuber"]
    assert result.languages == segments


def test_ner_filter_uses_only_business_terms_of_paragraph_language() -> None:
    class WordsNer:
        def detect(self, text: str) -> tuple[Finding, ...]:
            return tuple(
                Finding(
                    "PERSON",
                    text.index(value),
                    text.index(value) + len(value),
                    0.95,
                    FindingSource.NER,
                    text=value,
                )
                for value in ("Invoice", "Rechnung", "Firma", "Order")
            )

    text = "Invoice Rechnung\nFirma Order\n"
    boundary = text.index("Firma")
    lists = {code: load_language_wordlists(code) for code in ("de", "en")}
    result = DetectionPipeline(WordsNer()).analyze(
        text,
        languages=(LanguageSegment(0, boundary, "de"), LanguageSegment(boundary, len(text), "en")),
        business_terms_by_language={code: words.business_terms for code, words in lists.items()},
        suspicion_enabled=False,
    )

    # Foreign-language terms may be names here.
    assert [finding.text for finding in result.confirmed] == ["Invoice", "Firma"]


def test_suspicion_uses_terms_and_starters_only_of_paragraph_language() -> None:
    text = "Dear Meier\nBitte Miller\n"
    boundary = text.index("Bitte")
    lists = {code: load_language_wordlists(code) for code in ("de", "en")}
    result = DetectionPipeline(_NoNer()).analyze(
        text,
        languages=(LanguageSegment(0, boundary, "de"), LanguageSegment(boundary, len(text), "en")),
        business_terms_by_language={code: words.business_terms for code, words in lists.items()},
        sentence_starters_by_language={
            code: words.sentence_starters for code, words in lists.items()
        },
    )

    assert [finding.text for finding in result.suspicious] == ["Dear Meier", "Bitte Miller"]


@pytest.mark.parametrize(
    ("language", "text", "expected"),
    [("en", "Please John Smith", "John Smith"), ("de", "Bitte Hans Meier", "Hans Meier")],
)
def test_pipeline_can_be_created_for_a_document_language(
    language: str, text: str, expected: str
) -> None:
    result = DetectionPipeline(_NoNer(), language=language).analyze(text)

    assert [finding.text for finding in result.suspicious] == [expected]


def _services(tmp_path: Path, ner: Any = None) -> PhaseOneServices:
    registry = MasterDataRegistry(tmp_path / "registry.enc", generate_registry_key())
    return PhaseOneServices(
        Settings(token=TOKEN, state_directory=tmp_path / "state"),
        DiagnosticsRecorder(),
        registry=registry,
        analyzer=PlainTextAnalyzer(
            registry, semantic_pipeline=DetectionPipeline(ner=ner or _NoNer())
        ),
        profiles=load_profiles(),
        capacity=WorkerCapacity(1, 0, 1, 8 * 1024**3),
    )


@pytest.mark.parametrize("selected", [("de",), ("en",), ("de", "en")])
def test_language_configuration_keeps_word_lists_separate(
    tmp_path: Path, selected: tuple[str, ...]
) -> None:
    services = _services(tmp_path)
    try:
        configured = services.language_analysis(DocumentLanguageOptions(languages=selected))
        assert set(configured.business_terms) == set(selected)
        assert set(configured.sentence_starters) == set(selected)
        for code in selected:
            words = load_language_wordlists(code)
            assert configured.business_terms[code] == words.business_terms
            assert configured.sentence_starters[code] == words.sentence_starters
    finally:
        services.close()


def test_base_profile_does_not_mix_business_terms(tmp_path: Path) -> None:
    class NameNer:
        def detect(self, text: str) -> tuple[Finding, ...]:
            return tuple(
                Finding(
                    "PERSON",
                    text.index(value),
                    text.index(value) + len(value),
                    0.95,
                    FindingSource.NER,
                    text=value,
                )
                for value in ("Invoice", "Firma")
            )

    services = _services(tmp_path, NameNer())
    text = "Wir sehen die Person namens Invoice hier.\nWe see the person named Firma here.\n"
    try:
        result = services.analyzer_for_profile(base_profile()).analyze(
            text, progress=lambda _: None
        )

        assert [segment.language for segment in result.languages] == ["de", "en"]
        assert [(finding.entity_type, finding.text) for finding in result.confirmed] == [
            ("PERSON", "Invoice"),
            ("PERSON", "Firma"),
        ]
    finally:
        services.close()


def test_english_contract_from_vienna_in_base_profile(tmp_path: Path) -> None:
    """Test mixed German/English document, country Austria."""

    services = _services(tmp_path)
    services.settings_store().update({"document_languages": {"countries": ["AT"]}})
    text = (
        "Sehr geehrte Damen und Herren,\nanbei der Vertrag mit unserer Firma.\n\n"
        "This agreement is made with Gruber Joinery GmbH, FN 12345f.\n"
        "The VAT number of the company is ATU12345675.\n"
    )
    result = services.analyzer_for_profile(base_profile()).analyze(text, progress=lambda _: None)

    assert [segment.language for segment in result.languages] == ["de", "en"]
    found = {(f.entity_type, f.text) for f in result.confirmed}
    assert ("AT_UID", "ATU12345675") in found
    assert ("AT_FIRMENBUCH", "FN 12345f") in found
    english = text.index("This")
    assert result.language_at(english) == "en" and result.language_at(0) == "de"
    services.close()


def test_one_language_in_settings_turns_off_language_detection(tmp_path: Path) -> None:
    services = _services(tmp_path)
    services.settings_store().update({"document_languages": {"languages": ["en"]}})
    result = services.analyzer_for_profile(base_profile()).analyze(
        "Sehr geehrte Frau Hofer, vielen Dank für Ihre Nachricht.", progress=lambda _: None
    )
    assert [(s.language, s.detected) for s in result.languages] == [("en", False)]
    services.close()


# -- Placeholders and invented names in the paragraph language ----------------


class _NameNer:
    """Finds "Hofer" as person and "Gruber Joinery" or "Tischlerei Gruber" as organization."""

    detector_name = "test"

    def detect(self, text: str) -> tuple[Finding, ...]:
        found: list[Finding] = []
        for value, entity in (
            ("Hofer", "PERSON"),
            ("Gruber Joinery", "ORGANIZATION"),
            ("Tischlerei Gruber", "ORGANIZATION"),
        ):
            start = text.find(value)
            while start >= 0:
                found.append(
                    Finding(
                        entity_type=entity,
                        start=start,
                        end=start + len(value),
                        score=0.99,
                        source=FindingSource.NER,
                        text=value,
                        detector="test",
                    )
                )
                start = text.find(value, start + 1)
        return tuple(found)


@pytest.fixture
def api(tmp_path: Path) -> Iterator[tuple[TestClient, PhaseOneServices]]:
    registry = MasterDataRegistry(tmp_path / "registry.enc", generate_registry_key())
    settings = Settings(token=TOKEN, state_directory=tmp_path / "state")
    recorder = DiagnosticsRecorder()
    services = PhaseOneServices(
        settings,
        recorder,
        registry=registry,
        analyzer=PlainTextAnalyzer(registry, semantic_pipeline=DetectionPipeline(ner=_NameNer())),
        profiles=load_profiles(),
        capacity=WorkerCapacity(1, 0, 1, 8 * 1024**3),
    )
    with TestClient(create_app(settings, recorder, phase_one=services)) as client:
        yield client, services


MIXED = (
    "Sehr geehrte Damen und Herren, die Tischlerei Gruber hat mit Frau Hofer gesprochen.\n"
    "This agreement is made with Gruber Joinery and the tenant is Ms Hofer.\n"
)
PLACEHOLDERS = {
    "method": "irreversible",
    "style": "placeholder",
    "categories": ["names", "organizations"],
    "anonymize": False,
}


def _mask(client: TestClient, masking: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/text/mask",
        headers=AUTH,
        json={"text": MIXED, "masking": masking, "review": {"decisions": [], "manual": []}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_placeholders_follow_paragraph_language(api: Any) -> None:
    client, services = api
    text = _mask(client, PLACEHOLDERS)["result"]["text"]
    german_text, english_text = text.splitlines()
    assert "die [Firma] hat mit Frau [Name] gesprochen" in german_text
    assert "made with [Company] and the tenant is Ms [Name]" in english_text

    # Disabled: everything uses the app language.
    services.settings_store().update(
        {"document_languages": {"placeholders_in_document_language": False}}
    )
    text = _mask(client, PLACEHOLDERS)["result"]["text"]
    assert "[Company]" not in text and text.count("[Firma]") == 2


def test_paragraphs_carry_their_language() -> None:
    text = "Sehr geehrte Frau Hofer, vielen Dank.\n\nThe tenant shall pay the rent.\n"
    english = text.index("The")
    result = DetectionResult(
        languages=(LanguageSegment(0, english, "de"), LanguageSegment(english, len(text), "en"))
    )
    blocks = serialize_detection("job", text, result)["blocks"]
    assert [block["language"] for block in blocks] == ["de", "en"]
    assert [
        block["language"] for block in serialize_detection("job", text, DetectionResult())["blocks"]
    ] == [None, None]


def test_fake_names_come_from_the_language_pack(tmp_path: Path) -> None:
    vault = TokenVault(tmp_path / "state")
    english = vault.prepare_surrogate("PERSON", "Sabine Hofer", language="en").surrogate
    assert english is not None
    assert vault.prepare_surrogate("PERSON", "Sabine Hofer", language="en").surrogate == english
    first_name = english.split(" ")[0]
    assert first_name in {*English.first_names_male, *English.first_names_female}

    # A stored alias remains the same across languages.
    pending = vault.prepare_surrogate("PERSON", "Josef Gruber", language="de")
    vault.commit([pending])
    again = vault.prepare_surrogate("PERSON", "Josef Gruber", language="en")
    assert again.surrogate == pending.surrogate
    vault.close()


# -- Settings, Models, Health ---------------------------------------------


def test_settings_version_two_with_document_languages(tmp_path: Path) -> None:
    alt = {"schema_version": 1, "document_languages": {"languages": ["de"], "countries": ["CH"]}}
    migrated = migrate(alt)
    assert migrated["schema_version"] == 3 and "document_languages" not in migrated
    standard = AppSettings.model_validate(migrated).document_languages
    assert standard == DocumentLanguageSettings(
        languages=("de", "en"),
        per_paragraph=True,
        countries=("DE", "AT"),
        placeholders_in_document_language=True,
    )
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(alt), encoding="utf-8")
    assert SettingsStore(path).get().document_languages.countries == ("DE", "AT")

    store = SettingsStore(tmp_path / "neu.json")
    changed = store.update({"document_languages": {"languages": ["en", "de", "en"]}})
    assert changed.document_languages.languages == ("de", "en")
    for wrong in ({"languages": []}, {"countries": []}, {"countries": ["CH"]}):
        with pytest.raises(ValueError):
            store.update({"document_languages": wrong})
    with pytest.raises(ValidationError):
        DocumentLanguageSettings(languages=("fr",))  # type: ignore[arg-type]


def test_language_options_are_validated_and_sorted() -> None:
    options = DocumentLanguageOptions(languages=("en", "de"), countries=("AT", "DE"))
    assert options.languages == ("de", "en") and options.countries == ("DE", "AT")
    assert options.default_language == "de"
    assert DocumentLanguageOptions(languages=("en",)).default_language == "en"
    assert options.fingerprint() != DocumentLanguageOptions(countries=("AT",)).fingerprint()
    with pytest.raises(ValueError):
        DocumentLanguageOptions(languages=())


def test_any_language_pack_sufficient_for_setup() -> None:
    def inventory(**ready: bool) -> dict[str, dict[str, str]]:
        return {
            name: {"state": "ready" if ready.get(name) else "missing"}
            for name in ("ner", "de", "en", "ocr")
        }

    assert resources.setup_required(inventory(ner=True))
    assert resources.setup_required(inventory(de=True, en=True))
    assert not resources.setup_required(inventory(ner=True, en=True))
    assert not resources.setup_required(inventory(ner=True, de=True))


def test_api_settings_and_models_know_document_languages(api: Any) -> None:
    client, _ = api
    settings = client.get("/settings", headers=AUTH).json()
    assert settings["schema_version"] == 3
    assert settings["document_languages"] == {
        "languages": ["de", "en"],
        "per_paragraph": True,
        "countries": ["DE", "AT"],
        "placeholders_in_document_language": True,
    }
    updated = client.put(
        "/settings", headers=AUTH, json={"document_languages": {"countries": ["AT"]}}
    )
    assert updated.status_code == 200
    assert updated.json()["document_languages"]["countries"] == ["AT"]
    assert (
        client.put(
            "/settings", headers=AUTH, json={"document_languages": {"languages": []}}
        ).status_code
        == 422
    )
    models = client.get("/models", headers=AUTH).json()
    by_id = {item["id"]: item for item in models["components"]}
    assert set(by_id) == {"ner", "de", "en", "ocr", "classifier"}
    if by_id["en"]["state"] == "ready":
        assert by_id["en"]["languages"] == ["en"]
