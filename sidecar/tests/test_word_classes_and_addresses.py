"""Addresses and word classes (Polizze Haushaltsversicherung, 08.11.2026).

Background: In a policy the residential address appeared four times –
no recognizer returned ``STREET_ADDRESS`` – while “Sie”, “Verbraucher"
and “Versicherungsnehmer” were treated as names and generated 22 questions
through the repeat review.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import pytest

from presidio_sidecar.detection import DetectionPipeline, Finding, FindingSource
from presidio_sidecar.detection.pipeline import EXACT_DUPLICATE_DETECTOR
from presidio_sidecar.detection.second_opinion import LABELS
from presidio_sidecar.detection.suspicion import NAME_SUSPICION_DETECTOR
from presidio_sidecar.detection.word_classes import (
    SpacyWordClassTagger,
    WordClass,
    without_non_names,
    word_class,
)
from presidio_sidecar.deterministic_recognizers import (
    StreetAddressRecognizer,
    create_deterministic_recognizers,
)

# -- addresses ---------------------------------------------------------------


def _addresses(text: str) -> list[tuple[str, float]]:
    recognizer = StreetAddressRecognizer()
    return [
        (text[result.start : result.end], result.score)
        for result in recognizer.analyze(text, ["STREET_ADDRESS"])
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # address block: postal code and city on the next line.
        (
            "Max Mustermann\nMusterbergstraße 12/1/30\n1110 Wien\nPolizze",
            "Musterbergstraße 12/1/30\n1110 Wien",
        ),
        ("Musterbergstraße 12/1/30, 1110 Wien, Österreich", "Musterbergstraße 12/1/30, 1110 Wien"),
        (
            "Landesdirektion Wien, Obere Musterstraße 49-51, 1020 Wien, Tel",
            "Obere Musterstraße 49-51, 1020 Wien",
        ),
        ("Sitz in 1010 Wien, Schottenring 10, FN 333376i", "1010 Wien, Schottenring 10"),
        (
            "Group, Schottenring 10, Postfach 80, 1010 Wien, oder",
            "Schottenring 10, Postfach 80, 1010 Wien",
        ),
        ("Finanzmarktaufsicht, Otto-Wagner-Platz 5, 1090 Wien.", "Otto-Wagner-Platz 5, 1090 Wien"),
        ("Karl-Marx-Str. 7a, D-10178 Berlin", "Karl-Marx-Str. 7a, D-10178 Berlin"),
        ("Wiedner Hauptstraße 12/Top 4, 1040 Wien", "Wiedner Hauptstraße 12/Top 4, 1040 Wien"),
        ("St. Pöltner Straße 5, 3100 St. Pölten", "St. Pöltner Straße 5, 3100 St. Pölten"),
        ("Am Graben 5, 1010 Wien", "Am Graben 5, 1010 Wien"),
        ("MUSTERBERGSTRASSE 12\n1110 WIEN", "MUSTERBERGSTRASSE 12\n1110 WIEN"),
    ],
)
def test_address_with_postal_code_is_unambiguous(text: str, expected: str) -> None:
    assert _addresses(text) == [(expected, StreetAddressRecognizer.WITH_POSTAL_CODE)]


def test_street_without_postal_code_remains_a_question() -> None:
    assert _addresses("Lieferung an Linzer Str. 3") == [
        ("Linzer Str. 3", StreetAddressRecognizer.STREET_ONLY)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Im Jahr 2024 wurde es teurer.",  # preposition without postal code
        "Am Montag 3 Termine",
        "Der Rückweg 3 km war lang.",  # measurement, no house number
        "Die Straße 5 ist gesperrt",  # article, no street name
        "die Straße 5",
        "Abschnitt 2 Absatz 3",
        "Retouren an Postfach 350, 1375 Wien",
        "Seite 1 von 6",
    ],
)
def test_no_address(text: str) -> None:
    assert _addresses(text) == []


def test_addresses_belong_to_built_in_rules() -> None:
    names = {recognizer.name for recognizer in create_deterministic_recognizers()}
    assert "StreetAddressRecognizer" in names


# -- word classes ------------------------------------------------------------------


class FakeTagger:
    """Tags words according to the table; unknown words get ``NN``."""

    def __init__(self, tags: dict[str, str], *, languages: Sequence[str] = ("de",)) -> None:
        self.tags = tags
        self.languages = tuple(languages)
        self.calls: list[tuple[str, int]] = []

    def tag(self, texts: Sequence[str], language: str) -> list[list[tuple[int, int, str]]] | None:
        self.calls.append((language, len(texts)))
        if language not in self.languages:
            return None
        return [
            [
                (match.start(), match.end(), self.tags.get(match.group(), "NN"))
                for match in re.finditer(r"\w+|[^\w\s]", text)
            ]
            for text in texts
        ]


def _ner(text: str, value: str, entity_type: str = "PERSON", *, nth: int = 0) -> Finding:
    start = [match.start() for match in re.finditer(re.escape(value), text)][nth]
    return Finding(
        entity_type=entity_type,
        start=start,
        end=start + len(value),
        score=0.95,
        source=FindingSource.NER,
        text=value,
        detector="gliner",
    )


TAGS = {"Sie": "PPER", "sie": "PPER", "Florian": "NE", "Eder": "NE", "der": "ART"}
TEXT = "Bitte zahlen Sie. Der Verbraucher und Florian Eder. Wien liegt an der Donau."


def test_word_class_of_a_span() -> None:
    tokens = FakeTagger(TAGS).tag([TEXT], "de")[0]  # type: ignore[index]
    formal_you = TEXT.index("Sie")
    assert word_class(TEXT, tokens, formal_you, formal_you + 3) is WordClass.CLOSED
    name = TEXT.index("Florian Eder")
    assert word_class(TEXT, tokens, name, name + 12) is WordClass.PROPER
    noun = TEXT.index("Verbraucher")
    assert word_class(TEXT, tokens, noun, noun + 11) is WordClass.COMMON


def test_pronouns_removed_common_nouns_remain_without_second_opinion() -> None:
    findings = (
        _ner(TEXT, "Sie"),
        _ner(TEXT, "Verbraucher"),
        _ner(TEXT, "Florian Eder"),
        # Locations are not checked for word class.
        _ner(TEXT, "Wien", "LOCATION"),
    )
    kept = without_non_names(TEXT, findings, FakeTagger(TAGS), language_at=lambda _: "de")
    assert [finding.text for finding in kept] == ["Verbraucher", "Florian Eder", "Wien"]


def test_common_nouns_are_dropped_only_with_confident_second_opinion() -> None:
    asked: list[str] = []

    def term_check(text: str, findings: Sequence[Finding]) -> list[bool]:
        asked.extend(finding.text or "" for finding in findings)
        return [finding.text == "Verbraucher" for finding in findings]

    findings = (_ner(TEXT, "Verbraucher"), _ner(TEXT, "Donau"), _ner(TEXT, "Florian Eder"))
    kept = without_non_names(
        TEXT, findings, FakeTagger(TAGS), language_at=lambda _: "de", term_check=term_check
    )
    # No one asks about proper names; a common noun without a confident judgment stays.
    assert asked == ["Verbraucher", "Donau"]
    assert [finding.text for finding in kept] == ["Donau", "Florian Eder"]


def test_without_language_pack_everything_remains() -> None:
    findings = (_ner(TEXT, "Sie"),)
    tagger = FakeTagger(TAGS, languages=("de",))
    kept = without_non_names(
        TEXT, findings, tagger, language_at=lambda _: "en", term_check=lambda *_: [True]
    )
    assert kept == findings
    assert tagger.calls == [("en", 1)]


def test_rules_and_master_data_do_not_check_word_class() -> None:
    rule = Finding("PERSON", 0, 5, 1.0, FindingSource.MASTER_DATA, text="Bitte")
    kept = without_non_names(TEXT, (rule,), FakeTagger(TAGS), language_at=lambda _: "de")
    assert kept == (rule,)


class StubNer:
    def __init__(self, findings: Sequence[Finding]) -> None:
        self.findings = tuple(findings)

    def detect(self, text: str) -> tuple[Finding, ...]:
        return self.findings


def test_a_rejected_pronoun_generates_no_repetition_questions() -> None:
    text = "Bitte zahlen Sie sofort. Wenn Sie zahlen, ist sie erledigt. Florian Eder"
    ner = StubNer((_ner(text, "Sie"), _ner(text, "Florian Eder")))
    without = DetectionPipeline(ner=ner).analyze(text, suspicion_enabled=False)
    assert any(item.detector == EXACT_DUPLICATE_DETECTOR for item in without.suspicious)

    result = DetectionPipeline(ner=ner, word_classes=FakeTagger(TAGS)).analyze(
        text, suspicion_enabled=False
    )
    assert [finding.text for finding in result.confirmed] == ["Florian Eder"]
    assert result.suspicious == ()


# -- Second opinion -----------------------------------------------------------------


def _distribution(**values: float) -> list[float]:
    return [values.get(label, 0.0) for label in LABELS]


class LabelModel:
    """Fixed distribution per label; records batch sizes."""

    folder = Path("/nirgends")
    version = "label-1"

    def __init__(self, distributions: dict[str, list[float]]) -> None:
        self.distributions = distributions
        self.batch_sizes: list[int] = []

    def probabilities(self, inputs: Sequence[str], *, batch_size: int = 32) -> list[list[float]]:
        self.batch_sizes.append(batch_size)
        return [self.distributions[item.split("|")[0].split(": ", 1)[1].strip()] for item in inputs]


def test_term_check_decides_individually_and_only_by_policy() -> None:
    from presidio_sidecar.assessments import SecondOpinionOptions, term_check_for

    model = LabelModel(
        {
            "Verbraucher": _distribution(term=0.95, person=0.05),
            "Kranebitter": _distribution(term=0.75, person=0.25),
        }
    )
    assert term_check_for(model, SecondOpinionOptions("suggest", 0.95)) is None  # type: ignore[arg-type]
    check = term_check_for(model, SecondOpinionOptions("protect", 0.95))  # type: ignore[arg-type]
    assert check is not None
    text = "Der Verbraucher und Kranebitter"
    answers = check(text, [_ner(text, "Verbraucher"), _ner(text, "Kranebitter")])
    # Below 80 % confidence, a common noun remains a name.
    assert answers == [True, False]
    # Reproducible: no batch size that would shift the decision.
    assert model.batch_sizes == [1]


def test_repetition_of_pure_uppercase_suspicion_can_be_kept_by_ai() -> None:
    from presidio_sidecar.assessments import SecondOpinionOptions, with_assessments
    from presidio_sidecar.base_profile import base_profile
    from presidio_sidecar.detection import DetectionResult
    from presidio_sidecar.processing import FindingKind, finding_id

    text = "Zuständige Aufsichtsbehörde: FMA. Die zuständige Aufsichtsbehörde prüft. Hans Meier."

    def suspicion(value: str, detector: str, *, nth: int = 0) -> Finding:
        start = [match.start() for match in re.finditer(re.escape(value), text)][nth]
        return Finding(
            "PERSON", start, start + len(value), 0.25, FindingSource.SUSPICION,
            text=value, detector=detector,
        )  # fmt: skip

    hans = [m.start() for m in re.finditer("Hans Meier", text)][0]
    detection = DetectionResult(
        confirmed=(Finding("PERSON", hans, hans + 10, 0.5, FindingSource.NER, text="Hans Meier"),),
        suspicious=(
            suspicion("Zuständige Aufsichtsbehörde", NAME_SUSPICION_DETECTOR),
            suspicion("zuständige Aufsichtsbehörde", EXACT_DUPLICATE_DETECTOR),
        ),
    )
    model = LabelModel(
        {
            "Zuständige Aufsichtsbehörde": _distribution(term=0.95, person=0.05),
            "zuständige Aufsichtsbehörde": _distribution(term=0.95, person=0.05),
            "Hans Meier": _distribution(term=0.9, person=0.1),
        }
    )
    result = with_assessments(
        text,
        detection,
        base_profile(),
        model,  # type: ignore[arg-type]
        SecondOpinionOptions(policy="protect", keep_threshold=0.95),
    )
    by_text = {
        finding.text: result.assessments.get(finding_id(finding, kind=kind))
        for kind, findings in (
            (FindingKind.CONFIRMED, result.confirmed),
            (FindingKind.SUSPICIOUS, result.suspicious),
        )
        for finding in findings
    }
    duplicate = by_text["zuständige Aufsichtsbehörde"]
    assert duplicate is not None and duplicate.decided and duplicate.suggestion == "reject"
    # A model hit is still a question even if the AI labels it as a term.
    hans_meier = by_text["Hans Meier"]
    assert hans_meier is not None and not hans_meier.decided


# -- Real language packages --------------------------------------------------------


def _installed_language(language: str) -> str | None:
    from presidio_sidecar import resources

    return (
        resources.spacy_model_reference(language) if resources.spacy_available(language) else None
    )


@pytest.mark.skipif(_installed_language("de") is None, reason="deutsches Sprachpaket fehlt")
def test_real_tagger_separates_role_pronouns_and_names() -> None:
    text = (
        "Bitte zahlen Sie die erste Prämie sofort. Ist der Versicherungsnehmer Verbraucher, "
        "so wird der Vertrag verlängert. Wien, am 25.08.2026, Florian Eder, Michael Fitz"
    )
    tagger = SpacyWordClassTagger(_installed_language)
    findings = tuple(
        _ner(text, value)
        for value in ("Sie", "Versicherungsnehmer", "Florian Eder", "Michael Fitz")
    )
    kept = without_non_names(
        text,
        findings,
        tagger,
        language_at=lambda _: "de",
        term_check=lambda _text, items: [True] * len(items),
    )
    assert [finding.text for finding in kept] == ["Florian Eder", "Michael Fitz"]
