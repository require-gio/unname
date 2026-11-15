"""Public components of the local detection pipeline."""

from .context import apply_rule_context
from .models import DetectionResult, Finding, FindingSource, LanguageSegment
from .ner import (
    DEFAULT_ENTITY_THRESHOLDS,
    DEFAULT_GLINER_MODEL,
    DEFAULT_SPACY_MODEL,
    EntityThresholds,
    FallbackNerAdapter,
    GLiNERNerAdapter,
    NerAdapter,
    NerEngineStatus,
    NerHardwareSelection,
    NerModelUnavailableError,
    NerUnavailableError,
    SpacyGermanNerAdapter,
    build_ner_adapter,
    filter_ner_findings,
    select_ner_hardware,
)
from .pipeline import (
    EXACT_DUPLICATE_DETECTOR,
    DetectionPipeline,
    DuplicateReviewCapacityError,
)
from .spans import (
    SpanReplacement,
    replace_back_to_front,
    resolve_overlaps,
    spans_overlap,
    trim_finding,
    trim_findings,
)
from .suspicion import find_name_suspicions
from .wordlists import (
    LanguageWordlists,
    load_business_terms,
    load_language_wordlists,
    load_sentence_starters,
    load_wordlist,
    normalize_term,
    normalize_terms,
)

__all__ = [
    "DEFAULT_ENTITY_THRESHOLDS",
    "DEFAULT_GLINER_MODEL",
    "DEFAULT_SPACY_MODEL",
    "DetectionPipeline",
    "DetectionResult",
    "DuplicateReviewCapacityError",
    "EntityThresholds",
    "EXACT_DUPLICATE_DETECTOR",
    "FallbackNerAdapter",
    "Finding",
    "FindingSource",
    "GLiNERNerAdapter",
    "LanguageSegment",
    "LanguageWordlists",
    "NerAdapter",
    "NerEngineStatus",
    "NerHardwareSelection",
    "NerModelUnavailableError",
    "NerUnavailableError",
    "SpacyGermanNerAdapter",
    "SpanReplacement",
    "apply_rule_context",
    "build_ner_adapter",
    "filter_ner_findings",
    "find_name_suspicions",
    "load_business_terms",
    "load_language_wordlists",
    "load_sentence_starters",
    "load_wordlist",
    "normalize_term",
    "normalize_terms",
    "replace_back_to_front",
    "resolve_overlaps",
    "select_ner_hardware",
    "spans_overlap",
    "trim_finding",
    "trim_findings",
]
