"""Name detection with GLiNER as ONNX – without ``gliner``, PyTorch and transformers.

Since Phase I, name detection has been available only as ONNX (int8) in the model
folder. The ``gliner`` package still required PyTorch and transformers for this – solely
for tensors, DataLoader and the tokenizer. This runner computes the same using
``tokenizers``, ``onnxruntime`` and ``numpy``, just like the second opinion
(``second_opinion.py``).

Pre- and post-processing are taken from ``gliner`` 0.2.28 (``UniEncoderSpanProcessor``,
``prepare_word_mask``, ``prepare_span_idx``, ``SpanDecoder``) and tested against their
outputs in ``tests/test_gliner_onnx.py``. Deviations from these are errors, not
improvements:

* Words like ``WhitespaceTokenSplitter``: ``\\w+(?:[-_]\\w+)*|\\S``.
* Input ``[CLS] <<ENT>> label … <<SEP>> wort … [SEP]``, each word tokenized individually
  (``is_split_into_words``).
* Word mask: only the first subword of a word counts; prompt words do not.
* Spans: each word as start, width 0 to ``max_width - 1``; spans extending beyond the
  text end are masked.
* Decoding: Sigmoid, threshold ``>``, then greedy selection by score (``flat_ner``
  forbids any overlap, otherwise only crossings; ``multi_label`` allows multiple labels
  on the same span).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_FILE = "gliner_config.json"
TOKENIZER_FILE = "tokenizer.json"
#: Like ``WhitespaceTokenSplitter`` in ``gliner`` (``words_splitter_type``
#: ``whitespace``, the only one this model knows).
_WORD = re.compile(r"\w+(?:[-_]\w+)*|\S")


class GlinerOnnxError(RuntimeError):
    """Model folder incomplete or not of this model type."""


@dataclass(frozen=True, slots=True)
class GlinerConfig:
    """What the preprocessing from ``gliner_config.json`` needs."""

    max_width: int
    max_len: int
    ent_token: str
    sep_token: str

    @classmethod
    def from_file(cls, path: Path) -> GlinerConfig:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise GlinerOnnxError(f"{path.name} nicht lesbar: {error}") from error
        # Only the Uni-Encoder Span model (``span_mode`` markerV0, without label
        # encoder) is reproduced here. Another model would return false positives
        # silently – so better not to load it.
        if raw.get("labels_encoder") or raw.get("span_mode", "markerV0") != "markerV0":
            raise GlinerOnnxError("Nur GLiNER-Spannenmodelle mit einem Encoder werden unterstuetzt")
        if raw.get("words_splitter_type", "whitespace") != "whitespace":
            raise GlinerOnnxError("Nur der Wort-Splitter 'whitespace' wird unterstuetzt")
        if raw.get("subtoken_pooling", "first") != "first":
            raise GlinerOnnxError("Nur subtoken_pooling 'first' wird unterstuetzt")
        return cls(
            max_width=int(raw.get("max_width", 12)),
            max_len=int(raw.get("max_len", 384)),
            ent_token=str(raw.get("ent_token", "[ENT]")),
            sep_token=str(raw.get("sep_token", "[SEP]")),
        )


@dataclass(frozen=True, slots=True)
class _Span:
    start: int
    #: Last word of the span, inclusive.
    end: int
    label: str
    score: float


@dataclass(frozen=True, slots=True)
class PreparedInput:
    """Model inputs for a text (batch of size 1)."""

    feed: dict[str, Any]
    #: Character range per word in the text.
    word_offsets: tuple[tuple[int, int], ...]
    labels: tuple[str, ...]


def split_words(text: str) -> list[tuple[str, int, int]]:
    return [(match.group(), match.start(), match.end()) for match in _WORD.finditer(text)]


def word_mask(word_ids: Sequence[int | None], prompt_words: int) -> list[int]:
    """1-based word number at the first subword of each text word, otherwise 0."""

    mask: list[int] = []
    previous: int | None = None
    seen = 0
    for word_id in word_ids:
        if word_id is None:
            mask.append(0)
        else:
            if word_id != previous:
                seen += 1
                mask.append(0 if seen <= prompt_words else seen - prompt_words)
            else:
                mask.append(0)
        previous = word_id
    return mask


def greedy_search(spans: Sequence[_Span], *, flat_ner: bool, multi_label: bool) -> list[_Span]:
    """Highest score first; keep what does not collide with anything already selected."""

    selected: list[_Span] = []
    for span in sorted(spans, key=lambda item: -item.score):
        if not any(
            _collides(span, other, flat_ner=flat_ner, multi_label=multi_label) for other in selected
        ):
            selected.append(span)
    selected.sort(key=lambda item: item.start)
    return selected


def _collides(first: _Span, second: _Span, *, flat_ner: bool, multi_label: bool) -> bool:
    if (first.start, first.end) == (second.start, second.end):
        return not multi_label
    disjoint = first.start > second.end or second.start > first.end
    if flat_ner:
        return not disjoint
    nested = (first.start <= second.start and first.end >= second.end) or (
        second.start <= first.start and second.end >= first.end
    )
    return not (disjoint or nested)


class GlinerOnnxModel:
    """GLiNER span model as ONNX; same interface as ``GLiNER``.

    ``predict_entities`` returns dictionaries with ``start``, ``end`` (characters,
    half-open), ``text``, ``label``, and ``score``, just like ``gliner``.
    """

    def __init__(
        self,
        folder: Path,
        *,
        onnx_file: str,
        session_options: Any = None,
        providers: Sequence[str] = ("CPUExecutionProvider",),
    ) -> None:
        import onnxruntime
        from tokenizers import Tokenizer

        folder = Path(folder)
        self.config = GlinerConfig.from_file(folder / CONFIG_FILE)
        try:
            self._tokenizer = Tokenizer.from_file(str(folder / TOKENIZER_FILE))
        except Exception as error:  # noqa: BLE001 – tokenizers wirft eigene Ausnahmen
            raise GlinerOnnxError(f"{TOKENIZER_FILE} nicht lesbar: {error}") from error
        # No truncation and no padding: A text is a batch, and the length is limited by
        # the adapter's chunks (``max_len`` words).
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()
        for token in (self.config.ent_token, self.config.sep_token):
            if self._tokenizer.token_to_id(token) is None:
                raise GlinerOnnxError(f"Der Tokenizer kennt {token} nicht")
        self._session = onnxruntime.InferenceSession(
            str(folder / onnx_file),
            sess_options=session_options,
            providers=list(providers),
        )
        self._input_names = {item.name for item in self._session.get_inputs()}

    def prepare(self, text: str, labels: Sequence[str]) -> PreparedInput | None:
        import numpy

        words = split_words(text)[: self.config.max_len]
        if not words or not text.strip():
            return None
        entity_types = tuple(dict.fromkeys(labels))
        prompt: list[str] = []
        for label in entity_types:
            prompt += [self.config.ent_token, label]
        prompt.append(self.config.sep_token)

        encoding = self._tokenizer.encode(
            prompt + [word for word, _, _ in words], is_pretokenized=True
        )
        count = len(words)
        width = self.config.max_width
        span_idx = [(start, start + offset) for start in range(count) for offset in range(width)]
        feed = {
            "input_ids": numpy.array([encoding.ids], dtype=numpy.int64),
            "attention_mask": numpy.array([encoding.attention_mask], dtype=numpy.int64),
            "words_mask": numpy.array(
                [word_mask(encoding.word_ids, len(prompt))], dtype=numpy.int64
            ),
            "text_lengths": numpy.array([[count]], dtype=numpy.int64),
            "span_idx": numpy.array([span_idx], dtype=numpy.int64),
            "span_mask": numpy.array([[end < count for _, end in span_idx]], dtype=numpy.bool_),
        }
        return PreparedInput(
            feed=feed,
            word_offsets=tuple((start, end) for _, start, end in words),
            labels=entity_types,
        )

    def predict_entities(
        self,
        text: str,
        labels: Sequence[str],
        *,
        threshold: float = 0.5,
        flat_ner: bool = True,
        multi_label: bool = False,
    ) -> list[dict[str, Any]]:
        import numpy

        prepared = self.prepare(text, labels)
        if prepared is None:
            return []
        feed = {name: value for name, value in prepared.feed.items() if name in self._input_names}
        logits = self._session.run(["logits"], feed)[0][0]
        probabilities = 1.0 / (1.0 + numpy.exp(-logits.astype(numpy.float32)))
        # numpy 2 computes the threshold like torch in float32.
        count = len(prepared.word_offsets)
        candidates: list[_Span] = []
        for start, width, label in zip(*numpy.nonzero(probabilities > threshold), strict=True):
            end = int(start) + int(width)
            if end + 1 > count or int(label) >= len(prepared.labels):
                continue
            candidates.append(
                _Span(
                    start=int(start),
                    end=end,
                    label=prepared.labels[int(label)],
                    score=float(probabilities[start, width, label]),
                )
            )
        result: list[dict[str, Any]] = []
        for span in greedy_search(candidates, flat_ner=flat_ner, multi_label=multi_label):
            start = prepared.word_offsets[span.start][0]
            end = prepared.word_offsets[span.end][1]
            result.append(
                {
                    "start": start,
                    "end": end,
                    "text": text[start:end],
                    "label": span.label,
                    "score": span.score,
                }
            )
        return result
