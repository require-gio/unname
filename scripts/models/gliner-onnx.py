#!/usr/bin/env python3
"""GLiNER multi-PII einmalig nach ONNX (int8) konvertieren (Plan Phase I).

Die App laedt die Namenserkennung seit Phase I nicht mehr aus dem Bundle,
sondern einmalig ueber die Huelle (``models.rs``). Geladen wird dabei nur
ONNX und Tokenizer-Daten – kein Pickle (``pytorch_model.bin``) und kein
PyTorch-Gewicht. Dieses Skript erzeugt genau die Dateien, die danach unter
der eigenen Organisation gehostet werden; ``scripts/models/modelle.py``
traegt ihre Groessen und SHA-256 in ``app/src-tauri/models.lock.json`` ein.

Ablauf:

1. Aus dem lokalen Snapshot (feste Revision) werden nur ``model.safetensors``,
   ``gliner_config.json`` und die Tokenizer-Dateien in einen leeren Ordner
   kopiert – ``pytorch_model.bin`` bleibt draussen, damit nichts aus einem
   Pickle geladen werden kann.
2. ``GLiNER.export_to_onnx()`` schreibt das fp32-Modell.
3. Dynamische int8-Quantisierung **nur der Einbettungstabelle** (``Gather``).
   Gemessen am 24.10.2026: Die Tabelle von mDeBERTa (250 000 × 768) ist zwei
   Drittel des Modells; int8 halbiert die Datei (1,16 GB → 0,58 GB) ohne
   abweichenden Treffer. Jede Quantisierung der MatMul-Schichten – alle,
   ohne den GLiNER-Kopf, nur die FFN, per Kanal – verlor dagegen 24–52 % der
   Treffer bei 0,35. Deshalb bleiben sie fp32.
4. Vergleich PyTorch gegen ONNX an festen, erfundenen Beispielsaetzen: Ein
   Treffer gilt als same_count, wenn Typ und Spanne same_count sind; dazu die
   groesste Score-Abweichung gleicher Treffer. Weicht ein Treffer ab, endet
   das Skript mit Fehler.

Aufruf (aus dem Repo-Wurzelordner; ``onnx`` braucht nur der Export):

    uv run --project sidecar --group modellbau --with onnx python scripts/models/gliner-onnx.py \\
        --snapshot sidecar/models/gliner --output sidecar/models/gliner-onnx

Das Ergebnis ist nicht bitgleich reproduzierbar (Export und Quantisierung
haengen von den Paketversionen ab). Massgeblich ist die eine erzeugte Datei:
Sie wird hochgeladen, und ihr Hash steht in der signierten App. Eine neu
erzeugte Datei braucht deshalb auch einen neuen Manifest-Eintrag.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

#: Revision von ``urchade/gliner_multi_pii-v1``, aus der konvertiert wird
#: (dieselbe wie im bisherigen Bundle, ``prepare-sidecar-assets.py``).
GLINER_REVISION = "f334163a5d1dcdbacf365c4527538e3da3ecfb18"
ONNX_FILE = "model.int8.onnx"
#: Was aus dem Snapshot in den Export darf. Alles andere – vor allem
#: ``pytorch_model.bin`` – bleibt draussen.
SNAPSHOT_FILES = (
    "model.safetensors",
    "gliner_config.json",
    "spm.model",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "added_tokens.json",
)
#: Was ausgeliefert wird. Das fp32-Modell faellt weg.
DELIVERY = frozenset(
    {
        ONNX_FILE,
        "gliner_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "added_tokens.json",
        "spm.model",
    }
)
LABELS = ("person", "organization", "location")
THRESHOLD = 0.35
#: Erfundene Saetze im Stil der Dokumente, die die App sieht; keine echten
#: Personendaten.
EXAMPLES = (
    "Sehr geehrte Frau Annegret Wolkenstein, vielen Dank fuer Ihr Schreiben vom 3. Mai.",
    "Die Muster Logistik GmbH aus Bielefeld liefert die Ware an Herrn Jaromir Feldkamp.",
    "Bitte ueberweisen Sie den Betrag an die Sparkasse Oberhavel, z. Hd. Lioba Sternfeld.",
    "Dear Mr. Quentin Harrowgate, your order from Brightwater Supplies Ltd has shipped to Leeds.",
    "Im Termin vor dem Amtsgericht Celle vertrat Rechtsanwalt Dr. Ewald Brunsmann die Klaegerin.",
    "Kontakt: Mirela Hasenbein, Vertrieb, Nordlicht Textil AG, Hamburg-Altona.",
    "Herr Bertold Quast wohnt seit 2019 in der Lindenallee in Wernigerode.",
    "Die Firma Kesselring & Soehne KG hat ihren Sitz in Graz, Oesterreich.",
    "Rechnung Nr. 2026-0412 an Optik Rauschenbach, Inhaberin Wiebke Rauschenbach, Lueneburg.",
    "Mietvertrag zwischen der Hausverwaltung Elbblick GmbH und Frau Svenja Oltmanns.",
    "Unser Mitarbeiter Konstantin Pfeiffer meldet sich bis Freitag bei Ihnen.",
    "Die Versicherungsnummer von Herrn Edgar Lindqvist wurde an die Allianz weitergeleitet.",
    "Please forward the contract to Priya Ramaswamy at Northbridge Analytics in Manchester.",
    "Mit freundlichen Gruessen, Ihre Theresa Mangold, Steuerkanzlei Mangold & Partner, Freiburg.",
    "Das Paket wurde in der Filiale Muenchen-Pasing von Herrn Ilias Konstantinou abgeholt.",
    "Die Gesellschafterversammlung der Brenntag Nord AG findet in Bremen statt.",
    "Frau Dr. Henrike Albrecht-Sommer leitet die Abteilung Recht bei der Stadtwerke Kassel AG.",
    "Herr Tobias Wendland hat am 12.03. bei der Volksbank Mittelhessen ein Konto eroeffnet.",
    "Zeugin Olga Petrenko gab an, den Unfall auf der A7 bei Hildesheim beobachtet zu haben.",
    "Bitte wenden Sie sich an unsere Partnerin Camille Duvernoy in Strassburg.",
    "Die Lieferung von Holzbau Ritter aus Rosenheim verzoegert sich um zwei Wochen.",
    "Auftraggeber ist die Gemeinde Wennigsen, vertreten durch Buergermeister Lars Hoppe.",
    "Our CFO, Daniel Okonkwo, will join the call from Lagos together with Grace Mbeki.",
    "Der Patient Friedhelm Struck wurde am Klinikum Oldenburg aufgenommen.",
    "Antragstellerin: Merve Yildirim, wohnhaft in Duisburg-Marxloh.",
    "Die Pruefung durch die KPMG AG Wirtschaftspruefungsgesellschaft ist abgeschlossen.",
    "Herr Jonas Albers und Frau Katharina Albers ziehen nach Kiel.",
    "Gutachter Prof. Dr. Reinhold Esser kommt aus Aachen.",
    "Die Spedition Hellmann Worldwide Logistics hat die Container in Hamburg uebernommen.",
    "Ansprechpartnerin bei der Techniker Krankenkasse ist Frau Sabine Kowalczyk.",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    snapshot: Path = args.snapshot.resolve()
    output_dir: Path = args.output.resolve()
    if not (snapshot / "model.safetensors").is_file():
        print(f"Kein safetensors-Snapshot unter {snapshot}.", file=sys.stderr)
        return 2
    if output_dir.exists() and any(output_dir.iterdir()):
        print(f"Ausgabeordner {output_dir} ist nicht leer.", file=sys.stderr)
        return 2

    from gliner import GLiNER
    from onnxruntime.quantization import QuantType, quantize_dynamic

    with tempfile.TemporaryDirectory(prefix="gliner-onnx-") as tmp_dir:
        source_path = Path(tmp_dir) / "snapshot"
        source_path.mkdir()
        for name in SNAPSHOT_FILES:
            if (snapshot / name).is_file():
                shutil.copy2(snapshot / name, source_path / name)
        export = Path(tmp_dir) / "export"

        print("Lade PyTorch-Modell (safetensors) …", flush=True)
        modell = GLiNER.from_pretrained(str(source_path), local_files_only=True)
        modell.eval()
        print("Exportiere nach ONNX (fp32) …", flush=True)
        started = time.monotonic()
        modell.export_to_onnx(export, quantize=False)
        print("Quantisiere die Einbettungstabelle (int8) …", flush=True)
        quantize_dynamic(
            model_input=str(export / "model.onnx"),
            model_output=str(export / ONNX_FILE),
            weight_type=QuantType.QUInt8,
            op_types_to_quantize=["Gather"],
        )
        print(f"  fertig nach {time.monotonic() - started:.0f} s", flush=True)

        output_dir.mkdir(parents=True, exist_ok=True)
        for file_path in sorted(export.iterdir()):
            if file_path.name in DELIVERY and file_path.is_file():
                shutil.copy2(file_path, output_dir / file_path.name)

    print("Vergleiche PyTorch gegen ONNX int8 …", flush=True)
    onnx = GLiNER.from_pretrained(
        str(output_dir),
        local_files_only=True,
        load_onnx_model=True,
        onnx_model_file=ONNX_FILE,
    )
    comparison = compare_models(modell, onnx)

    files = [
        {"path": file_path.name, "size": file_path.stat().st_size, "sha256": _sha256(file_path)}
        for file_path in sorted(output_dir.iterdir())
        if file_path.is_file()
    ]
    report = {
        "component": "ner",
        "source_model": "urchade/gliner_multi_pii-v1",
        "source_revision": GLINER_REVISION,
        "quantization": "dynamic int8 (QUInt8), only Gather",
        "files": files,
        "comparison": comparison,
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    (output_dir.parent / f"{output_dir.name}.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if comparison["only_pytorch"] == 0 and comparison["only_onnx"] == 0 else 1


def compare_models(pytorch: object, onnx: object) -> dict[str, object]:
    same_count = 0
    only_pytorch = 0
    only_onnx = 0
    deviation = 0.0
    for sentence in EXAMPLES:
        expected = _matches(pytorch, sentence)
        actual = _matches(onnx, sentence)
        common = expected.keys() & actual.keys()
        same_count += len(common)
        only_pytorch += len(expected.keys() - actual.keys())
        only_onnx += len(actual.keys() - expected.keys())
        for key in common:
            deviation = max(deviation, abs(expected[key] - actual[key]))
    gesamt = same_count + only_pytorch
    return {
        "sentences": len(EXAMPLES),
        "threshold": THRESHOLD,
        "same": same_count,
        "only_pytorch": only_pytorch,
        "only_onnx": only_onnx,
        "agreement": round(same_count / gesamt, 4) if gesamt else 1.0,
        "max_score_delta": round(deviation, 4),
    }


def _matches(modell: object, sentence: str) -> dict[tuple[str, int, int], float]:
    predictions = modell.predict_entities(  # type: ignore[attr-defined]
        sentence, list(LABELS), threshold=THRESHOLD, flat_ner=False, multi_label=True
    )
    return {
        (str(v["label"]), int(v["start"]), int(v["end"])): float(v["score"]) for v in predictions
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_path:
        for block in iter(lambda: file_path.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
