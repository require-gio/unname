"""Suspicion check closes the known recall gap."""

from __future__ import annotations

import pytest

from presidio_sidecar.detection import (
    DetectionPipeline,
    Finding,
    FindingSource,
    find_name_suspicions,
)
from presidio_sidecar.detection import pipeline as detection_pipeline


class EmptyNer:
    def detect(self, text: str) -> tuple[Finding, ...]:
        return ()


class SampleNer:
    def detect(self, text: str) -> tuple[Finding, ...]:
        start = text.index("Muster")
        return (
            Finding(
                "PERSON",
                start,
                start + len("Muster"),
                0.99,
                FindingSource.NER,
                text="Muster",
            ),
        )


class FirstLocationOnlyNer:
    def detect(self, text: str) -> tuple[Finding, ...]:
        value = "LG-Wels"
        start = text.index(value)
        return (
            Finding(
                "LOCATION",
                start,
                start + len(value),
                0.73,
                FindingSource.NER,
                text=value,
                detector="first-only",
            ),
        )


class EveryLocationNer:
    def detect(self, text: str) -> tuple[Finding, ...]:
        value = "LG-Wels"
        first = text.index(value)
        second = text.index(value, first + len(value))
        return tuple(
            Finding(
                "LOCATION",
                start,
                start + len(value),
                0.73,
                FindingSource.NER,
                text=value,
                detector="all",
            )
            for start in (first, second)
        )


def test_reference_case_wachtelhuber_brothers_detected_as_suspicion() -> None:
    text = "Die Rechnung geht an Gebrüder Wachtelhuber."

    findings = find_name_suspicions(text)

    assert len(findings) == 1
    assert findings[0].text == "Gebrüder Wachtelhuber"
    assert findings[0].source == FindingSource.SUSPICION


def test_sentence_initial_stopwords_prevent_false_positives() -> None:
    text = "Vielen Dank. Betreff Rechnung."

    assert find_name_suspicions(text) == ()


def test_sentence_starters_do_not_hide_names_behind_them() -> None:
    findings = find_name_suspicions("Bitte Gebrüder Wachtelhuber")

    assert len(findings) == 1
    assert findings[0].text == "Gebrüder Wachtelhuber"


def test_multi_word_phrase_is_cut_off_from_following_name() -> None:
    findings = find_name_suspicions("Vielen Dank Max Mustermann")

    assert [finding.text for finding in findings] == ["Max Mustermann"]


def test_non_breaking_space_joins_names_but_newline_does_not() -> None:
    assert [finding.text for finding in find_name_suspicions("Max\u00a0Mustermann")] == [
        "Max\u00a0Mustermann"
    ]
    assert find_name_suspicions("Max\nMustermann") == ()


def test_confirmed_span_not_reported_again_as_suspicion() -> None:
    text = "Gebrüder Wachtelhuber"
    confirmed = Finding(
        "ORGANIZATION",
        0,
        len(text),
        0.88,
        FindingSource.NER,
        text=text,
    )

    assert find_name_suspicions(text, (confirmed,)) == ()


def test_punctuation_between_words_forms_no_name_run() -> None:
    assert find_name_suspicions("Rechnung. Firma.") == ()


def test_newline_does_not_connect_name_with_next_field() -> None:
    findings = find_name_suspicions("Gebrüder Wachtelhuber\nE-Mail")

    assert [finding.text for finding in findings] == ["Gebrüder Wachtelhuber"]


def test_job_titles_and_cloud_services_in_resume_not_name_suspicion() -> None:
    text = (
        "AI Software Architect 08/2025 – heute. Full Stack Developer. "
        "Tools: Azure AI, Azure DevOps, Azure Data Factory. Ansprechpartner Gebrüder Wachtelhuber"
    )

    findings = find_name_suspicions(text)

    assert [finding.text for finding in findings] == ["Ansprechpartner Gebrüder Wachtelhuber"]


def test_bank_and_legal_form_turn_suspicion_into_company() -> None:
    findings = find_name_suspicions(
        "Uneos AG (Volksbank Freiburg) Freiburg, Partner Kanzlei Beispiel GmbH, Hans Meier"
    )

    assert [(finding.entity_type, finding.text) for finding in findings] == [
        ("ORGANIZATION", "Uneos AG"),
        ("ORGANIZATION", "Volksbank Freiburg"),
        ("ORGANIZATION", "Partner Kanzlei Beispiel GmbH"),
        ("PERSON", "Hans Meier"),
    ]


def test_tabulator_does_not_connect_table_cells() -> None:
    # Table cells are separated by tabs in the source text.
    assert find_name_suspicions("Variable\tBedeutung\nOLLAMA_HOST\tAdresse") == ()
    findings = find_name_suspicions("Kunde\tGebrüder Wachtelhuber")
    assert [finding.text for finding in findings] == ["Gebrüder Wachtelhuber"]


def test_pipeline_separates_confirmed_and_suspicious_categories() -> None:
    text = "Gebrüder Wachtelhuber"

    result = DetectionPipeline(EmptyNer()).analyze(text)

    assert result.confirmed == ()
    assert [finding.text for finding in result.suspicious] == [text]


def test_pipeline_suppresses_patterns_despite_high_ner_score() -> None:
    result = DetectionPipeline(SampleNer()).analyze("Muster")

    assert result.confirmed == ()
    assert result.suspicious == ()


def test_pipeline_includes_previous_stages_in_coverage() -> None:
    text = "Gebrüder Wachtelhuber"
    master_data = Finding(
        "ORGANIZATION",
        0,
        len(text),
        1.0,
        FindingSource.MASTER_DATA,
        text=text,
    )

    result = DetectionPipeline(EmptyNer()).analyze(text, confirmed=(master_data,))

    assert result.confirmed == (master_data,)
    assert result.suspicious == ()


def test_pipeline_reports_ner_and_suspicion_phase_to_job_progress() -> None:
    phases: list[str] = []

    DetectionPipeline(EmptyNer()).analyze("Text", progress=phases.append)

    assert phases == ["ner", "suspicion"]


def test_pipeline_adds_skipped_exact_repetition_to_review() -> None:
    text = "Registergericht: LG-Wels\nFooter: LG-Wels"

    result = DetectionPipeline(FirstLocationOnlyNer()).analyze(text)

    assert [(finding.entity_type, finding.text) for finding in result.confirmed] == [
        ("LOCATION", "LG-Wels")
    ]
    assert [
        (finding.entity_type, finding.text, finding.source, finding.detector)
        for finding in result.suspicious
    ] == [("LOCATION", "LG-Wels", FindingSource.SUSPICION, "exact_duplicate")]
    assert result.suspicious[0].start == text.rindex("LG-Wels")


def test_pipeline_does_not_duplicate_already_detected_occurrences() -> None:
    result = DetectionPipeline(EveryLocationNer()).analyze("LG-Wels und LG-Wels")

    assert len(result.confirmed) == 2
    assert result.suspicious == ()


def test_pipeline_adds_literal_substring_to_review_like_residual_text_check() -> None:
    text = "Ort: LG-Wels; Produkt: LG-Welsberg"

    result = DetectionPipeline(FirstLocationOnlyNer()).analyze(text)

    assert len(result.confirmed) == 1
    assert [(finding.text, finding.start) for finding in result.suspicious] == [
        ("LG-Wels", text.rindex("LG-Wels"))
    ]


@pytest.mark.parametrize(
    ("value", "duplicate"),
    [
        ("Google", "GOOGLE"),
        ("Budapest", "BUDAPEST"),
        ("Hans Meier", "Hans\n  Meier"),
        ("Hans Meier", "H a n s M e i e r"),
        ("Hans Meier", "HansMeier"),
        ("Straße", "STRASSE"),
        ("STRASSE", "Straße"),
        ("Café", "Cafe\u0301"),
        ("Firma", "Ｆｉｒｍａ"),
    ],
)
def test_repetition_search_normalizes_like_pdf_check_with_original_offsets(
    value: str, duplicate: str
) -> None:
    text = f"{value} | {duplicate}"
    seed = Finding("PERSON", 0, len(value), 0.9, FindingSource.NER, text=value)

    result = DetectionPipeline(EmptyNer()).analyze(text, confirmed=(seed,), suspicion_enabled=False)

    assert [(finding.text, finding.start, finding.end) for finding in result.suspicious] == [
        (duplicate, len(value) + 3, len(text))
    ]


def test_whitespace_tolerant_repetition_invents_no_name_in_email() -> None:
    text = "Erika Musterfrau | erikamusterfrau97@example.org"
    seed = Finding("PERSON", 0, 16, 0.9, FindingSource.NER, text="Erika Musterfrau")

    result = DetectionPipeline(EmptyNer()).analyze(text, confirmed=(seed,), suspicion_enabled=False)

    assert result.suspicious == ()


def test_partially_detected_repetition_sends_free_rest_to_review() -> None:
    text = "Firmensitz Wien | Firmensitz Wien"
    confirmed = (
        Finding("LOCATION", 11, 15, 0.9, FindingSource.NER, text="Wien"),
        Finding("PERSON", 18, len(text), 0.9, FindingSource.NER, text="Firmensitz Wien"),
    )

    result = DetectionPipeline(EmptyNer()).analyze(
        text, confirmed=confirmed, suspicion_enabled=False
    )

    assert [(finding.text, finding.start, finding.end) for finding in result.suspicious] == [
        ("Firmensitz", 0, 10)
    ]
    assert result.confirmed == confirmed


def test_duplicate_matcher_keeps_free_shorter_prefix_candidate() -> None:
    text = "AB|ABC|ABC"
    confirmed = (
        Finding("PERSON", 0, 2, 0.9, FindingSource.NER, text="AB"),
        Finding("PERSON", 3, 6, 0.9, FindingSource.NER, text="ABC"),
        Finding("PERSON", 9, 10, 0.9, FindingSource.NER, text="C"),
    )

    result = DetectionPipeline(EmptyNer()).analyze(
        text,
        confirmed=confirmed,
        suspicion_enabled=False,
    )

    assert [(finding.text, finding.start, finding.end) for finding in result.suspicious] == [
        ("AB", 7, 9)
    ]


def test_exact_repetition_remains_review_required_without_name_heuristic() -> None:
    text = "Registergericht: LG-Wels\nFooter: LG-Wels"

    result = DetectionPipeline(FirstLocationOnlyNer()).analyze(
        text,
        suspicion_enabled=False,
    )

    assert [(finding.entity_type, finding.detector) for finding in result.suspicious] == [
        ("LOCATION", "exact_duplicate")
    ]


def test_duplicate_review_misses_no_value_after_many_unique_seeds() -> None:
    values = [f"Wert-{index:04d}" for index in range(513)]
    text = " ".join((*values, values[-1]))
    confirmed: list[Finding] = []
    search_from = 0
    for value in values:
        start = text.index(value, search_from)
        confirmed.append(
            Finding(
                "PERSON",
                start,
                start + len(value),
                0.99,
                FindingSource.NER,
                text=value,
            )
        )
        search_from = start + len(value)

    result = DetectionPipeline(EmptyNer()).analyze(
        text,
        confirmed=confirmed,
        suspicion_enabled=False,
    )

    assert [(finding.text, finding.start) for finding in result.suspicious] == [
        (values[-1], text.rindex(values[-1]))
    ]


def test_duplicate_review_does_not_silently_cut_many_repetitions() -> None:
    value = "LG-Wels"
    occurrence_count = 2_050
    text = " ".join([value] * occurrence_count)
    confirmed = Finding(
        "LOCATION",
        0,
        len(value),
        0.73,
        FindingSource.NER,
        text=value,
    )

    result = DetectionPipeline(EmptyNer()).analyze(
        text,
        confirmed=(confirmed,),
        suspicion_enabled=False,
    )

    assert len(result.suspicious) == occurrence_count - 1
    assert result.suspicious[-1].start == text.rindex(value)


def test_duplicate_review_reports_capacity_limit_instead_of_silently_cutting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = "LG-Wels"
    text = " ".join([value] * 3)
    confirmed = Finding(
        "LOCATION",
        0,
        len(value),
        0.73,
        FindingSource.NER,
        text=value,
    )
    monkeypatch.setattr(detection_pipeline, "_MAX_DUPLICATE_CANDIDATES", 1)

    with pytest.raises(detection_pipeline.DuplicateReviewCapacityError):
        DetectionPipeline(EmptyNer()).analyze(
            text,
            confirmed=(confirmed,),
            suspicion_enabled=False,
        )


def test_duplicate_matcher_caches_no_values_and_aborts_cleanly_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimedOutMatcher:
        def finditer(self, text: str, **options: object) -> None:
            assert text == "LG-Wels und LG-Wels"
            assert options["overlapped"] is True
            raise TimeoutError

    def compile_without_cache(pattern: str, **options: object) -> TimedOutMatcher:
        assert pattern == r"\L<duplicate_values>"
        assert options["duplicate_values"] == ("LG-Wels",)
        assert options["cache_pattern"] is False
        return TimedOutMatcher()

    monkeypatch.setattr(detection_pipeline.regex, "compile", compile_without_cache)

    with pytest.raises(detection_pipeline.DuplicateReviewCapacityError):
        list(
            detection_pipeline._iter_exact_matches(
                "LG-Wels und LG-Wels",
                {"LG-Wels": "LOCATION"},
            )
        )


def test_business_terms_break_a_name_sequence() -> None:
    """\"Ihr Online Team\" is a phrase, \"Firma Hans Meier\" stays a name."""

    assert find_name_suspicions("Es bediente Sie: Ihr Online Team") == ()
    assert find_name_suspicions("Ihr Online Team\nTel: 0664 1234567") == ()
    assert [finding.text for finding in find_name_suspicions("Firma Hans Meier")] == ["Hans Meier"]


def test_custom_business_terms_replace_the_default_list() -> None:
    assert [finding.text for finding in find_name_suspicions("Hans Meier")] == ["Hans Meier"]
    assert find_name_suspicions("Hans Meier", business_terms=["Meier"]) == ()


@pytest.mark.parametrize(
    "text",
    ["Jedes Holz zeigt Merkmale.", "PER POSTVERSAND", "ZUSTELLUNG ONLINESHOP"],
)
def test_pronouns_and_shipping_phrases_are_not_suspicious(text: str) -> None:
    assert find_name_suspicions(text) == ()


def test_uppercase_terms_in_product_line_are_not_suspicious() -> None:
    assert find_name_suspicions("ANTHRAZIT,GESTE:METALL SCHWARZ") == ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("MAX MUSTERMANN\nMUSTERGASSE 12/1/30", ["MAX MUSTERMANN"]),
        ("Lieferschein & Rechnung - MUSTERMANN MAX - 4QK2AB", ["MUSTERMANN MAX"]),
    ],
)
def test_name_in_uppercase_alone_remains_a_suspicion(text: str, expected: list[str]) -> None:
    assert [finding.text for finding in find_name_suspicions(text)] == expected
