# unname — remove personal data without anything leaving your device

unname detects and masks personal data in documents and text — **entirely on
your own device**. No cloud call, no cloud fallback, no telemetry. Drop
documents, see beforehand what the result will look like, decide the few
uncertain spots, save a protected copy next to the original – and later read an
AI's answer again with the real names.

**Documents never leave the device; a one-time, verified model download, no
network traffic afterwards.** The detection models are too large for the app
download. First-run setup downloads them once – only on click, only over HTTPS,
only from the chosen source – and checks every file against the SHA-256 in the
signed app. Without internet, a model package from a file installs the same,
equally checked files.

The app is German-first: the UI ships in German (the reference) and English, and
detection handles German and English documents, with numbers for Germany and
Austria. The code and its documentation are in English.

## What it does

- **History and drop zone** for files and whole folders, and a text mode that
  masks live.
- **Detection in four stages:** your name lists, deterministic recognizers,
  semantic NER (GLiNER) and a suspicion check, plus an optional local second
  opinion for uncertain spots.
- **Review** with the document as it will be saved (page images, Word, Excel),
  a few numbered questions instead of long hit lists, and a fixed rule: when in
  doubt, mask.
- **Saving as a commit:** a protected copy next to the original, a content-free
  review report, and a translation table that is written only when you save.
- **Restoring:** paste or drop an AI's answer and get the real names back, even
  days later and with codes from all earlier entries.
- **Fail-closed:** if the detection service is down, dropping and saving are
  blocked; nothing is ever saved from a failed job.

The full behavior is in [docs/spec.md](docs/spec.md).

**Status:** the local end-to-end flow is implemented. What is still open –
signing, model hosting and three external acceptance proofs – is in
[docs/roadmap.md](docs/roadmap.md).

## Quick start

Prerequisites: Node ≥ 20, Rust ≥ 1.77, [uv](https://docs.astral.sh/uv/), and for
scans and photos a text recognizer (Apple Vision through the helper in
`vision-ocr/` on the Mac, Tesseract on Windows).

```bash
swift build -c release --package-path vision-ocr   # macOS: the Vision helper
cd sidecar && uv sync                              # Python environment and Presidio
cd ../app  && npm install                          # UI
npm run tauri:dev                                  # start the app
```

Then run all checks with `node scripts/check.mjs`. Details, model setup for
development and building signed packages are in
[docs/development.md](docs/development.md).

## Repository map

```text
/app            Tauri 2 shell (app/src-tauri, Rust) + SvelteKit 3 UI (app/src)
/sidecar        Python 3.12, FastAPI, Presidio: detection, masking, vault, export
/vision-ocr     Swift helper: text recognition with Apple Vision (macOS)
/download-xpc   Swift XPC service: the only part of the Mac package that downloads
/office-engine  Rust helper: DOCX → PDF for the optional page-faithful view
/profiles       YAML profiles, the source of the example templates
/master-data    customer name lists — personal data, never in the repository
/stopwords      word lists for the stopword filter and the suspicion check
/acceptance     machine-readable acceptance evidence (spec-11.json) and measurements
/testdata       test corpora and reference documents
/scripts        build, bundle, check and measurement scripts
/docs           specification, architecture, development, detection, glossary
/unname-logo    logo and app icons
```

## Documentation

| Document | Contents |
|---|---|
| [docs/spec.md](docs/spec.md) | the behavioral contract and the acceptance criteria |
| [docs/architecture.md](docs/architecture.md) | app ↔ shell ↔ sidecar, transport, vault, drafts, layout viewers |
| [docs/development.md](docs/development.md) | setup, checks, test layers, building and signing |
| [docs/detection.md](docs/detection.md) | the pipeline, profiles, master data, stopwords, second opinion, measuring quality |
| [docs/acceptance.md](docs/acceptance.md) | how the criteria are proven, and the measurements |
| [docs/roadmap.md](docs/roadmap.md) | what is still open |
| [docs/glossary.md](docs/glossary.md) | German terms and the English names code uses for them |
| [AGENTS.md](AGENTS.md) | conventions for language, commits and comments (for people and AI agents) |

## Origin and license

This application builds on **Presidio**. The project moved from Microsoft to the
independent organization **Data Privacy Stack**
(`github.com/data-privacy-stack/presidio`, documentation at
`presidio.dataprivacystack.org`). The PyPI package names `presidio-analyzer` and
`presidio-anonymizer` are unchanged; containers now come from
`ghcr.io/data-privacy-stack/`, and the old MCR images are deprecated. Presidio
is licensed under the MIT license.

This application is too: [LICENSE](LICENSE) (MIT).

The bundled fonts (Instrument Sans, Instrument Serif, Newsreader, JetBrains
Mono) are licensed under the SIL Open Font License 1.1 and may be redistributed
— a precondition for putting them into a signed installer. The license texts are
in [THIRD_PARTY_NOTICES/fonts/](THIRD_PARTY_NOTICES/fonts/).
