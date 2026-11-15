"""Lazy NER adapter using GLiNER as the primary model and a local spaCy fallback."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, cast

from ..languages import DEFAULT_DOCUMENT_LANGUAGE
from ..resources import (
    gliner_model_reference,
    gliner_onnx_file,
    spacy_available,
    spacy_model_reference,
)
from ..workers import detect_performance_cores
from .models import Finding, FindingSource, LanguageSegment
from .spans import trim_findings
from .wordlists import load_business_terms, normalize_term, normalize_terms

DEFAULT_GLINER_MODEL = "urchade/gliner_multi_pii-v1"
DEFAULT_SPACY_MODEL = "de_core_news_lg"
DEFAULT_GLINER_CHUNK_WORDS = 200
DEFAULT_GLINER_CHUNK_OVERLAP_WORDS = 40
DEFAULT_GLINER_CHUNK_CHARS = 1_200
DEFAULT_GLINER_CHUNK_OVERLAP_CHARS = 200
DEFAULT_SPACY_CHUNK_CHARS = 100_000
DEFAULT_SPACY_CHUNK_OVERLAP_CHARS = 1_000
# Words like in the suspicion check: "Full-Stack" remains a single word.
_NER_WORD = re.compile(r"[^\W\d_]+(?:[-’'][^\W\d_]+)*")

DEFAULT_ENTITY_THRESHOLDS: Mapping[str, float] = MappingProxyType(
    {
        "PERSON": 0.35,
        "ORGANIZATION": 0.40,
        "LOCATION": 0.45,
    }
)

DEFAULT_LABEL_MAP: Mapping[str, str] = MappingProxyType(
    {
        "per": "PERSON",
        "person": "PERSON",
        "person name": "PERSON",
        "pers": "PERSON",
        "org": "ORGANIZATION",
        "organisation": "ORGANIZATION",
        "organization": "ORGANIZATION",
        "company": "ORGANIZATION",
        "loc": "LOCATION",
        "location": "LOCATION",
        "place": "LOCATION",
        "gpe": "LOCATION",
    }
)
#: What GLiNER is asked for per section. Deliberately without ``address``: The model
#: knows the label and finds streets, but the labels are in the same prompt and compete.
#: On the second-opinion corpus, 24 out of 480 locations were completely missing with
#: ``address`` (recall 0.94 → 0.89), and persons and companies also dropped. A separate
#: run only for ``address`` doubles the compute time. Addresses are therefore recognized
#: by ``StreetAddressRecognizer``.
DEFAULT_GLINER_LABELS = ("person", "organization", "location")


class NerAdapter(Protocol):
    """Small interface so the pipeline remains testable without a model."""

    def detect(self, text: str) -> Sequence[Finding]:
        """Returns raw NER findings in the original text."""


@dataclass(frozen=True, slots=True)
class NerEngineStatus(Mapping[str, str | bool | None]):
    """Small, suitable for health/diagnosis runtime information."""

    preferred: str
    active: str
    loaded: bool | None
    fallback_reason: str | None = None
    #: The device on which the *active* engine actually computes. A confirmed GPU says
    #: nothing about this: if detection falls back to spaCy, it runs here on the CPU,
    #: while the GPU remains usable.
    device: str = "cpu"

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "preferred": self.preferred,
            "active": self.active,
            "loaded": self.loaded,
            "fallback_reason": self.fallback_reason,
            "device": self.device,
        }

    def __getitem__(self, key: str) -> str | bool | None:
        return self.as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __len__(self) -> int:
        return 5


@dataclass(frozen=True, slots=True)
class EntityThresholds:
    """Confidence thresholds per entity instead of a global threshold."""

    values: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_ENTITY_THRESHOLDS))
    default: float = 0.50

    def __post_init__(self) -> None:
        normalized = {key.strip().upper(): value for key, value in self.values.items()}
        if not 0.0 < self.default <= 1.0:
            raise ValueError("Der Standard-Schwellenwert muss groesser 0 und hoechstens 1 sein")
        if any(not 0.0 < value <= 1.0 for value in normalized.values()):
            raise ValueError("NER-Schwellenwerte muessen groesser 0 und hoechstens 1 sein")
        object.__setattr__(self, "values", MappingProxyType(normalized))

    def for_entity(self, entity_type: str) -> float:
        return self.values.get(entity_type.strip().upper(), self.default)


def filter_ner_findings(
    text: str,
    findings: Iterable[Finding],
    *,
    thresholds: EntityThresholds | None = None,
    business_terms: Iterable[str] | None = None,
    language: str = DEFAULT_DOCUMENT_LANGUAGE,
) -> tuple[Finding, ...]:
    """Trims NER spans and applies thresholds and business stopwords."""

    configured_thresholds = thresholds or EntityThresholds()
    blocked_terms = (
        load_business_terms(language=language)
        if business_terms is None
        else normalize_terms(business_terms)
    )
    accepted: list[Finding] = []
    for finding in trim_findings(text, findings):
        if finding.score < configured_thresholds.for_entity(finding.entity_type):
            continue
        matched_text = finding.text or text[finding.start : finding.end]
        if normalize_term(matched_text) in blocked_terms:
            continue
        # "Software Engineer", "Service Team": every word is a term.
        words = _NER_WORD.findall(normalize_term(matched_text))
        if words and all(word in blocked_terms for word in words):
            continue
        # Names contain no digits (“Technical Management (5 Developers)”).
        if finding.entity_type == "PERSON" and any(c.isdigit() for c in matched_text):
            continue
        accepted.append(finding)
    return tuple(accepted)


class _GLiNERModel(Protocol):
    def predict_entities(
        self,
        text: str,
        labels: Sequence[str],
        *,
        threshold: float,
        flat_ner: bool,
        multi_label: bool,
    ) -> Sequence[Mapping[str, object]]: ...


GLiNERLoader = Callable[[str, str], _GLiNERModel]


class NerModelUnavailableError(RuntimeError):
    """The preferred model is not present locally.

    Explicitly separated from a device error: a missing artifact says nothing about CUDA
    or MPS. Anyone who throws both into the same bucket reports a GPU fallback where
    only a file is missing — and thereby conceals both the working GPU and the actual
    cause (Spec. 4, 5).
    """


def _onnx_session_options() -> Any:
    """ONNX session on the performance cores.

    The ORT default takes all cores, on Apple Silicon also the E-cores, and waits at
    each synchronization for the slowest thread. Measured on an M1 Max: 250 ms with the
    default, 176 ms with 8 threads.
    """

    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = detect_performance_cores()
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return options


def _load_local_gliner(model_name: str, device: str) -> _GLiNERModel:
    """Loads only existing model artifacts; never silently from the network.

    Since, name recognition is available as ONNX (int8) in the harness's model
    folder; it runs using its own runner (``gliner_onnx``) without
    ``gliner``, PyTorch, and transformers, always on the CPU. The sidecar no longer
    loads a PyTorch snapshot; ``device`` remains only for the loader interface.
    """

    del device
    if not Path(model_name).is_dir():
        raise NerModelUnavailableError("Die Namenserkennung ist noch nicht geladen")
    onnx_file = gliner_onnx_file(model_name)
    if onnx_file is None:
        raise NerModelUnavailableError(
            f"Unter '{model_name}' liegt kein ONNX-Modell der Namenserkennung"
        )
    from .gliner_onnx import GlinerOnnxError, GlinerOnnxModel

    try:
        model = GlinerOnnxModel(
            Path(model_name),
            onnx_file=onnx_file,
            session_options=_onnx_session_options(),
        )
    except GlinerOnnxError as error:
        raise NerModelUnavailableError(str(error)) from error
    except Exception as error:
        # onnxruntime reports a missing or broken file as its own exception. Only a
        # genuine device error may change device selection, so the artifact case is
        # clearly named here.
        if _is_missing_artifact(error) or type(error).__module__.startswith("onnxruntime"):
            raise NerModelUnavailableError(
                f"Die Namenserkennung unter '{model_name}' laesst sich nicht laden"
            ) from error
        raise
    return cast("_GLiNERModel", model)


#: Error classes describing a missing local artifact. Comparison runs via names so
#: neither huggingface_hub nor transformers need to be imported for this distinction.
_MISSING_ARTIFACT_ERRORS = frozenset(
    {
        "EntryNotFoundError",
        "LocalEntryNotFoundError",
        "OfflineModeIsEnabled",
        "RepositoryNotFoundError",
        "RevisionNotFoundError",
    }
)


def _is_missing_artifact(error: BaseException) -> bool:
    for candidate in (error, *_causes(error)):
        if type(candidate).__name__ in _MISSING_ARTIFACT_ERRORS:
            return True
        if isinstance(candidate, OSError) and not isinstance(candidate, ConnectionError):
            # ``spacy.load`` and ``from_pretrained`` report a missing directory as
            # OSError; a network error would not be an artifact problem here but a
            # violated offline promise.
            return True
    return False


def _causes(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = {id(error)}
    current = error.__cause__ or error.__context__
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


class GLiNERNerAdapter:
    """Multilingual GLiNER adapter with document-wide chunking.

    GLiNER models have a limited token window. Therefore, long plain text is split at
    word boundaries with overlap before the model sees it. Hits are then mapped back to
    original offsets and identical overlap hits are merged.
    """

    detector_name = "gliner"

    def __init__(
        self,
        *,
        model_name: str | None = None,
        device: str = "cpu",
        loader: GLiNERLoader | None = None,
        labels: Sequence[str] = DEFAULT_GLINER_LABELS,
        label_map: Mapping[str, str] = DEFAULT_LABEL_MAP,
        thresholds: EntityThresholds | None = None,
        max_chunk_words: int = DEFAULT_GLINER_CHUNK_WORDS,
        chunk_overlap_words: int = DEFAULT_GLINER_CHUNK_OVERLAP_WORDS,
        max_chunk_chars: int = DEFAULT_GLINER_CHUNK_CHARS,
        chunk_overlap_chars: int = DEFAULT_GLINER_CHUNK_OVERLAP_CHARS,
    ) -> None:
        if max_chunk_words < 2:
            raise ValueError("max_chunk_words muss mindestens 2 sein")
        if not 0 <= chunk_overlap_words < max_chunk_words:
            raise ValueError("chunk_overlap_words muss zwischen 0 und max_chunk_words liegen")
        if max_chunk_chars < 2:
            raise ValueError("max_chunk_chars muss mindestens 2 sein")
        if not 0 <= chunk_overlap_chars < max_chunk_chars:
            raise ValueError("chunk_overlap_chars muss zwischen 0 und max_chunk_chars liegen")
        self.model_name = model_name or gliner_model_reference()
        self.device = device
        self._loader = loader or _load_local_gliner
        self._labels = tuple(labels)
        self._label_map = {
            normalize_term(source): target.strip().upper() for source, target in label_map.items()
        }
        configured_thresholds = thresholds or EntityThresholds()
        mapped_entities = {
            self._label_map[normalize_term(label)]
            for label in self._labels
            if normalize_term(label) in self._label_map
        }
        if not mapped_entities:
            raise ValueError("Mindestens ein GLiNER-Label braucht ein Entity-Mapping")
        self._mapped_entities = frozenset(mapped_entities)
        self._candidate_threshold = min(
            configured_thresholds.for_entity(entity) for entity in mapped_entities
        )
        self._max_chunk_words = max_chunk_words
        self._chunk_overlap_words = chunk_overlap_words
        self._max_chunk_chars = max_chunk_chars
        self._chunk_overlap_chars = chunk_overlap_chars
        self._model: _GLiNERModel | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def status(self) -> NerEngineStatus:
        return NerEngineStatus(
            preferred=self.detector_name,
            active=self.detector_name,
            loaded=self.loaded,
            device=self.device,
        )

    def _get_model(self) -> _GLiNERModel:
        if self._model is None:
            self._model = self._loader(self.model_name, self.device)
        return self._model

    def detect(self, text: str) -> tuple[Finding, ...]:
        return self._detect(text, candidate_threshold=self._candidate_threshold)

    def detect_with_thresholds(
        self,
        text: str,
        thresholds: EntityThresholds,
    ) -> tuple[Finding, ...]:
        """Requests candidates up to the currently lowest profile threshold."""

        candidate_threshold = min(thresholds.for_entity(entity) for entity in self._mapped_entities)
        return self._detect(text, candidate_threshold=candidate_threshold)

    def _detect(
        self,
        text: str,
        *,
        candidate_threshold: float,
    ) -> tuple[Finding, ...]:
        if not text:
            return ()

        findings_by_span: dict[tuple[str, int, int], Finding] = {}
        model = self._get_model()
        for chunk_start, chunk_end in self._chunks(text):
            chunk = text[chunk_start:chunk_end]
            predictions = model.predict_entities(
                chunk,
                self._labels,
                threshold=candidate_threshold,
                flat_ner=False,
                multi_label=True,
            )
            for prediction in predictions:
                label = self._label_map.get(normalize_term(str(prediction.get("label", ""))))
                if label is None:
                    continue
                local_start = int(prediction["start"])
                local_end = int(prediction["end"])
                if local_start < 0 or local_end <= local_start or local_end > len(chunk):
                    continue
                start = chunk_start + local_start
                end = chunk_start + local_end
                finding = Finding(
                    entity_type=label,
                    start=start,
                    end=end,
                    score=float(prediction["score"]),
                    source=FindingSource.NER,
                    text=text[start:end],
                    detector=self.detector_name,
                )
                key = (finding.entity_type, finding.start, finding.end)
                previous = findings_by_span.get(key)
                if previous is None or finding.score > previous.score:
                    findings_by_span[key] = finding

        return tuple(
            sorted(
                findings_by_span.values(),
                key=lambda finding: (
                    finding.start,
                    finding.end,
                    finding.entity_type,
                    -finding.score,
                ),
            )
        )

    def _chunks(self, text: str) -> tuple[tuple[int, int], ...]:
        words = tuple(re.finditer(r"\S+", text))
        if len(words) <= self._max_chunk_words:
            word_chunks = ((0, len(text)),)
        else:
            chunks: list[tuple[int, int]] = []
            step = self._max_chunk_words - self._chunk_overlap_words
            first_word = 0
            while first_word < len(words):
                after_last_word = min(first_word + self._max_chunk_words, len(words))
                start = 0 if first_word == 0 else words[first_word].start()
                end = (
                    len(text) if after_last_word == len(words) else words[after_last_word - 1].end()
                )
                chunks.append((start, end))
                if after_last_word == len(words):
                    break
                first_word += step
            word_chunks = tuple(chunks)

        bounded_chunks: list[tuple[int, int]] = []
        for word_start, word_end in word_chunks:
            fragment = text[word_start:word_end]
            bounded_chunks.extend(
                (
                    word_start + chunk_start,
                    word_start + chunk_end,
                )
                for chunk_start, chunk_end in _character_chunks(
                    fragment,
                    max_chars=self._max_chunk_chars,
                    overlap_chars=self._chunk_overlap_chars,
                )
            )
        return tuple(bounded_chunks)


class _SpacyEntity(Protocol):
    start_char: int
    end_char: int
    label_: str
    text: str


class _SpacyDocument(Protocol):
    ents: Sequence[_SpacyEntity]


class _SpacyModel(Protocol):
    def __call__(self, text: str) -> _SpacyDocument: ...


SpacyLoader = Callable[[str], _SpacyModel]


def _load_local_spacy(model_name: str) -> _SpacyModel:
    try:
        import spacy
    except ImportError as error:
        raise NerModelUnavailableError(
            "Das optionale Paket 'spacy' ist nicht installiert"
        ) from error
    try:
        return cast(
            "_SpacyModel",
            spacy.load(
                model_name,
                disable=["tagger", "parser", "lemmatizer", "attribute_ruler"],
            ),
        )
    except OSError as error:
        raise NerModelUnavailableError(f"Das lokale spaCy-Modell '{model_name}' fehlt") from error


def _spacy_score(entity: object, default: float) -> float:
    direct_score = getattr(entity, "score", None)
    if direct_score is not None:
        return float(direct_score)
    extensions = getattr(entity, "_", None)
    extension_score = getattr(extensions, "score", None) if extensions is not None else None
    return default if extension_score is None else float(extension_score)


class SpacyGermanNerAdapter:
    """Lazy spaCy fallback with safe long-text chunking.

    German is the base (engine ID ``de_core_news_lg``, as in the profile schema). Since,
    ``detect_segments`` selects the language pack's model per paragraph:
    English paragraphs go to ``en_core_web_sm`` once available. If a language pack is
    missing, the German model takes over – better a weaker model than no detection.
    """

    detector_name = "de_core_news_lg"

    def __init__(
        self,
        *,
        model_name: str | None = None,
        loader: SpacyLoader | None = None,
        language_models: Mapping[str, str] | None = None,
        default_score: float = 0.50,
        label_map: Mapping[str, str] = DEFAULT_LABEL_MAP,
        max_chunk_chars: int = DEFAULT_SPACY_CHUNK_CHARS,
        chunk_overlap_chars: int = DEFAULT_SPACY_CHUNK_OVERLAP_CHARS,
    ) -> None:
        if not 0.0 <= default_score <= 1.0:
            raise ValueError("default_score muss zwischen 0 und 1 liegen")
        if max_chunk_chars < 2:
            raise ValueError("max_chunk_chars muss mindestens 2 sein")
        if not 0 <= chunk_overlap_chars < max_chunk_chars:
            raise ValueError("chunk_overlap_chars muss zwischen 0 und max_chunk_chars liegen")
        self.model_name = model_name or spacy_model_reference()
        self.default_score = default_score
        self._loader = loader or _load_local_spacy
        self._label_map = {
            normalize_term(source): target.strip().upper() for source, target in label_map.items()
        }
        self._max_chunk_chars = max_chunk_chars
        self._chunk_overlap_chars = chunk_overlap_chars
        self._model: _SpacyModel | None = None
        #: Other languages → model reference; ``None`` means: determine from available
        #: language packs on first need.
        self._language_models = dict(language_models) if language_models is not None else None
        self._language_cache: dict[str, _SpacyModel] = {}

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def status(self) -> NerEngineStatus:
        return NerEngineStatus(
            preferred=self.detector_name,
            active=self.detector_name,
            loaded=self.loaded,
            # The German spaCy model runs in this application without GPU connection.
            # Marking it as a GPU path would be the wrong message that Spec 4 aims to
            # prevent.
            device="cpu",
        )

    def _get_model(self) -> _SpacyModel:
        if self._model is None:
            self._model = self._loader(self.model_name)
        return self._model

    def language_models(self) -> Mapping[str, str]:
        """Languages other than German for which a dedicated model is available."""

        if self._language_models is None:
            self._language_models = {
                language: spacy_model_reference(language)
                for language in ("en",)
                if spacy_available(language)
            }
        return self._language_models

    def _model_for(self, language: str) -> tuple[_SpacyModel, str | None]:
        """Model and detector ID for a language (German as fallback)."""

        reference = self.language_models().get(language) if language != "de" else None
        if reference is None:
            return self._get_model(), None
        model = self._language_cache.get(language)
        if model is None:
            try:
                model = self._loader(reference)
            except NerModelUnavailableError:
                # Package removed during the session: German takes over.
                self._language_models = {
                    key: value for key, value in self.language_models().items() if key != language
                }
                return self._get_model(), None
            self._language_cache[language] = model
        return model, f"spacy_{language}"

    def detect(self, text: str) -> tuple[Finding, ...]:
        return self._detect_with(self._get_model(), text)

    def detect_segments(
        self,
        text: str,
        segments: Sequence[LanguageSegment],
    ) -> tuple[Finding, ...]:
        """Detects each language segment with its language's model."""

        if not segments:
            return self.detect(text)
        findings: list[Finding] = []
        for segment in segments:
            if segment.end <= segment.start:
                continue
            model, detector = self._model_for(segment.language)
            local = self._detect_with(model, text[segment.start : segment.end], detector)
            findings.extend(_shifted(finding, segment.start, text) for finding in local)
        return tuple(sorted(findings, key=lambda item: (item.start, item.end, item.entity_type)))

    def _detect_with(
        self, model: _SpacyModel, text: str, detector: str | None = None
    ) -> tuple[Finding, ...]:
        if not text:
            return ()

        findings_by_span: dict[tuple[str, int, int], Finding] = {}
        for chunk_start, chunk_end in _character_chunks(
            text,
            max_chars=self._max_chunk_chars,
            overlap_chars=self._chunk_overlap_chars,
        ):
            chunk = text[chunk_start:chunk_end]
            document = model(chunk)
            for entity in document.ents:
                entity_type = self._label_map.get(normalize_term(entity.label_))
                if entity_type is None:
                    continue
                if (
                    entity.start_char < 0
                    or entity.end_char <= entity.start_char
                    or entity.end_char > len(chunk)
                ):
                    continue
                start = chunk_start + entity.start_char
                end = chunk_start + entity.end_char
                finding = Finding(
                    entity_type=entity_type,
                    start=start,
                    end=end,
                    score=_spacy_score(entity, self.default_score),
                    source=FindingSource.NER,
                    text=text[start:end],
                    detector=detector or self.detector_name,
                )
                key = (finding.entity_type, finding.start, finding.end)
                previous = findings_by_span.get(key)
                if previous is None or finding.score > previous.score:
                    findings_by_span[key] = finding

        return tuple(
            sorted(
                findings_by_span.values(),
                key=lambda finding: (
                    finding.start,
                    finding.end,
                    finding.entity_type,
                    -finding.score,
                ),
            )
        )


def _shifted(finding: Finding, offset: int, text: str) -> Finding:
    start = finding.start + offset
    end = finding.end + offset
    return Finding(
        entity_type=finding.entity_type,
        start=start,
        end=end,
        score=finding.score,
        source=finding.source,
        text=text[start:end],
        detector=finding.detector,
    )


def detect_with_segments(
    adapter: NerAdapter,
    text: str,
    *,
    segments: Sequence[LanguageSegment],
    thresholds: EntityThresholds | None,
) -> Sequence[Finding]:
    """Uses language segments if the adapter can do something with them.

    GLiNER is multilingual and sees the whole document; the spaCy fallback takes the
    model for each segment.
    """

    by_segments = getattr(adapter, "detect_segments", None)
    if segments and callable(by_segments):
        return by_segments(text, segments)
    return _detect_with_optional_thresholds(adapter, text, thresholds=thresholds)


def _character_chunks(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int,
) -> tuple[tuple[int, int], ...]:
    """Splits long text near whitespace boundaries without infinite loops."""

    if len(text) <= max_chars:
        return ((0, len(text)),)

    chunks: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        raw_end = min(start + max_chars, len(text))
        end = raw_end
        if raw_end < len(text):
            # Do not cut off too early just to find any whitespace. With a single giant
            # token, the hard limit remains.
            search_start = start + max_chars // 2
            whitespace = tuple(re.finditer(r"\s+", text[search_start:raw_end]))
            if whitespace:
                end = search_start + whitespace[-1].end()
        chunks.append((start, end))
        if end == len(text):
            break

        desired_start = max(start + 1, end - overlap_chars)
        prefix_start = max(start + 1, desired_start - overlap_chars)
        whitespace = tuple(re.finditer(r"\s+", text[prefix_start:desired_start]))
        next_start = prefix_start + whitespace[-1].end() if whitespace else desired_start
        if next_start <= start:
            next_start = end
        start = next_start
    return tuple(chunks)


class NerUnavailableError(RuntimeError):
    """Neither standard nor fallback engine could operate safely."""


class FallbackNerAdapter:
    """Uses GLiNER and switches permanently to spaCy on load/inference errors."""

    detector_name = "fallback"

    def __init__(
        self,
        primary: NerAdapter,
        fallback: NerAdapter,
        *,
        on_fallback: Callable[[Exception], None] | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self._active: NerAdapter = primary
        self.primary_error: Exception | None = None
        self._on_fallback = on_fallback

    @property
    def active_adapter(self) -> NerAdapter:
        return self._active

    def status(self) -> NerEngineStatus:
        return NerEngineStatus(
            preferred=_adapter_name(self.primary),
            active=_adapter_name(self._active),
            loaded=_adapter_loaded(self._active),
            fallback_reason=(
                None if self.primary_error is None else type(self.primary_error).__name__
            ),
            device=_adapter_device(self._active),
        )

    def detect(self, text: str) -> Sequence[Finding]:
        return self._detect(text, thresholds=None)

    def detect_with_thresholds(
        self,
        text: str,
        thresholds: EntityThresholds,
    ) -> Sequence[Finding]:
        return self._detect(text, thresholds=thresholds)

    def detect_segments(
        self,
        text: str,
        segments: Sequence[LanguageSegment],
        thresholds: EntityThresholds | None = None,
    ) -> Sequence[Finding]:
        return self._detect(text, thresholds=thresholds, segments=segments)

    def _detect(
        self,
        text: str,
        *,
        thresholds: EntityThresholds | None,
        segments: Sequence[LanguageSegment] = (),
    ) -> Sequence[Finding]:
        try:
            return detect_with_segments(
                self._active,
                text,
                segments=segments,
                thresholds=thresholds,
            )
        except Exception as error:
            if self._active is self.fallback:
                raise NerUnavailableError(
                    "Die lokale NER-Fallback-Engine ist ausgefallen"
                ) from error
            self.primary_error = error
            self._active = self.fallback
            if self._on_fallback is not None:
                self._on_fallback(error)
            try:
                return detect_with_segments(
                    self._active,
                    text,
                    segments=segments,
                    thresholds=thresholds,
                )
            except Exception as fallback_error:
                raise NerUnavailableError(
                    "Weder GLiNER noch die lokale NER-Fallback-Engine ist verfuegbar"
                ) from fallback_error


def _detect_with_optional_thresholds(
    adapter: NerAdapter,
    text: str,
    *,
    thresholds: EntityThresholds | None,
) -> Sequence[Finding]:
    detect_for_thresholds = getattr(adapter, "detect_with_thresholds", None)
    if thresholds is not None and callable(detect_for_thresholds):
        return detect_for_thresholds(text, thresholds)
    return adapter.detect(text)


#: From here, prefer GLiNER detection (ONNX, 0.58 GB). A device claiming "8
#: GB" often reports less, hence 7 GiB.
GLINER_ONNX_MIN_MEMORY_BYTES = 7 * 1024**3


@dataclass(frozen=True, slots=True)
class NerHardwareSelection:
    """Result of the preceding hardware selection for NER generation."""

    #: Always ``cpu``: Name detection is ONNX.
    active_device: str = "cpu"
    prefer_gliner: bool = True
    memory_bytes: int | None = None


def select_ner_hardware(
    memory_bytes: int | None = None,
    *,
    active_device: str = "cpu",
) -> NerHardwareSelection:
    """Binds the once-verified hardware choice to NER generation.

    Since E-Y1 there is no PyTorch path: a confirmed GPU (``active_device``) changes
    nothing; GLiNER computes as ONNX on the CPU.
    """

    del active_device
    if memory_bytes is None:
        from ..workers import detect_physical_memory

        memory_bytes = detect_physical_memory()
    if memory_bytes < 0:
        raise ValueError("memory_bytes darf nicht negativ sein")
    return NerHardwareSelection(
        active_device="cpu",
        prefer_gliner=memory_bytes >= GLINER_ONNX_MIN_MEMORY_BYTES,
        memory_bytes=memory_bytes,
    )


def _adapter_name(adapter: NerAdapter) -> str:
    return str(getattr(adapter, "detector_name", type(adapter).__name__))


def _adapter_loaded(adapter: NerAdapter) -> bool | None:
    loaded = getattr(adapter, "loaded", None)
    return loaded if isinstance(loaded, bool) else None


def _adapter_device(adapter: NerAdapter) -> str:
    device = getattr(adapter, "device", None)
    return device if isinstance(device, str) and device else "cpu"


def build_ner_adapter(
    hardware: NerHardwareSelection | None = None,
    *,
    thresholds: EntityThresholds | None = None,
    gliner_loader: GLiNERLoader | None = None,
    spacy_loader: SpacyLoader | None = None,
    on_fallback: Callable[[Exception], None] | None = None,
) -> NerAdapter:
    """Builds the engine after hardware selection, without loading a model yet."""

    selection = hardware or select_ner_hardware()
    fallback = SpacyGermanNerAdapter(loader=spacy_loader)
    if not selection.prefer_gliner:
        return fallback
    primary = GLiNERNerAdapter(
        device=selection.active_device,
        loader=gliner_loader,
        thresholds=thresholds,
    )
    return FallbackNerAdapter(primary, fallback, on_fallback=on_fallback)
