"""GLiNER as ONNX without ``gliner``, PyTorch and transformers.

The unit tests run everywhere. With the model under
``sidecar/models/gliner-onnx`` (``scripts/models/gliner-onnx.py``) a test with real
detection is added; if the ``modellbau`` group is installed
(``uv sync --project sidecar --group modellbau``), ``@pytest.mark.gliner`` compares
inputs and hits with ``gliner`` 0.2.28.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from presidio_sidecar import resources
from presidio_sidecar.detection import ner
from presidio_sidecar.detection.gliner_onnx import (
    GlinerConfig,
    GlinerOnnxError,
    _Span,
    greedy_search,
    split_words,
    word_mask,
)

MODEL = resources.repository_root() / "sidecar" / "models" / "gliner-onnx"
LABELS = ["person", "organization", "location"]
needs_model = pytest.mark.skipif(
    not (MODEL / resources.GLINER_ONNX_FILE).is_file(),
    reason="ONNX-Modell fehlt; `scripts/models/gliner-onnx.py`",
)


def test_words_like_gliner_whitespace_splitter() -> None:
    assert split_words("Dr. Albrecht-Sommer, 12.03.") == [
        ("Dr", 0, 2),
        (".", 2, 3),
        ("Albrecht-Sommer", 4, 19),
        (",", 19, 20),
        ("12", 21, 23),
        (".", 23, 24),
        ("03", 24, 26),
        (".", 26, 27),
    ]


def test_word_mask_counts_only_first_subword_after_prompt() -> None:
    # [CLS] <<ENT>> person <<SEP>> Anne gret Wolk enstein [SEP]
    word_ids = [None, 0, 1, 2, 3, 3, 4, 4, None]
    assert word_mask(word_ids, 3) == [0, 0, 0, 0, 1, 0, 2, 0, 0]


def test_greedy_selection_like_gliner() -> None:
    lang = _Span(0, 2, "person", 0.9)
    inner = _Span(1, 1, "person", 0.8)
    crossing = _Span(2, 3, "person", 0.7)
    same = _Span(0, 2, "organization", 0.6)
    candidates = [crossing, inner, same, lang]

    # Nested spans are allowed; overlapping spans are not. multi_label keeps the
    # second label on the same span.
    assert greedy_search(candidates, flat_ner=False, multi_label=True) == [lang, same, inner]
    assert greedy_search(candidates, flat_ner=False, multi_label=False) == [lang, inner]
    assert greedy_search(candidates, flat_ner=True, multi_label=False) == [lang]


def test_only_known_span_model_is_loaded(tmp_path: Path) -> None:
    config = tmp_path / "gliner_config.json"
    config.write_text(json.dumps({"max_width": 12, "ent_token": "<<ENT>>"}), encoding="utf-8")
    assert GlinerConfig.from_file(config).max_width == 12
    for foreign in (
        {"labels_encoder": "bge"},
        {"span_mode": "token_level"},
        {"subtoken_pooling": "mean"},
    ):
        config.write_text(json.dumps(foreign), encoding="utf-8")
        with pytest.raises(GlinerOnnxError):
            GlinerConfig.from_file(config)


@needs_model
def test_real_detection_without_pytorch(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    for package in ("gliner", "torch", "transformers"):
        monkeypatch.setitem(sys.modules, package, None)
    adapter = ner.GLiNERNerAdapter(model_name=str(MODEL))
    text = "Die Muster Logistik GmbH aus Bielefeld liefert die Ware an Herrn Jaromir Feldkamp."
    hits = {(f.entity_type, text[f.start : f.end]) for f in adapter.detect(text)}

    assert ("PERSON", "Jaromir Feldkamp") in hits
    assert ("ORGANIZATION", "Muster Logistik GmbH") in hits
    assert ("LOCATION", "Bielefeld") in hits
    assert adapter.status().as_dict()["device"] == "cpu"


def _examples() -> tuple[str, ...]:
    path = resources.repository_root() / "scripts" / "models" / "gliner-onnx.py"
    spec = importlib.util.spec_from_file_location("gliner_onnx_skript", path)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.EXAMPLES)


def _corpus(per_profile: int) -> list[str]:
    catalog = resources.repository_root() / "testdata" / "quality" / "synthetic-v1"
    texts: list[str] = []
    for profile in sorted(p for p in catalog.iterdir() if p.is_dir()):
        lines = (profile / "documents.jsonl").read_text(encoding="utf-8").splitlines()
        texts += [json.loads(line)["text"] for line in lines[:per_profile]]
    return texts


@pytest.mark.gliner
@needs_model
def test_same_inputs_and_findings_as_gliner() -> None:
    numpy = pytest.importorskip("numpy")
    gliner = pytest.importorskip("gliner", reason="Gruppe modellbau fehlt")
    from presidio_sidecar.detection.gliner_onnx import GlinerOnnxModel

    options = ner._onnx_session_options()
    reference = gliner.GLiNER.from_pretrained(
        str(MODEL),
        local_files_only=True,
        load_onnx_model=True,
        onnx_model_file=resources.GLINER_ONNX_FILE,
        session_options=options,
    )
    own = GlinerOnnxModel(MODEL, onnx_file=resources.GLINER_ONNX_FILE, session_options=options)
    texts = [*_examples(), *_corpus(10)]

    for text in texts[:40]:
        chunk = text[: ner.DEFAULT_GLINER_CHUNK_CHARS]
        prepared_batch = reference.prepare_batch([chunk], LABELS)
        batch = reference.collate_batch(prepared_batch["input_x"], prepared_batch["entity_types"])
        prepared = own.prepare(chunk, LABELS)
        assert prepared is not None
        for name, value in prepared.feed.items():
            assert numpy.array_equal(batch[name].numpy().astype(value.dtype), value), name

    def findings_of(model: object) -> dict[tuple[int, str, int, int], float]:
        adapter = ner.GLiNERNerAdapter(model_name=str(MODEL), loader=lambda *_: model)
        return {
            (index, f.entity_type, f.start, f.end): f.score
            for index, text in enumerate(texts)
            for f in adapter.detect(text)
        }

    expected, received = findings_of(reference), findings_of(own)
    assert expected.keys() == received.keys()
    assert max(abs(expected[k] - received[k]) for k in expected) <= 0.01
