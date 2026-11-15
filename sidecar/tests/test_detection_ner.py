"""NER can be tested offline: all model responses come from small fakes."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from presidio_sidecar.detection import (
    DetectionPipeline,
    EntityThresholds,
    FallbackNerAdapter,
    Finding,
    FindingSource,
    GLiNERNerAdapter,
    NerHardwareSelection,
    NerUnavailableError,
    SpacyGermanNerAdapter,
    build_ner_adapter,
    filter_ner_findings,
    select_ner_hardware,
)


class FakeGLiNERModel:
    def __init__(self, predictions: list[dict[str, object]]) -> None:
        self.predictions = predictions
        self.calls: list[tuple[str, tuple[str, ...], float, bool, bool]] = []

    def predict_entities(
        self,
        text: str,
        labels: tuple[str, ...],
        *,
        threshold: float,
        flat_ner: bool,
        multi_label: bool,
    ) -> list[dict[str, object]]:
        self.calls.append((text, tuple(labels), threshold, flat_ner, multi_label))
        return self.predictions


class SearchingGLiNERModel:
    def __init__(self, needle: str) -> None:
        self.needle = needle
        self.calls: list[str] = []

    def predict_entities(
        self,
        text: str,
        labels: tuple[str, ...],
        *,
        threshold: float,
        flat_ner: bool,
        multi_label: bool,
    ) -> list[dict[str, object]]:
        del labels, threshold, flat_ner, multi_label
        self.calls.append(text)
        start = text.find(self.needle)
        if start < 0:
            return []
        return [
            {
                "start": start,
                "end": start + len(self.needle),
                "label": "person",
                "score": 0.91,
            }
        ]


def test_gliner_lazy_loaded_with_hardware_selection() -> None:
    text = "Petra Novak"
    model = FakeGLiNERModel([{"start": 0, "end": len(text), "label": "person", "score": 0.91}])
    loader_calls: list[tuple[str, str]] = []

    def loader(model_name: str, device: str) -> FakeGLiNERModel:
        loader_calls.append((model_name, device))
        return model

    adapter = GLiNERNerAdapter(device="mps", loader=loader)
    assert loader_calls == [], "Import und Modellladung muessen lazy bleiben"

    finding = adapter.detect(text)[0]

    assert loader_calls == [(adapter.model_name, "mps")]
    assert finding == Finding(
        entity_type="PERSON",
        start=0,
        end=len(text),
        score=0.91,
        source=FindingSource.NER,
        text=text,
        detector="gliner",
    )
    assert model.calls[0][2:] == (0.35, False, True)


def test_gliner_chunking_detects_entity_beyond_model_window() -> None:
    needle = "Petra Novak"
    text = " ".join([*(f"wort{index}" for index in range(430)), needle, "schluss"])
    model = SearchingGLiNERModel(needle)
    adapter = GLiNERNerAdapter(
        loader=lambda model_name, device: model,
        max_chunk_words=200,
        chunk_overlap_words=40,
    )

    findings = adapter.detect(text)

    assert len(model.calls) >= 3
    assert [(finding.text, finding.start, finding.end) for finding in findings] == [
        (needle, text.index(needle), text.index(needle) + len(needle))
    ]


def test_gliner_chunking_deduplicates_hits_in_overlap_area() -> None:
    needle = "Petra Novak"
    words = [f"wort{index}" for index in range(390)]
    words[170:172] = needle.split()
    text = " ".join(words)
    model = SearchingGLiNERModel(needle)
    adapter = GLiNERNerAdapter(
        loader=lambda model_name, device: model,
        max_chunk_words=200,
        chunk_overlap_words=40,
    )

    findings = adapter.detect(text)

    assert sum(needle in chunk for chunk in model.calls) == 2
    assert len(findings) == 1
    assert findings[0].text == needle


def test_gliner_chunking_limits_extremely_long_token() -> None:
    needle = "PetraNovak"
    text = f"{'x' * 2_500}{needle}{'y' * 2_500}"
    model = SearchingGLiNERModel(needle)
    adapter = GLiNERNerAdapter(
        loader=lambda model_name, device: model,
        max_chunk_chars=600,
        chunk_overlap_chars=100,
    )

    findings = adapter.detect(text)

    assert max(map(len, model.calls)) <= 600
    assert len(findings) == 1
    assert findings[0].text == needle


def test_gliner_keeps_candidates_for_entity_specific_thresholds() -> None:
    text = "Meier Spielwaren GmbH"
    model = FakeGLiNERModel(
        [
            {
                "start": 0,
                "end": len(text),
                "label": "organization",
                "score": 0.40,
            },
            {
                "start": 0,
                "end": len("Meier"),
                "label": "person",
                "score": 0.39,
            },
        ]
    )
    thresholds = EntityThresholds({"PERSON": 0.35, "ORGANIZATION": 0.75})
    adapter = GLiNERNerAdapter(
        loader=lambda model_name, device: model,
        thresholds=thresholds,
    )

    accepted = filter_ner_findings(
        text,
        adapter.detect(text),
        thresholds=thresholds,
        business_terms=(),
    )

    assert [(finding.entity_type, finding.text) for finding in accepted] == [("PERSON", "Meier")]
    assert model.calls[0][2:] == (0.35, False, True)


def test_pipeline_passes_lower_profile_threshold_to_gliner_model() -> None:
    text = "Petra Novak"
    model = FakeGLiNERModel(
        [
            {
                "start": 0,
                "end": len(text),
                "label": "person",
                "score": 0.25,
            }
        ]
    )
    adapter = GLiNERNerAdapter(
        loader=lambda model_name, device: model,
        thresholds=EntityThresholds({"PERSON": 0.50}),
    )

    result = DetectionPipeline(
        ner=adapter,
        thresholds=EntityThresholds({"PERSON": 0.50}),
        business_terms=(),
    ).analyze(
        text,
        thresholds=EntityThresholds({"PERSON": 0.20}),
        suspicion_enabled=False,
    )

    assert [finding.text for finding in result.confirmed] == [text]
    assert model.calls[0][2] == 0.20


@dataclass
class FakeSpacyEntity:
    start_char: int
    end_char: int
    label_: str
    text: str
    score: float


@dataclass
class FakeSpacyDocument:
    ents: list[FakeSpacyEntity]


class FakeSpacyModel:
    def __init__(self, entities: list[FakeSpacyEntity]) -> None:
        self.entities = entities

    def __call__(self, text: str) -> FakeSpacyDocument:
        return FakeSpacyDocument(self.entities)


class SearchingSpacyModel:
    def __init__(self, needle: str) -> None:
        self.needle = needle
        self.calls: list[str] = []

    def __call__(self, text: str) -> FakeSpacyDocument:
        self.calls.append(text)
        start = text.find(self.needle)
        if start < 0:
            return FakeSpacyDocument([])
        return FakeSpacyDocument(
            [
                FakeSpacyEntity(
                    start,
                    start + len(self.needle),
                    "PER",
                    self.needle,
                    0.72,
                )
            ]
        )


def test_de_core_news_lg_lazy_and_maps_german_labels() -> None:
    text = "Wien"
    loader_calls: list[str] = []

    def loader(model_name: str) -> FakeSpacyModel:
        loader_calls.append(model_name)
        return FakeSpacyModel([FakeSpacyEntity(0, 4, "LOC", text, 0.72)])

    adapter = SpacyGermanNerAdapter(loader=loader)
    assert loader_calls == []
    finding = adapter.detect(text)[0]

    assert loader_calls == ["de_core_news_lg"]
    assert finding.entity_type == "LOCATION"
    assert finding.detector == "de_core_news_lg"


def test_spacy_chunking_processes_text_beyond_a_million_characters() -> None:
    needle = "Petra Novak"
    text = f"{'wort ' * 200_010}{needle} ende"
    model = SearchingSpacyModel(needle)
    adapter = SpacyGermanNerAdapter(loader=lambda model_name: model)

    findings = adapter.detect(text)

    assert len(text) > 1_000_000
    assert len(model.calls) > 10
    assert [(finding.text, finding.start, finding.end) for finding in findings] == [
        (needle, text.index(needle), text.index(needle) + len(needle))
    ]


def test_spacy_chunking_deduplicates_overlapping_hits() -> None:
    needle = "Petra Novak"
    text = f"{'wort ' * 30}{needle}{' ende' * 30}"
    model = SearchingSpacyModel(needle)
    adapter = SpacyGermanNerAdapter(
        loader=lambda model_name: model,
        max_chunk_chars=180,
        chunk_overlap_chars=80,
    )

    findings = adapter.detect(text)

    assert sum(needle in chunk for chunk in model.calls) >= 2
    assert len(findings) == 1
    assert findings[0].text == needle


class FailingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def detect(self, text: str) -> tuple[Finding, ...]:
        self.calls += 1
        raise RuntimeError("Modellartefakt fehlt")


class StaticAdapter:
    def __init__(self, findings: tuple[Finding, ...]) -> None:
        self.findings = findings
        self.calls = 0

    def detect(self, text: str) -> tuple[Finding, ...]:
        self.calls += 1
        return self.findings


def test_gliner_error_switches_permanently_to_spacy_fallback() -> None:
    primary = FailingAdapter()
    expected = (Finding("PERSON", 0, 4, 0.5, FindingSource.NER, text="Anna", detector="spacy"),)
    fallback = StaticAdapter(expected)
    adapter = FallbackNerAdapter(primary, fallback)

    assert adapter.detect("Anna") == expected
    assert adapter.detect("Anna") == expected
    assert primary.calls == 1
    assert fallback.calls == 2
    assert adapter.active_adapter is fallback
    assert isinstance(adapter.primary_error, RuntimeError)


def test_failure_of_both_ner_engines_is_content_free_and_fail_closed() -> None:
    secret = "Geheime Person Max Mustermann"

    class LeakingAdapter:
        def detect(self, text: str) -> tuple[Finding, ...]:
            raise RuntimeError(f"Modellfehler bei {text}")

    adapter = FallbackNerAdapter(LeakingAdapter(), LeakingAdapter())

    with pytest.raises(NerUnavailableError) as captured:
        adapter.detect(secret)

    assert secret not in str(captured.value)
    assert adapter.status().fallback_reason == "RuntimeError"


def test_factory_selects_gliner_by_hardware_without_loading_models() -> None:
    gliner_calls: list[tuple[str, str]] = []
    spacy_calls: list[str] = []

    def gliner_loader(model_name: str, device: str) -> FakeGLiNERModel:
        gliner_calls.append((model_name, device))
        return FakeGLiNERModel([])

    def spacy_loader(model_name: str) -> FakeSpacyModel:
        spacy_calls.append(model_name)
        return FakeSpacyModel([])

    adapter = build_ner_adapter(
        NerHardwareSelection(active_device="cuda"),
        gliner_loader=gliner_loader,
        spacy_loader=spacy_loader,
    )

    assert isinstance(adapter, FallbackNerAdapter)
    assert gliner_calls == []
    assert spacy_calls == []
    assert adapter.status().as_dict() == {
        "preferred": "gliner",
        "active": "gliner",
        "loaded": False,
        "fallback_reason": None,
        # The device of the active engine is independent of the hardware detection result:
        # a confirmed GPU does not guarantee the model runs on it.
        "device": "cuda",
    }
    assert adapter.detect("Text") == ()
    assert gliner_calls[0][1] == "cuda"
    assert spacy_calls == []
    assert adapter.status().loaded is True


def test_hardware_can_choose_resource_friendly_spacy_path() -> None:
    calls: list[str] = []

    def loader(model_name: str) -> FakeSpacyModel:
        calls.append(model_name)
        return FakeSpacyModel([])

    adapter = build_ner_adapter(
        NerHardwareSelection(prefer_gliner=False),
        spacy_loader=loader,
    )
    assert isinstance(adapter, SpacyGermanNerAdapter)
    assert adapter.detect("Text") == ()
    assert calls == ["de_core_news_lg"]


def test_ram_matrix_selects_spacy_at_four_gib_and_gliner_at_eight_gib() -> None:
    small = select_ner_hardware(4 * 1024**3)
    minimum = select_ner_hardware(8 * 1024**3)

    assert small.prefer_gliner is False
    assert minimum.active_device == "cpu"
    assert minimum.prefer_gliner is True
    assert minimum.memory_bytes == 8 * 1024**3


def test_checked_gpu_does_not_change_onnx_detection() -> None:
    # No PyTorch path any more; GLiNER always runs as ONNX on the CPU.
    selected = select_ner_hardware(4 * 1024**3, active_device="mps")

    assert selected.active_device == "cpu"
    assert selected.prefer_gliner is False


def test_fallback_callback_executed_exactly_once_on_first_switch() -> None:
    primary = FailingAdapter()
    fallback = StaticAdapter(())
    errors: list[Exception] = []
    adapter = FallbackNerAdapter(primary, fallback, on_fallback=errors.append)

    assert adapter.detect("eins") == ()
    assert adapter.detect("zwei") == ()

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)


def test_fallback_status_shows_active_engine_and_reason() -> None:
    def broken_gliner(model_name: str, device: str) -> FakeGLiNERModel:
        raise RuntimeError(f"{model_name} fehlt auf {device}")

    def spacy_loader(model_name: str) -> FakeSpacyModel:
        return FakeSpacyModel([])

    adapter = build_ner_adapter(
        NerHardwareSelection(memory_bytes=16 * 1024**3),
        gliner_loader=broken_gliner,
        spacy_loader=spacy_loader,
    )
    assert isinstance(adapter, FallbackNerAdapter)

    assert adapter.detect("Text") == ()
    status = adapter.status()

    assert status.preferred == "gliner"
    assert status.active == "de_core_news_lg"
    assert status.loaded is True
    assert status.fallback_reason == "RuntimeError"
    assert "fehlt" not in status.fallback_reason


def test_fallback_status_outputs_no_document_content() -> None:
    secret = "Kundengeheimnis-4711"
    primary = FailingAdapter()

    def detect_with_document_leak(text: str) -> tuple[Finding, ...]:
        raise RuntimeError(f"Inferenzfehler bei {text}")

    primary.detect = detect_with_document_leak  # type: ignore[method-assign]
    adapter = FallbackNerAdapter(primary, StaticAdapter(()))

    assert adapter.detect(secret) == ()
    assert adapter.status().fallback_reason == "RuntimeError"
    assert secret not in str(adapter.status().as_dict())


def test_thresholds_apply_per_entity() -> None:
    findings = (
        Finding("PERSON", 0, 4, 0.40, FindingSource.NER, text="Anna"),
        Finding("ORGANIZATION", 5, 9, 0.40, FindingSource.NER, text="Acme"),
    )
    thresholds = EntityThresholds({"PERSON": 0.35, "ORGANIZATION": 0.75})

    accepted = filter_ner_findings(
        "Anna Acme",
        findings,
        thresholds=thresholds,
        business_terms=(),
    )

    assert [finding.entity_type for finding in accepted] == ["PERSON"]


def test_business_term_pattern_suppressed_after_ner() -> None:
    finding = Finding("PERSON", 0, 6, 0.99, FindingSource.NER, text="Muster")

    assert filter_ner_findings("Muster", (finding,)) == ()


@pytest.mark.parametrize("heading", ["Ort", "ORT", "Place"])
def test_statement_column_header_is_not_location(heading: str) -> None:
    text = f"{heading}\nWien"
    findings = (
        Finding("LOCATION", 0, len(heading), 0.99, FindingSource.NER, text=heading),
        Finding("LOCATION", len(heading) + 1, len(text), 0.9, FindingSource.NER, text="Wien"),
    )
    language = "en" if heading == "Place" else "de"
    accepted = filter_ner_findings(text, findings, language=language)
    assert [finding.text for finding in accepted] == ["Wien"]


def test_job_title_made_of_terms_is_not_a_name() -> None:
    text = "Software Engineer, Full-Stack Engineer, Hans Engineer"
    findings = (
        Finding("PERSON", 0, 17, 0.80, FindingSource.NER, text="Software Engineer"),
        Finding("PERSON", 19, 38, 0.80, FindingSource.NER, text="Full-Stack Engineer"),
        Finding("PERSON", 40, 53, 0.80, FindingSource.NER, text="Hans Engineer"),
    )

    accepted = filter_ner_findings(
        text, findings, business_terms={"software", "engineer", "full-stack"}
    )

    assert [finding.text for finding in accepted] == ["Hans Engineer"]


def test_person_name_with_digit_rejected() -> None:
    text = "Technische Leitung (5 Entwickler"
    findings = (
        Finding("PERSON", 0, len(text), 0.48, FindingSource.NER, text=text),
        Finding("ORGANIZATION", 0, 4, 0.90, FindingSource.NER, text="Tech"),
    )

    accepted = filter_ner_findings(text, findings, business_terms=())

    assert [finding.entity_type for finding in accepted] == ["ORGANIZATION"]


def test_stoplist_compares_nfkc_and_case_insensitive() -> None:
    full_width = "ＭＵＳＴＥＲ"
    finding = Finding(
        "PERSON",
        0,
        len(full_width),
        0.99,
        FindingSource.NER,
        text=full_width,
    )

    assert (
        filter_ner_findings(
            full_width,
            (finding,),
            business_terms={"muster"},
        )
        == ()
    )
