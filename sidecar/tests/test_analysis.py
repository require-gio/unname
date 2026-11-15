"""Integration of levels 0 to 3."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from presidio_analyzer import Pattern, PatternRecognizer

from presidio_sidecar.analysis import PlainTextAnalyzer
from presidio_sidecar.detection import (
    DetectionPipeline,
    Finding,
    FindingSource,
    SpacyGermanNerAdapter,
)
from presidio_sidecar.profiles import load_profile
from presidio_sidecar.registry import MasterDataRegistry, RegistrySources
from presidio_sidecar.registry_crypto import generate_registry_key


class EmptyNer:
    detector_name = "test-empty"

    def detect(self, text: str):  # noqa: ANN201
        return ()


class SampleNer:
    detector_name = "test-muster"

    def detect(self, text: str):  # noqa: ANN201
        start = text.index("Muster")
        return (
            Finding(
                entity_type="PERSON",
                start=start,
                end=start + len("Muster"),
                score=0.99,
                source=FindingSource.NER,
            ),
        )


class OverlappingPersonNer:
    detector_name = "test-overlap"

    def detect(self, text: str):  # noqa: ANN201
        value = "Hans Meier"
        start = text.index(value)
        return (
            Finding(
                entity_type="PERSON",
                start=start,
                end=start + len(value),
                score=0.50,
                source=FindingSource.NER,
                text=value,
            ),
        )


def _registry(tmp_path: Path) -> MasterDataRegistry:
    persons = tmp_path / "personen.json"
    persons.write_text(json.dumps(["Josef Brandstetter"]), encoding="utf-8")
    organizations = tmp_path / "organisationen.json"
    organizations.write_text(
        json.dumps(["Tischlerei Josef Brandstetter e.U."]),
        encoding="utf-8",
    )
    registry = MasterDataRegistry(
        tmp_path / "registry.enc",
        generate_registry_key(),
    )
    registry.reimport(RegistrySources(persons, organizations))
    return registry


def test_milestone_one_runs_all_four_stages(tmp_path: Path) -> None:
    analyzer = PlainTextAnalyzer(
        _registry(tmp_path),
        semantic_pipeline=DetectionPipeline(ner=EmptyNer()),
    )
    phases: list[str] = []
    text = "Tischlerei Josef Brandstetter e.U.\nGebrüder Wachtelhuber\nE-Mail: info@example.at"

    result = analyzer.analyze(text, progress=phases.append)

    assert phases == ["master_data", "rules", "ner", "suspicion"]
    assert any(
        finding.text == "Tischlerei Josef Brandstetter e.U."
        and finding.source == FindingSource.MASTER_DATA
        for finding in result.confirmed
    )
    assert any(
        finding.text == "info@example.at" and finding.source == FindingSource.RULE
        for finding in result.confirmed
    )
    assert any(
        finding.text == "Gebrüder Wachtelhuber" and finding.source == FindingSource.SUSPICION
        for finding in result.suspicious
    )


def test_pattern_removed_from_ner_hits(tmp_path: Path) -> None:
    analyzer = PlainTextAnalyzer(
        _registry(tmp_path),
        semantic_pipeline=DetectionPipeline(ner=SampleNer()),
    )

    result = analyzer.analyze("Muster", progress=lambda _: None)

    assert result.confirmed == ()
    assert result.suspicious == ()


@pytest.mark.parametrize(
    "secret",
    [
        "Sehr-Geheim-2026!",
        "!Sehr-Geheim-2026!",
    ],
)
def test_rule_pipeline_keeps_punctuation_in_secrets(
    tmp_path: Path,
    secret: str,
) -> None:
    analyzer = PlainTextAnalyzer(
        _registry(tmp_path),
        semantic_pipeline=DetectionPipeline(ner=EmptyNer()),
    )

    result = analyzer.analyze(
        f"Passwort: '{secret}'",
        progress=lambda _: None,
    )

    assert [finding.text for finding in result.confirmed if finding.entity_type == "SECRET"] == [
        secret
    ]


def test_rule_pipeline_normalizes_only_phone_wrapper(
    tmp_path: Path,
) -> None:
    analyzer = PlainTextAnalyzer(
        _registry(tmp_path),
        semantic_pipeline=DetectionPipeline(ner=EmptyNer()),
    )
    text = "Tel.: (+43 1 2345678). Telefon: (030) 1234567"

    result = analyzer.analyze(text, progress=lambda _: None)

    assert [
        finding.text for finding in result.confirmed if finding.entity_type == "PHONE_NUMBER"
    ] == ["+43 1 2345678", "(030) 1234567"]


def test_profile_ner_threshold_removes_no_deterministic_rule(
    tmp_path: Path,
) -> None:
    low_rule = PatternRecognizer(
        supported_entity="PHONE_NUMBER",
        patterns=[Pattern("test-overlap", r"Hans Meier", 0.54)],
        supported_language="de",
    )
    analyzer = PlainTextAnalyzer(
        _registry(tmp_path),
        deterministic_recognizers=(low_rule,),
        semantic_pipeline=DetectionPipeline(ner=OverlappingPersonNer()),
    )
    profile = load_profile(Path(__file__).resolve().parents[2] / "profiles/de-at-rechnung-v3.yaml")

    result = analyzer.analyze_for_profile(
        "Hans Meier",
        profile,
        progress=lambda _: None,
    )

    assert [(item.entity_type, item.text) for item in result.confirmed] == [
        ("PHONE_NUMBER", "Hans Meier")
    ]


@pytest.mark.parametrize(
    "profile_name",
    [
        "de-at-rechnung-v3.yaml",
        "kanzlei-beratung-v1.yaml",
        "llm-upload-v1.yaml",
    ],
)
def test_de_at_phone_remains_in_every_start_profile_in_review(
    tmp_path: Path,
    profile_name: str,
) -> None:
    analyzer = PlainTextAnalyzer(
        _registry(tmp_path),
        semantic_pipeline=DetectionPipeline(ner=EmptyNer()),
    )
    profile = load_profile(Path(__file__).resolve().parents[2] / "profiles" / profile_name)

    # Without a label the match stays at the base score 0.4 and must not disappear at the profile
    # thresholds.
    # After "Telefon:" it would be unambiguous (see test_detection_context.py), also with country
    # code.
    result = analyzer.analyze_for_profile(
        "Rückruf unter 030 12345678",
        profile,
        progress=lambda _: None,
    )

    assert [
        (item.entity_type, item.text, item.score)
        for item in result.confirmed
        if item.entity_type == "PHONE_NUMBER"
    ] == [("PHONE_NUMBER", "030 12345678", pytest.approx(0.4))]


def test_milestone_one_runs_with_installed_spacy_model(
    tmp_path: Path,
) -> None:
    ner = SpacyGermanNerAdapter()
    analyzer = PlainTextAnalyzer(
        _registry(tmp_path),
        semantic_pipeline=DetectionPipeline(ner=ner),
    )
    phases: list[str] = []
    text = (
        "Tischlerei Josef Brandstetter e.U.\nGebrüder Wachtelhuber\nMuster\nE-Mail: info@example.at"
    )

    result = analyzer.analyze(text, progress=phases.append)

    assert ner.loaded is True
    assert phases == ["master_data", "rules", "ner", "suspicion"]
    assert any(
        finding.text == "Tischlerei Josef Brandstetter e.U."
        and finding.source == FindingSource.MASTER_DATA
        for finding in result.confirmed
    )
    assert [finding.text for finding in result.suspicious] == ["Gebrüder Wachtelhuber"]
    assert all(finding.text != "Muster" for finding in result.confirmed)
