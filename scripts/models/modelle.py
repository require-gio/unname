#!/usr/bin/env python3
"""Modell-Manifest und Modellpaket fuer die Ersteinrichtung (Plan Phase I).

Die Huelle laedt die Modelle einmalig nach und prueft jede Datei gegen
``app/src-tauri/models.lock.json``. Das Manifest wird per ``include_str!`` in
die signierte App gebaut; was dort nicht mit Groesse und SHA-256 steht, wird
nicht installiert.

``manifest`` erzeugt das Manifest aus lokal vorliegenden, bereits gepruefen
Dateien:

* ``ner`` – GLiNER multi-PII als ONNX aus ``scripts/models/gliner-onnx.py``,
* je Sprachpaket (``de``, ``en``; seit Phase J) das kleine spaCy-Wheel
  ``de_core_news_sm`` bzw. ``en_core_web_sm`` 3.8.0 (Hash wie in
  ``sidecar/uv.lock``). Installiert wird nur der Datenordner; jede entpackte
  Datei steht mit eigenem Hash im Manifest, Python-Code im Wheel wird nie
  ausgepackt. Stoppwoerter, Kontextwoerter, Monatsnamen, Platzhalter und
  Kalibrierung des Pakets sind Text und liegen signiert in der App
  (``sidecar/…/languages.py``, ``stoppwoerter/<sprache>_*.txt``),
* ``ocr`` – ``deu``/``eng.traineddata`` aus ``tesseract-ocr/tessdata_best``,
* ``classifier`` (seit Phase K, optional) – die Zweitmeinung aus
  ``scripts/zweitmeinung/trainieren.py``: ONNX int8, Tokenizer, Konfiguration.

``paket`` schnuert daraus eine Paketdatei fuer Geraete ohne Internet
(„Modellpaket aus Datei installieren"): ein Zip ohne Kompression mit
``<bestandteil>/<file>``. Die Huelle prueft jede Datei gegen dasselbe
Manifest; das Paket selbst braucht deshalb keine eigene Signatur.

Aufruf (aus dem Repo-Wurzelordner):

    python3 scripts/models/modelle.py manifest --ner sidecar/models/gliner-onnx \\
        --spacy-wheel …/de_core_news_sm-3.8.0-py3-none-any.whl \\
        --spacy-wheel …/en_core_web_sm-3.8.0-py3-none-any.whl --tessdata …/tessdata
    python3 scripts/models/modelle.py sprachen --spacy-wheel … --spacy-wheel …
    python3 scripts/models/modelle.py paket --ner sidecar/models/gliner-onnx \\
        --spacy-wheel … --spacy-wheel … --tessdata … --output unname-modelle.zip

``sprachen`` ersetzt nur die Sprachpakete in einem vorhandenen Manifest; NER
und Texterkennung muessen dafuer nicht lokal vorliegen. Ebenso ersetzt
``zweitmeinung --classifier sidecar/models/zweitmeinung`` nur den
Klassifikator (oder fuegt ihn an). ``adressen`` setzt nur die URLs neu (etwa
nach einem Umzug der Spiegel); Groessen und Hashes bleiben.

``spiegel`` legt den Upload-Ordner fuer die eigenen Spiegel an: ``hf/`` mit
``<bestandteil>/<file>`` samt Model Card und Lizenzen, ``github/`` mit
flachen Namen ``<bestandteil>--<file>`` fuer das Release. Jede Datei wird
vorher gegen das Manifest geprueft; grosse Dateien werden verlinkt, nicht
kopiert:

    python3 scripts/models/modelle.py spiegel --ner sidecar/models/gliner-onnx \\
        --classifier sidecar/models/zweitmeinung --spacy-wheel … --spacy-wheel … \\
        --tessdata …/tessdata --output modelle-upload
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "app" / "src-tauri" / "models.lock.json"
SCHEMA = 1
PAKET_FORMAT = "unname-modellpaket"

#: Eigene Spiegel fuer Dateien ohne passende Quelle beim Hersteller (M-1).
#: Die Namen ``unname`` sind auf beiden Plattformen fremd vergeben. Fehlt eine
#: Datei beim Spiegel, liefert er 404, und die Huelle meldet „Quelle nicht
#: erreichbar"; installiert wird ohnehin nur, was Groesse und SHA-256 trifft.
HF_REPO = "Giorgi-KI/unname-app"
GH_REPO = "require-gio/unname"
#: Revision der Spiegel; zeigt auf den Stand, der die Dateien dieses Manifests traegt.
SPIEGEL_REVISION = "v1"
#: Auf GitHub teilen sich die Modelle das Repository mit den App-Releases
#: (``v0.2.0``). Das Praefix haelt beide auseinander: ``models-v1`` zaehlt nie als
#: App-Version, und der Release-Workflow (``.github/workflows/models-mirror.yml``)
#: legt es als eigenes, nie „latest" markiertes Release an.
GH_SPIEGEL_TAG = f"models-{SPIEGEL_REVISION}"
HF_SPIEGEL = f"https://huggingface.co/{HF_REPO}/resolve/{SPIEGEL_REVISION}"
GH_SPIEGEL = f"https://github.com/{GH_REPO}/releases/download/{GH_SPIEGEL_TAG}"

GLINER_QUELLE = "urchade/gliner_multi_pii-v1@f334163a5d1dcdbacf365c4527538e3da3ecfb18"
NER_FILES = (
    "model.int8.onnx",
    "gliner_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
)
SPACY_VERSION = "3.8.0"
#: Sprachpakete (Phase J): Sprache → spaCy-Paket und SHA-256 des Wheels (wie in
#: ``sidecar/uv.lock``). Klein statt ``lg``: Die Namenserkennung ist GLiNER,
#: spaCy ist nur Satzzerlegung und Rueckfall auf schwachen Geraeten.
LANGUAGE_PACKAGES = {
    "de": (
        "de_core_news_sm",
        "fec69fec52b1780f2d269d5af7582a5e28028738bd3190532459aeb473bfa3e7",
    ),
    "en": (
        "en_core_web_sm",
        "1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85",
    ),
}
TESSDATA_COMMIT = "e12c65a915945e4c28e237a9b52bc4a8f39a0cec"
TESSDATA_URL = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/{TESSDATA_COMMIT}"
TESSDATA_FILES = ("deu.traineddata", "eng.traineddata")
CLASSIFIER_FILES = ("model.int8.onnx", "tokenizer.json", "zweitmeinung.json")

SOURCES = {
    "huggingface": {"hosts": ["huggingface.co", "*.huggingface.co", "*.hf.co"]},
    "github": {
        "hosts": [
            "github.com",
            "objects.githubusercontent.com",
            "release-assets.githubusercontent.com",
            "raw.githubusercontent.com",
        ]
    },
}
#: Was nie installiert wird: ausfuehrbarer Code und Pickle.
FORBIDDEN = (
    ".py",
    ".pyc",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".pkl",
    ".pickle",
    ".pt",
    ".pth",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_arguments(argv)
    if args.command in {"manifest", "sprachen", "zweitmeinung", "adressen"}:
        if args.command == "adressen":
            manifest = replace_addresses(json.loads(args.output.read_text(encoding="utf-8")))
        elif args.command == "manifest":
            manifest = build_manifest(args.ner, args.spacy_wheel, args.tessdata)
            if args.classifier is not None:
                manifest = replace_classifier(manifest, args.classifier)
        elif args.command == "zweitmeinung":
            vorhanden = json.loads(args.output.read_text(encoding="utf-8"))
            manifest = replace_classifier(vorhanden, args.classifier)
        else:
            vorhanden = json.loads(args.output.read_text(encoding="utf-8"))
            manifest = replace_languages(vorhanden, args.spacy_wheel)
        args.output.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"geschrieben: {args.output}")
        return 0
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    quellen: dict[str, Path] = {"ner": args.ner, "ocr": args.tessdata}
    if args.classifier is not None:
        quellen["classifier"] = args.classifier
    for wheel in args.spacy_wheel:
        quellen[_language_of_wheel(wheel)] = wheel.parent
    if args.command == "spiegel":
        spiegel_bauen(manifest, quellen, args.output)
        print(f"geschrieben: {args.output}")
        return 0
    bestandteile = args.components.split(",") if args.components else None
    build_package(manifest, quellen, args.output, bestandteile)
    print(f"geschrieben: {args.output}")
    return 0


def build_manifest(ner: Path, wheels: list[Path], tessdata: Path) -> dict[str, object]:
    sprachen = _language_packages(wheels)
    return {
        "schema": SCHEMA,
        "revision": datetime.now(UTC).strftime("%Y-%m-%d"),
        "sources": SOURCES,
        "components": [
            {
                "id": "ner",
                "kind": "ner",
                "origin": f"{GLINER_QUELLE} · ONNX, Einbettungen int8",
                "license": "Apache-2.0",
                "files": [
                    _file_entry(ner / name, name, mirror_urls("ner", name)) for name in NER_FILES
                ],
            },
            *sprachen,
            {
                "id": "ocr",
                "kind": "ocr",
                "origin": f"tesseract-ocr/tessdata_best@{TESSDATA_COMMIT}",
                "license": "Apache-2.0",
                # Phase U: Auf dem Mac erkennt Apple Vision; tessdata nur fuer Windows.
                "platforms": ["windows"],
                "files": [
                    _file_entry(tessdata / name, name, mirror_urls("ocr", name))
                    for name in TESSDATA_FILES
                ],
            },
        ],
    }


def replace_languages(manifest: dict[str, object], wheels: list[Path]) -> dict[str, object]:
    """Ersetzt die Sprachpakete eines Manifests; Reihenfolge: NER, Sprachen, Rest."""

    teile = list(manifest["components"])  # type: ignore[arg-type]
    ohne = [teil for teil in teile if teil["kind"] != "language"]
    sprachen = _language_packages(wheels)
    ner = [teil for teil in ohne if teil["kind"] == "ner"]
    rest = [teil for teil in ohne if teil["kind"] != "ner"]
    return {
        **manifest,
        "revision": datetime.now(UTC).strftime("%Y-%m-%d"),
        "components": [*ner, *sprachen, *rest],
    }


def classifier_component(folder: Path) -> dict[str, object]:
    """Die Zweitmeinung (Phase K) als optionaler Bestandteil."""

    konfiguration = json.loads((folder / "zweitmeinung.json").read_text(encoding="utf-8"))
    return {
        "id": "classifier",
        "kind": "classifier",
        "origin": (
            f"{konfiguration['version']} · feinjustiert aus {konfiguration['encoder']} · ONNX int8"
        ),
        "license": "MIT",
        "files": [
            _file_entry(folder / name, name, mirror_urls("classifier", name))
            for name in CLASSIFIER_FILES
        ],
    }


def replace_classifier(manifest: dict[str, object], folder: Path) -> dict[str, object]:
    """Ersetzt den Klassifikator eines Manifests oder fuegt ihn hinten an."""

    teile = [
        teil
        for teil in manifest["components"]  # type: ignore[union-attr]
        if teil["kind"] != "classifier"
    ]
    return {
        **manifest,
        "revision": datetime.now(UTC).strftime("%Y-%m-%d"),
        "components": [*teile, classifier_component(folder)],
    }


def mirror_urls(teil: str, name: str) -> dict[str, str]:
    """Adressen einer Datei je Quelle.

    Hugging Face ist immer der eigene Spiegel. Unter ``github`` steht der
    Hersteller, wo er die Datei selbst anbietet (spaCy, tessdata), sonst das
    eigene Release.
    """

    if teil in LANGUAGE_PACKAGES:
        paket = LANGUAGE_PACKAGES[teil][0]
        github = (
            f"https://github.com/explosion/spacy-models/releases/download/"
            f"{paket}-{SPACY_VERSION}/{name}"
        )
    elif teil == "ocr":
        github = f"{TESSDATA_URL}/{name}"
    else:
        github = f"{GH_SPIEGEL}/{teil}--{name}"
    return {"huggingface": f"{HF_SPIEGEL}/{teil}/{name}", "github": github}


def replace_addresses(manifest: dict[str, object]) -> dict[str, object]:
    """Setzt die URLs neu; Dateien, Groessen und Hashes bleiben unveraendert."""

    teile = []
    for teil in manifest["components"]:  # type: ignore[union-attr]
        files = [{**file, "urls": mirror_urls(teil["id"], file["path"])} for file in teil["files"]]
        teile.append({**teil, "files": files})
    return {
        **manifest,
        "revision": datetime.now(UTC).strftime("%Y-%m-%d"),
        "components": teile,
    }


#: Ab dieser Groesse wird verlinkt statt kopiert (``hf upload`` und ``gh``
#: folgen dem Link).
_LINK_THRESHOLD = 16 * 1024 * 1024


def spiegel_bauen(manifest: dict[str, object], quellen: dict[str, Path], output_path: Path) -> None:
    """Upload-Ordner fuer die eigenen Spiegel; prueft jede Datei vorher."""

    if output_path.exists() and any(output_path.iterdir()):
        raise SystemExit(f"{output_path} ist nicht leer.")
    hf, gh = output_path / "hf", output_path / "github"
    for teil in manifest["components"]:  # type: ignore[union-attr]
        if teil["id"] not in quellen:
            raise SystemExit(f"Fuer {teil['id']} fehlt die lokale Quelle.")
        for file in teil["files"]:
            path = quellen[teil["id"]] / file["path"]
            if path.stat().st_size != file["size"] or _sha256(path) != file["sha256"]:
                raise SystemExit(f"{teil['id']}/{file['path']}: weicht vom Manifest ab.")
            for folder, basis, url in (
                (hf, HF_SPIEGEL, file["urls"]["huggingface"]),
                (gh, GH_SPIEGEL, file["urls"]["github"]),
            ):
                if url.startswith(basis + "/"):
                    _ablegen(path, folder / url[len(basis) + 1 :])
    lizenzen = hf / "LICENSES"
    lizenzen.mkdir(parents=True)
    (lizenzen / "Apache-2.0.txt").write_bytes(
        (REPO / "sidecar" / "packaging" / "licenses" / "Apache-2.0.txt").read_bytes()
    )
    (lizenzen / "MIT.txt").write_bytes((REPO / "LICENSE").read_bytes())
    (hf / "README.md").write_text(_model_card(manifest), encoding="utf-8")
    (output_path / "RELEASE.md").write_text(_release_notes(manifest), encoding="utf-8")


def _ablegen(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.stat().st_size >= _LINK_THRESHOLD:
        target_path.symlink_to(source_path.resolve())
    else:
        target_path.write_bytes(source_path.read_bytes())


def _file_table(manifest: dict[str, object]) -> str:
    lines = ["| Bestandteil | Datei | Bytes | SHA-256 |", "|---|---|---:|---|"]
    for teil in manifest["components"]:  # type: ignore[union-attr]
        for file in teil["files"]:
            lines.append(
                f"| `{teil['id']}` | `{file['path']}` | {file['size']} | `{file['sha256']}` |"
            )
    return "\n".join(lines)


def _model_card(manifest: dict[str, object]) -> str:
    herkunft = "\n".join(
        f"| `{teil['id']}` | {teil['origin']} | {teil['license']} |"
        for teil in manifest["components"]  # type: ignore[union-attr]
    )
    return f"""---
license: other
license_name: mixed-apache-2.0-and-mit
license_link: https://huggingface.co/{HF_REPO}/tree/main/LICENSES
library_name: onnx
tags:
  - onnx
  - pii
  - anonymization
  - gliner
---

# unname – Modelldateien

Spiegel der Modelle, die die Desktop-App **unname** bei der Ersteinrichtung
lädt. Die App installiert nur Dateien, deren Größe und SHA-256 im signierten
Manifest `models.lock.json` (Revision {manifest["revision"]}) stehen; jede
Änderung an diesem Repository ohne neues Manifest wird abgelehnt.

## Herkunft und Lizenzen

| Bestandteil | Herkunft | Lizenz |
|---|---|---|
{herkunft}

Lizenztexte: [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt),
[`LICENSES/MIT.txt`](LICENSES/MIT.txt). Die spaCy-Wheels (`de`, `en`) enthalten
ihre Lizenz selbst.

## Änderungen gegenüber den Originalen (Apache-2.0 § 4 b)

* `ner/`: [`urchade/gliner_multi_pii-v1`](https://huggingface.co/urchade/gliner_multi_pii-v1)
  @ `{GLINER_QUELLE.split("@")[1]}`, von PyTorch nach ONNX exportiert und die
  Einbettungsmatrizen (Gather) dynamisch auf int8 quantisiert. Gewichte sonst
  unverändert; Tokenizer und Konfiguration wie im Original. Encoder
  `microsoft/mdeberta-v3-base` (MIT).
* `classifier/`: eigenes Modell. Klassifikationskopf auf
  [`intfloat/multilingual-e5-small`](https://huggingface.co/intfloat/multilingual-e5-small)
  (MIT), feinjustiert auf einem **erfundenen** Korpus (keine echten
  Personendaten), dann nach ONNX exportiert und int8-quantisiert. Parameter in
  `classifier/zweitmeinung.json`.
* `de/`, `en/`, `ocr/`: unverändert vom Hersteller (spaCy 3.8.0,
  `tesseract-ocr/tessdata_best` @ `{TESSDATA_COMMIT}`); hier nur als zweite
  Quelle.

## Dateien

{_file_table(manifest)}
"""


def _release_notes(manifest: dict[str, object]) -> str:
    return f"""Modelldateien für unname, Manifest-Revision {manifest["revision"]}.

Zweite Quelle neben https://huggingface.co/{HF_REPO}. Herkunft, Lizenzen und
Änderungen: siehe Model Card dort. Die App prüft jede Datei gegen SHA-256.

{_file_table(manifest)}
"""


def _language_of_wheel(wheel: Path) -> str:
    for sprache, (paket, _hash) in LANGUAGE_PACKAGES.items():
        if wheel.name == f"{paket}-{SPACY_VERSION}-py3-none-any.whl":
            return sprache
    raise SystemExit(f"{wheel.name}: kein bekanntes Sprachpaket.")


def _language_packages(wheels: list[Path]) -> list[dict[str, object]]:
    nach_sprache = {_language_of_wheel(wheel): wheel for wheel in wheels}
    fehlend = sorted(set(LANGUAGE_PACKAGES) - set(nach_sprache))
    if fehlend:
        raise SystemExit("Es fehlen Sprachpakete: " + ", ".join(fehlend))
    return [_language_package(sprache, nach_sprache[sprache]) for sprache in LANGUAGE_PACKAGES]


def _language_package(sprache: str, wheel: Path) -> dict[str, object]:
    paket, expected = LANGUAGE_PACKAGES[sprache]
    if _sha256(wheel) != expected:
        raise SystemExit(f"{wheel.name}: SHA-256 weicht von sidecar/uv.lock ab.")
    prefix = f"{paket}/{paket}-{SPACY_VERSION}/"
    return {
        "id": sprache,
        "kind": "language",
        "origin": f"explosion/spacy-models {paket} {SPACY_VERSION}",
        "license": "MIT",
        "files": [
            {
                **_file_entry(wheel, wheel.name, mirror_urls(sprache, wheel.name)),
                "unpack": {"prefix": prefix, "files": _wheel_inhalt(wheel, prefix)},
            }
        ],
    }


def build_package(
    manifest: dict[str, object],
    quellen: dict[str, Path],
    output_path: Path,
    bestandteile: list[str] | None = None,
) -> None:
    """Zip ohne Kompression; jede Datei wird vorher gegen das Manifest geprueft."""

    gewaehlt = [
        teil
        for teil in manifest["components"]  # type: ignore[union-attr]
        if bestandteile is None or teil["id"] in bestandteile
    ]
    with zipfile.ZipFile(
        output_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True
    ) as paket:
        paket.writestr(
            "unname-modellpaket.json",
            json.dumps(
                {
                    "format": PAKET_FORMAT,
                    "manifest_revision": manifest["revision"],
                    "components": [teil["id"] for teil in gewaehlt],
                },
                indent=2,
            ),
        )
        for teil in gewaehlt:
            for file in teil["files"]:
                path = quellen[teil["id"]] / file["path"]
                if path.stat().st_size != file["size"] or _sha256(path) != file["sha256"]:
                    raise SystemExit(f"{teil['id']}/{file['path']}: weicht vom Manifest ab.")
                paket.write(path, f"{teil['id']}/{file['path']}")


def _file_entry(path: Path, name: str, urls: dict[str, str]) -> dict[str, object]:
    _path_check(name)
    return {
        "path": name,
        "size": path.stat().st_size,
        "sha256": _sha256(path),
        "urls": urls,
    }


def _wheel_inhalt(wheel: Path, prefix: str) -> list[dict[str, object]]:
    content = []
    with zipfile.ZipFile(wheel) as archiv:
        for eintrag in sorted(archiv.infolist(), key=lambda info: info.filename):
            if eintrag.is_dir() or not eintrag.filename.startswith(prefix):
                continue
            relativ = eintrag.filename[len(prefix) :]
            _path_check(relativ)
            digest = hashlib.sha256()
            with archiv.open(eintrag) as file:
                for block in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(block)
            content.append(
                {
                    "path": relativ,
                    "size": eintrag.file_size,
                    "sha256": digest.hexdigest(),
                }
            )
    return content


def _path_check(relativ: str) -> None:
    path = PurePosixPath(relativ)
    if path.is_absolute() or ".." in path.parts or relativ.endswith(FORBIDDEN):
        raise SystemExit(f"Unzulaessiger Pfad im Manifest: {relativ}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "manifest",
        "paket",
        "sprachen",
        "zweitmeinung",
        "adressen",
        "spiegel",
    ):
        command = commands.add_parser(name)
        if name == "adressen":
            command.add_argument("--output", type=Path, default=MANIFEST)
            continue
        if name == "zweitmeinung":
            command.add_argument("--classifier", type=Path, required=True)
            command.add_argument("--output", type=Path, default=MANIFEST)
            continue
        command.add_argument("--spacy-wheel", type=Path, action="append", required=True)
        if name != "sprachen":
            command.add_argument("--ner", type=Path, required=True)
            command.add_argument("--tessdata", type=Path, required=True)
            command.add_argument("--classifier", type=Path, help="Zweitmeinung (optional)")
    commands.choices["manifest"].add_argument("--output", type=Path, default=MANIFEST)
    commands.choices["sprachen"].add_argument("--output", type=Path, default=MANIFEST)
    commands.choices["paket"].add_argument("--manifest", type=Path, default=MANIFEST)
    commands.choices["paket"].add_argument("--output", type=Path, required=True)
    commands.choices["paket"].add_argument("--components", help="z. B. ner,de,en,classifier")
    commands.choices["spiegel"].add_argument("--manifest", type=Path, default=MANIFEST)
    commands.choices["spiegel"].add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
