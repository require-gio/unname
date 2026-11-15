"""Integration of the four detection levels for plain text."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from presidio_analyzer import EntityRecognizer, RecognizerResult

from .detection import (
    DetectionPipeline,
    DetectionResult,
    EntityThresholds,
    Finding,
    FindingSource,
    apply_rule_context,
)
from .detection.language import segment_languages
from .detection.models import LanguageSegment
from .detection.word_classes import TermCheck
from .deterministic_recognizers import create_deterministic_recognizers
from .diagnostics import DiagnosticsRecorder
from .profiles import Profile, runtime_entity_type
from .registry import MasterDataRegistry


@dataclass(frozen=True, slots=True)
class LanguageAnalysis:
    """Document languages for a detection, fully prepared.

    ``function_words`` contains exactly the enabled languages; the first
    is the default. The word lists are normalized.
    """

    function_words: Mapping[str, frozenset[str]]
    default: str
    per_paragraph: bool = True
    sentence_starters: Mapping[str, frozenset[str]] = field(default_factory=dict)
    business_terms: Mapping[str, frozenset[str]] = field(default_factory=dict)
    thresholds: Mapping[str, EntityThresholds] = field(default_factory=dict)

    def segments(self, text: str) -> tuple[LanguageSegment, ...]:
        return segment_languages(
            text,
            self.function_words,
            default=self.default,
            per_paragraph=self.per_paragraph,
        )


class PlainTextAnalyzer:
    """Combines master data, rules, NER and suspicion checking."""

    def __init__(
        self,
        master_data: MasterDataRegistry,
        *,
        deterministic_recognizers: Iterable[EntityRecognizer] | None = None,
        semantic_pipeline: DetectionPipeline | None = None,
        diagnostics: DiagnosticsRecorder | None = None,
    ) -> None:
        self.master_data = master_data
        self.deterministic_recognizers = tuple(
            deterministic_recognizers
            if deterministic_recognizers is not None
            else create_deterministic_recognizers()
        )
        self.semantic_pipeline = semantic_pipeline or DetectionPipeline()
        self.diagnostics = diagnostics
        # Viele Dokument-Worker duerfen Regel-PII parallel bearbeiten. Das
        # gemeinsam geladene NER-Modell bekommt dagegen serialisierte Aufrufe.
        self._ner_lock = threading.Lock()
        self._reported_fallback = False

    def analyze(
        self,
        text: str,
        *,
        progress: Callable[[str], None],
    ) -> DetectionResult:
        progress("master_data")
        master_findings = _run_recognizers(
            text,
            self.master_data.recognizers(),
            source=FindingSource.MASTER_DATA,
        )

        progress("rules")
        rule_findings = _run_recognizers(
            text,
            self.deterministic_recognizers,
            source=FindingSource.RULE,
        )

        _report_interim(progress, (*master_findings, *rule_findings))
        with self._ner_lock:
            result = self.semantic_pipeline.analyze(
                text,
                confirmed=(*master_findings, *rule_findings),
                progress=progress,
                on_resolved=lambda findings: _report_interim(progress, findings),
            )
            self._record_primary_fallback_once()
        return result

    def analyze_for_profile(
        self,
        text: str,
        profile: Profile,
        *,
        progress: Callable[[str], None],
        master_data: MasterDataRegistry | None = None,
        deterministic_recognizers: Iterable[EntityRecognizer] | None = None,
        sentence_starters: Iterable[str] | None = None,
        languages: LanguageAnalysis | None = None,
        term_check: TermCheck | None = None,
    ) -> DetectionResult:
        """Applies profile filter before overlap and suspicion check.

        ``term_check`` is the second opinion for NER hits that are not
        proper nouns according to their word class (``detection/word_classes.py``).
        """

        allowed_entities = set(profile.presidio_entities)
        for entity, rule in profile.thresholds.root.items():
            if entity != "default" and not rule.enabled:
                allowed_entities.discard(runtime_entity_type(entity))
        native_config = profile.to_presidio_analyzer_config()
        default_threshold = profile.thresholds.default.needs_review
        assert default_threshold is not None
        threshold_values = {
            runtime_entity_type(entity): (
                rule.needs_review if rule.needs_review is not None else default_threshold
            )
            for entity, rule in profile.thresholds.root.items()
            if entity != "default" and rule.enabled
        }
        thresholds = EntityThresholds(
            threshold_values,
            default=default_threshold,
        )
        native_floor = float(native_config["default_score_threshold"])
        if native_floor != min((default_threshold, *threshold_values.values())):
            raise ValueError(
                "Native Presidio-Konfiguration und Profilschwellen widersprechen sich."
            )

        progress("master_data")
        active_master_data = master_data or self.master_data
        master_findings = (
            _run_recognizers(
                text,
                active_master_data.recognizers(),
                source=FindingSource.MASTER_DATA,
            )
            if profile.master_data.enabled
            else ()
        )

        progress("rules")
        rule_findings = _run_recognizers(
            text,
            (
                deterministic_recognizers
                if deterministic_recognizers is not None
                else self.deterministic_recognizers
            ),
            source=FindingSource.RULE,
        )
        base_findings = tuple(
            finding
            for finding in (*master_findings, *rule_findings)
            if runtime_entity_type(finding.entity_type) in allowed_entities
        )

        _report_interim(progress, base_findings)
        segments = languages.segments(text) if languages is not None else ()
        language_thresholds = (
            {
                language: EntityThresholds(
                    {**thresholds.values, **calibration.values},
                    default=thresholds.default,
                )
                for language, calibration in languages.thresholds.items()
            }
            if languages is not None
            else None
        )
        with self._ner_lock:
            result = self.semantic_pipeline.analyze(
                text,
                confirmed=base_findings,
                progress=progress,
                on_resolved=lambda findings: _report_interim(progress, findings),
                allowed_entities=allowed_entities,
                thresholds=thresholds,
                suspicion_enabled=profile.suspect_check.enabled,
                sentence_starters=sentence_starters,
                languages=segments,
                sentence_starters_by_language=(
                    languages.sentence_starters if languages is not None else None
                ),
                business_terms_by_language=(
                    languages.business_terms if languages is not None else None
                ),
                language_thresholds=language_thresholds,
                term_check=term_check,
            )
            self._record_primary_fallback_once()
        return result

    def warm_up(self) -> None:
        """Loads the NER model before the first document waits for it.

        Under the same lock as a real analysis: Two threads touching
        an unloaded model simultaneously would both load it and double
        the memory usage.
        """

        with self._ner_lock:
            self.semantic_pipeline.ner.detect("Anna Beispiel arbeitet in Wien.")

    def nlp_status(self) -> dict[str, Any]:
        """Lazy model state for ``GET /health``; claims no availability."""

        ner = self.semantic_pipeline.ner
        status_method = getattr(ner, "status", None)
        if callable(status_method):
            status = dict(status_method())
        else:
            active = getattr(getattr(ner, "active_adapter", ner), "detector_name", "unknown")
            loaded = any(
                getattr(candidate, "_model", None) is not None
                for candidate in (
                    ner,
                    getattr(ner, "primary", None),
                    getattr(ner, "fallback", None),
                )
                if candidate is not None
            )
            status = {"active": active, "loaded": loaded}
        return {
            "preferred": "gliner",
            "fallback": "de_core_news_lg",
            **status,
        }

    def _record_primary_fallback_once(self) -> None:
        ner = self.semantic_pipeline.ner
        primary_error = getattr(ner, "primary_error", None)
        if primary_error is None or self._reported_fallback or self.diagnostics is None:
            return
        self._reported_fallback = True
        self.diagnostics.record(
            "model_load",
            "GLiNER war lokal nicht verfuegbar; spaCy-Fallback ist aktiv.",
            severity="warning",
            from_engine="gliner",
            to_engine="de_core_news_lg",
            error_type=type(primary_error).__name__,
        )


class ProfileAnalyzer:
    """Job runner that binds a validated profile firmly to an analysis."""

    def __init__(
        self,
        analyzer: PlainTextAnalyzer,
        profile: Profile,
        *,
        master_data: MasterDataRegistry | None = None,
        deterministic_recognizers: Iterable[EntityRecognizer] | None = None,
        sentence_starters: Iterable[str] | None = None,
        languages: LanguageAnalysis | None = None,
        second_opinion: Callable[[str, DetectionResult], DetectionResult] | None = None,
        term_check: TermCheck | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.languages = languages
        self.term_check = term_check
        #: Second opinion after the suspicion check: attaches the local
        #: AI's assessment to the questions.
        self.second_opinion = second_opinion
        self.profile = profile
        self.master_data = master_data
        self.deterministic_recognizers = (
            tuple(deterministic_recognizers) if deterministic_recognizers is not None else None
        )
        self.sentence_starters = tuple(sentence_starters) if sentence_starters is not None else None

    def analyze(
        self,
        text: str,
        *,
        progress: Callable[[str], None],
    ) -> DetectionResult:
        profile_options: dict[str, object] = {}
        if self.deterministic_recognizers is not None:
            profile_options["deterministic_recognizers"] = self.deterministic_recognizers
        if self.sentence_starters is not None:
            profile_options["sentence_starters"] = self.sentence_starters
        if self.languages is not None:
            profile_options["languages"] = self.languages
        if self.master_data is not None:
            profile_options["master_data"] = self.master_data
        if self.term_check is not None:
            profile_options["term_check"] = self.term_check
        result = self.analyzer.analyze_for_profile(
            text,
            self.profile,
            progress=progress,
            **profile_options,
        )
        if self.second_opinion is not None:
            result = self.second_opinion(text, result)
        return result


def _report_interim(progress: Callable[[str], None], findings: Iterable[Finding]) -> None:
    """Reports an interim count if the caller accepts it.

    The JobManager passes a progress object with ``interim``;
    a simple phase callback (tests, text mode) gets nothing of it.
    """

    interim = getattr(progress, "interim", None)
    if callable(interim):
        interim(tuple(findings))


def _run_recognizers(
    text: str,
    recognizers: Iterable[EntityRecognizer],
    *,
    source: FindingSource,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for recognizer in recognizers:
        results = recognizer.analyze(
            text,
            entities=list(recognizer.supported_entities),
            nlp_artifacts=None,
        )
        # Rules run without NLP artifacts; Presidio does not evaluate their
        # context words then. We make up for this here.
        context = getattr(recognizer, "context", None) if source is FindingSource.RULE else None
        for result in results:
            candidate = _to_finding(text, recognizer, result, source)
            if candidate is not None and source is FindingSource.RULE:
                candidate = apply_rule_context(text, candidate, context)
            if candidate is not None:
                findings.append(candidate)
    return tuple(findings)


def _to_finding(
    text: str,
    recognizer: EntityRecognizer,
    result: RecognizerResult,
    source: FindingSource,
) -> Finding | None:
    if result.start < 0 or result.end <= result.start or result.end > len(text):
        return None
    return Finding(
        entity_type=result.entity_type,
        start=result.start,
        end=result.end,
        score=max(0.0, min(1.0, float(result.score))),
        source=source,
        text=text[result.start : result.end],
        detector=recognizer.name,
    )
