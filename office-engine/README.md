# unname-office-engine

DOCX → PDF for the page-faithful Word view in the review step. It is a program
of its own with a narrow interface, so the engine stays replaceable until the
page-faithful spike has measured it against Word.

**Not in the installation package.** Without an engine there is no
"page-faithful" mode; Word documents appear as before through docx-preview.

## Interface

| Command | Input | Output |
|---|---|---|
| `unname-office-engine info` | – | one JSON line: `{"engine", "version", "protocol": 1}` |
| `unname-office-engine docx-to-pdf [--fonts DIR]...` | DOCX on stdin (at most 64 MB) | PDF on stdout |

Exit codes: 0 success, 2 bad usage, 3 input too large, 4 conversion failed.
Messages on stderr name only the kind of error, never content. The engine gets
no path, writes no file of the document and does not talk to the network.

## Build

Exactly one engine per build, chosen through a feature; versions are pinned.

```sh
# office2pdf 0.10.0 (Typst; needs rustc ≥ 1.92) – the default
cargo build --release
# docxide-pdf 0.20.1
cargo build --release --no-default-features --features docxide --target-dir target-docxide
```

In development, the sidecar finds `target/release/unname-office-engine` by
itself; another file through `PRESIDIO_OFFICE_ENGINE`. In the frozen sidecar
only `assets/office-engine/unname-office-engine` from the signed package counts
(`sidecar/src/presidio_sidecar/office_engine.py`).

## Measure

`scripts/page-faithful-spike.py` measures engines against Word (SSIM at
150 DPI, page count, code localization, time, memory) and applies the decision
rule: same page count in ≥ 85 % of the documents, mean SSIM > 0.85, 100 % of
the codes localized.

## Licenses

- office2pdf: Apache-2.0 (pulls in Typst, Apache-2.0)
- docxide-pdf: Apache-2.0 – creates a font cache in the user folder
  (`~/Library/Caches/docxide-pdf`, `%LOCALAPPDATA%\docxide-pdf\cache`)

If an engine goes into the package, its license notice and those of its
dependencies belong in `THIRD_PARTY_NOTICES`.
