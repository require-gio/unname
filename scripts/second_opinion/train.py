"""Step 4: the student learns the teacher's labels; export to ONNX int8.

* **Split per document** (fixed by hash of ID): 65 % training, 10 % calibration, 25 % test.
  The test set stays untouched for ``measure``.
* **Training data:** the assessable questions from the training documents with the
  teacher's label (distillation). Additionally – half-weighted – the confidently
  detected names, companies and places from the same documents with their gold label:
  they show the encoder how names appear in context without giving it anything
  the teacher did not see.
* **Encoder:** ``intfloat/multilingual-e5-small`` (MIT, 118 M parameters,
  12 layers, 384 dimensions) with classification head, fine-tuned.
* **Calibration:** temperature on the calibration part (minimum NLL) so that
  “95 %” really means about 95 % – the policy “keep also from 95 %” depends on this.
* **Export:** ONNX (fp32), then dynamic int8 quantisation. We measure the
  fp32-vs-int8 agreement on the calibration part; if int8 deviates by more than one
  point we keep only the int8 embedding (as with GLiNER).

Invocation (repo root; ``onnx`` needed only for export):

    uv run --project sidecar --group modellbau --with onnx \\
        python -m scripts.second_opinion.train \\
        --output sidecar/models/second-opinion
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from . import LABELS

ENCODER = "intfloat/multilingual-e5-small"
#: Fixed revision so a new training starts from the same baseline.
ENCODER_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MAX_LENGTH = 128
TEST_FRACTION = 0.25
CALIBRATION_FRACTION = 0.10


def part(document_id: str) -> str:
    """``train``, ``kalibrierung`` or ``test`` – fixed per document."""

    val = int(hashlib.sha256(document_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    if val < TEST_FRACTION:
        return "test"
    if val < TEST_FRACTION + CALIBRATION_FRACTION:
        return "kalibrierung"
    return "train"


def examples(question_defs: Path, teacher_path: Path) -> dict[str, list[tuple[str, int, float]]]:
    """(input, label index, weight) per part."""

    from presidio_sidecar.detection.second_opinion import ASSESSED_TYPES

    teacher_labels: dict[str, str] = {}
    for line in teacher_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            teacher_labels.update(json.loads(line)["labels"])
    result: dict[str, list[tuple[str, int, float]]] = {
        "train": [],
        "kalibrierung": [],
        "test": [],
    }
    for line in question_defs.read_text(encoding="utf-8").splitlines():
        document = json.loads(line)
        target = result[part(document["id"])]
        for place in document["stellen"]:
            if place["typ"] not in ASSESSED_TYPES:
                continue
            if place["frage"]:
                label = teacher_labels.get(place["id"])
                if label is not None:
                    target.append((place["eingabe"], LABELS.index(label), 1.0))
            elif part(document["id"]) == "train":
                target.append((place["eingabe"], LABELS.index(place["gold"]), 0.5))
    return result


def _temperature(logits, labels) -> float:  # type: ignore[no-untyped-def]
    import torch

    beste, beste_nll = 1.0, float("inf")
    for step_num in range(5, 400):
        temperature = step_num / 100
        nll = torch.nn.functional.cross_entropy(logits / temperature, labels).item()
        if nll < beste_nll:
            beste, beste_nll = temperature, nll
    return beste


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions", type=Path, default=Path("testdata/zweitmeinung/fragen.jsonl")
    )
    parser.add_argument("--teacher", type=Path, default=Path("testdata/zweitmeinung/lehrer.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("sidecar/models/second-opinion"))
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=4e-5)
    parser.add_argument("--seed", type=int, default=13)
    args = parser.parse_args(argv)

    import numpy
    import onnxruntime
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    data = examples(args.questions, args.teacher)
    for name, item_list in data.items():
        distribution = {
            label: sum(1 for item in item_list if item[1] == index)
            for index, label in enumerate(LABELS)
        }
        print(f"{name}: {len(item_list)} {distribution}", flush=True)

    device_type = "mps" if torch.backends.mps.is_available() else "cpu"
    tokenizer = AutoTokenizer.from_pretrained(ENCODER, revision=ENCODER_REVISION)
    model = AutoModelForSequenceClassification.from_pretrained(
        ENCODER,
        revision=ENCODER_REVISION,
        num_labels=len(LABELS),
        id2label=dict(enumerate(LABELS)),
        label2id={label: i for i, label in enumerate(LABELS)},
    ).to(device_type)

    def batch(item_list):  # type: ignore[no-untyped-def]
        encoded = tokenizer(
            [item[0] for item in item_list],
            truncation=True,
            max_length=MAX_LENGTH,
            padding=True,
            return_tensors="pt",
        )
        return (
            {key: value.to(device_type) for key, value in encoded.items()},
            torch.tensor([item[1] for item in item_list], device=device_type),
            torch.tensor([item[2] for item in item_list], device=device_type),
        )

    # Weight classes equally because terms dominate the questions.
    counter = numpy.bincount([item[1] for item in data["train"]], minlength=len(LABELS))
    classes = torch.tensor(
        (counter.sum() / (len(LABELS) * numpy.maximum(counter, 1))) ** 0.5,
        dtype=torch.float32,
        device=device_type,
    )
    optimierer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.01)
    size = 32
    steps = args.epochs * ((len(data["train"]) + size - 1) // size)
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimierer, max_lr=args.learning_rate, total_steps=steps, pct_start=0.1
    )
    beginn = time.monotonic()

    def vorhersagen(item_list):  # type: ignore[no-untyped-def]
        model.eval()
        outputs = []
        with torch.no_grad():
            for start in range(0, len(item_list), 64):
                input_data, _labels, _weights = batch(item_list[start : start + 64])
                outputs.append(model(**input_data).logits.float().cpu())
        return torch.cat(outputs)

    for epoche in range(args.epochs):
        model.train()
        order = list(data["train"])
        random.shuffle(order)
        total = 0.0
        for start in range(0, len(order), size):
            input_data, labels, weights = batch(order[start : start + size])
            logits = model(**input_data).logits
            loss = torch.nn.functional.cross_entropy(
                logits, labels, weight=classes, reduction="none"
            )
            loss = (loss * weights).sum() / weights.sum()
            optimierer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimierer.step()
            scheduler.step()
            total += loss.item()
        logits = vorhersagen(data["kalibrierung"])
        richtig = (logits.argmax(-1) == torch.tensor([i[1] for i in data["kalibrierung"]])).float()
        print(
            f"Epoche {epoche + 1}: Verlust {total / max(1, len(order) // size):.4f}, "
            f"Kalibrierung {richtig.mean().item():.3f} ({time.monotonic() - beginn:.0f} s)",
            flush=True,
        )

    logits = vorhersagen(data["kalibrierung"])
    labels = torch.tensor([item[1] for item in data["kalibrierung"]])
    temperature = _temperature(logits, labels)
    print(f"Temperatur {temperature:.2f}", flush=True)

    model = model.to("cpu").eval()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zweitmeinung-export-") as work:
        work_path = Path(work)
        tokenizer.save_pretrained(work_path)
        example = tokenizer(["verdacht: Beispiel | « Beispiel »"], return_tensors="pt")
        fp32 = work_path / "model.onnx"
        torch.onnx.export(
            model,
            (example["input_ids"], example["attention_mask"]),
            str(fp32),
            input_names=["input_ids", "attention_mask"],
            output_names=["logits"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "sequence"},
                "attention_mask": {0: "batch", 1: "sequence"},
                "logits": {0: "batch"},
            },
            opset_version=17,
            dynamo=False,
        )

        def onnx_logits(path: Path, item_list) -> numpy.ndarray:  # type: ignore[no-untyped-def]
            session = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            result = []
            for start in range(0, len(item_list), 64):
                encoded = tokenizer(
                    [item[0] for item in item_list[start : start + 64]],
                    truncation=True,
                    max_length=MAX_LENGTH,
                    padding=True,
                    return_tensors="np",
                )
                result.append(
                    session.run(
                        None,
                        {
                            "input_ids": encoded["input_ids"].astype(numpy.int64),
                            "attention_mask": encoded["attention_mask"].astype(numpy.int64),
                        },
                    )[0]
                )
            return numpy.concatenate(result)

        reference = onnx_logits(fp32, data["kalibrierung"]).argmax(-1)
        int8 = work_path / "model.int8.onnx"
        quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8)
        equal = float((onnx_logits(int8, data["kalibrierung"]).argmax(-1) == reference).mean())
        quantization_desc = "dynamic int8 (QInt8), alle MatMul und Gather"
        if equal < 0.99:
            quantize_dynamic(
                str(fp32), str(int8), weight_type=QuantType.QUInt8, op_types_to_quantize=["Gather"]
            )
            equal = float((onnx_logits(int8, data["kalibrierung"]).argmax(-1) == reference).mean())
            quantization_desc = "dynamic int8 (QUInt8), nur Gather"
        print(f"{quantization_desc}: Übereinstimmung mit fp32 {equal:.4f}", flush=True)

        for name in ("model.int8.onnx", "tokenizer.json"):
            shutil.copyfile(work_path / name, args.output / name)

    version = f"zweitmeinung-{datetime.now(UTC).strftime('%Y%m%d')}"
    config = {
        "version": version,
        "labels": list(LABELS),
        "temperature": temperature,
        "max_length": MAX_LENGTH,
        "pad_id": tokenizer.pad_token_id,
        "encoder": f"{ENCODER}@{ENCODER_REVISION}",
        "quantization": quantization_desc,
        "int8_agreement": round(equal, 4),
        "training": {
            "examples": len(data["train"]),
            "calibration": len(data["kalibrierung"]),
            "epochs": args.epochs,
            "learning_rate": args.learning_rate,
            "seed": args.seed,
        },
    }
    (args.output / "zweitmeinung.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    size_mb = (args.output / "model.int8.onnx").stat().st_size / 1e6
    print(f"geschrieben: {args.output} ({size_mb:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
