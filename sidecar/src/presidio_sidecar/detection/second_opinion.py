"""Second opinion: a small local classifier for open questions.

After the suspicion review, positions with uncertain detection – a capitalized word pair,
a low-score hit – remain. The classifier reads each of these positions with some context
and chooses one of four fixed labels: ``person``, ``organization_or_product``,
``location`` or ``term``. It does not write text; it only outputs probabilities.

It is trained once by distillation (``scripts/second_opinion``): a large local language
model labels questions in a synthetic corpus, a multilingual encoder with a classification
head learns from that. At runtime only ONNX int8 runs on the CPU – no PyTorch.

``classifier_input`` is the only place that defines what the model sees. Training and
runtime use the same function.
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

LABELS = ("person", "organization_or_product", "location", "term")
#: The same labels as the published v1 model package names them in its
#: ``zweitmeinung.json``. That file is checksummed, so it keeps these names;
#: only the order matters, because the model outputs label indices.
V1_MODEL_LABELS = ("person", "firma_oder_produkt", "ort", "begriff")
#: Labels that indicate the position belongs to masking.
PROTECT_LABELS = frozenset(LABELS[:3])
#: Types the classifier judges: names, companies, locations – and any
#: suspicion that appears as ``PERSON`` or as a repeat type. An uncertain date
#: or number is not covered by these four labels.
ASSESSED_TYPES = frozenset({"PERSON", "ORGANIZATION", "LOCATION"})
CONFIG_FILE = "zweitmeinung.json"
MODEL_FILE = "model.int8.onnx"
TOKENIZER_FILE = "tokenizer.json"
#: Character context per page. Enough for “Sehr geehrter Herr …” and the
#: preceding line, short enough for 128 tokens.
CONTEXT_CHARACTERS = 120
#: From this score upward a suggestion is confident enough to highlight;
#: below it the question card shows both buttons equally.
SUGGEST_MINIMUM = 0.65
#: From this score upward the KI decides to protect according to policy ``protect``.
#: Masking already happens when saving with an open question; the threshold only
#: controls how many questions still need a manual glance.
PROTECT_MINIMUM = 0.80
#: At most this many questions per file; the rest remain without assessment.
MAX_QUESTIONS = 2_000

Policy = Literal["protect", "protect_and_keep", "suggest"]
Label = Literal["person", "organization_or_product", "location", "term"]


class SecondOpinionUnavailableError(RuntimeError):
    """The model is missing or cannot be loaded."""


def classifier_input(
    text: str,
    start: int,
    end: int,
    *,
    entity_type: str,
    suspicion: bool,
) -> str:
    """What the model sees for a position: type, value and highlighted context.

    Line breaks are kept as `` / `` – they often indicate whether a position is a
    heading or a field label.
    """

    def flatten(piece: str) -> str:
        return " ".join(piece.replace("\r", "\n").replace("\n", " / ").split())

    before = text[max(0, start - CONTEXT_CHARACTERS) : start]
    after = text[end : end + CONTEXT_CHARACTERS]
    # Trim to word boundaries so a partial word doesn't look like a name.
    if start - CONTEXT_CHARACTERS > 0 and " " in before:
        before = before[before.index(" ") + 1 :]
    if end + CONTEXT_CHARACTERS < len(text) and " " in after:
        after = after[: after.rindex(" ")]
    value = flatten(text[start:end])
    kind = "verdacht" if suspicion else entity_type.lower()
    return f"{kind}: {value} | {flatten(before)} « {value} » {flatten(after)}".strip()


@dataclass(frozen=True, slots=True)
class Assessment:
    """Local KI assessment for a question."""

    label: Label
    #: Probability of the label.
    confidence: float
    #: Probability that the position belongs to masking (person, company or location).
    protect: float
    #: ``accept`` (mask), ``reject`` (keep) or ``None`` (uncertain).
    suggestion: Literal["accept", "reject"] | None
    #: Did the KI decide according to the policy?
    decided: bool
    model: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "protect": round(self.protect, 4),
            "suggestion": self.suggestion,
            "decided": self.decided,
            "model": self.model,
        }


def assess(
    probabilities: Sequence[float],
    *,
    policy: Policy,
    keep_threshold: float,
    model: str,
    heuristic: bool = False,
) -> Assessment:
    """Derive suggestion and decision from probabilities according to the policy.

    * ``protect``: the KI decides to protect (≥ 80 %) – and for a pure suspicion
      flagged by the capital-letter heuristic (``heuristic``) it also decides
      to keep. No model, rule or name list matched, only capitalization.
    * ``protect_and_keep``: additionally ``keep`` for any question that is
      confidently a term (≥ ``keep_threshold``).
    * ``suggest``: only suggests; final decision is manual.
    """

    if len(probabilities) != len(LABELS):
        raise ValueError("Eine Wahrscheinlichkeit je Label erwartet")
    best = max(range(len(LABELS)), key=lambda index: probabilities[index])
    protect = sum(probabilities[: len(PROTECT_LABELS)])
    keep = 1.0 - protect
    suggestion: Literal["accept", "reject"] | None
    if protect >= SUGGEST_MINIMUM:
        suggestion = "accept"
    elif keep >= SUGGEST_MINIMUM:
        suggestion = "reject"
    else:
        suggestion = None
    decided = policy != "suggest" and (
        protect >= PROTECT_MINIMUM
        or (heuristic and keep >= PROTECT_MINIMUM)
        or (policy == "protect_and_keep" and keep >= keep_threshold)
    )
    return Assessment(
        label=LABELS[best],  # type: ignore[arg-type]
        confidence=float(probabilities[best]),
        protect=float(protect),
        suggestion=suggestion,
        decided=decided,
        model=model,
    )


def softmax(logits: Sequence[float], temperature: float = 1.0) -> list[float]:
    scaled = [value / temperature for value in logits]
    peak = max(scaled)
    exponents = [math.exp(value - peak) for value in scaled]
    total = sum(exponents)
    return [value / total for value in exponents]


class SecondOpinionModel:
    """ONNX classifier with tokenizer, lazily loaded and thread-safe."""

    def __init__(self, folder: Path, *, threads: int = 1) -> None:
        self.folder = Path(folder)
        self.threads = threads
        self._lock = threading.Lock()
        self._session: Any = None
        self._tokenizer: Any = None
        self._config: dict[str, Any] | None = None

    @property
    def config(self) -> dict[str, Any]:
        if self._config is None:
            try:
                raw = json.loads((self.folder / CONFIG_FILE).read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise SecondOpinionUnavailableError(str(error)) from error
            if tuple(raw.get("labels", ())) not in (LABELS, V1_MODEL_LABELS):
                raise SecondOpinionUnavailableError("Unbekannte Labels im Modell")
            self._config = raw
        return self._config

    @property
    def version(self) -> str:
        return str(self.config.get("version", "unbekannt"))

    def _load(self) -> None:
        if self._session is not None:
            return
        try:
            import onnxruntime
            from tokenizers import Tokenizer
        except ImportError as error:  # pragma: no cover – part of the dependencies
            raise SecondOpinionUnavailableError(str(error)) from error
        config = self.config
        try:
            tokenizer = Tokenizer.from_file(str(self.folder / TOKENIZER_FILE))
            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = self.threads
            options.inter_op_num_threads = 1
            session = onnxruntime.InferenceSession(
                str(self.folder / MODEL_FILE),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as error:  # noqa: BLE001 – jede Ladeursache gleich behandeln
            raise SecondOpinionUnavailableError(str(error)) from error
        tokenizer.enable_truncation(int(config.get("max_length", 128)))
        tokenizer.enable_padding(pad_id=int(config.get("pad_id", 1)), pad_token="<pad>")
        self._tokenizer = tokenizer
        self._session = session

    def probabilities(self, inputs: Sequence[str], *, batch_size: int = 32) -> list[list[float]]:
        if not inputs:
            return []
        import numpy

        with self._lock:
            self._load()
            temperature = float(self.config.get("temperature", 1.0))
            names = {item.name for item in self._session.get_inputs()}
            result: list[list[float]] = []
            for offset in range(0, len(inputs), batch_size):
                encoded = self._tokenizer.encode_batch(list(inputs[offset : offset + batch_size]))
                feed = {
                    "input_ids": numpy.array([item.ids for item in encoded], dtype=numpy.int64),
                    "attention_mask": numpy.array(
                        [item.attention_mask for item in encoded], dtype=numpy.int64
                    ),
                }
                logits = self._session.run(None, {k: v for k, v in feed.items() if k in names})[0]
                result.extend(softmax(row.tolist(), temperature) for row in logits)
            return result
