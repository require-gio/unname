# Development

How to set up, run, test and package unname. For what the app does, see
[spec.md](spec.md); for how it is built, [architecture.md](architecture.md).
Conventions for language, commits and comments are in [AGENTS.md](../AGENTS.md).

## Prerequisites

- Node ≥ 20 and Rust ≥ 1.77
- [uv](https://docs.astral.sh/uv/) for the Python environment
- Text recognition for scans and photos:
  - **macOS:** Apple Vision through the helper in [vision-ocr/](../vision-ocr)
    (build once; needs the Xcode command line tools)
  - **Windows:** [Tesseract](https://tesseract-ocr.github.io/) with the languages
    `deu` and `eng` (`winget install UB-Mannheim.TesseractOCR`, or build it
    yourself, see below)

## Setup and running

```bash
swift build -c release --package-path vision-ocr   # macOS: the Vision helper
swift test --package-path download-xpc             # macOS: the download service, optional
cd sidecar && uv sync                              # Python environment and Presidio
cd ../app  && npm install                          # UI
npm run tauri:dev                                  # start the app
```

In development the shell starts the sidecar from `sidecar/.venv`. A distribution
build uses only the bundled binary; if it is missing from a release, the app
stays fail-closed.

Outside an app bundle – that is with `npm run tauri:dev` – the shell downloads
models itself, because libxpc finds the download service only in the bundle of
the calling program. `scripts/download-xpc-e2e.sh` checks the service end to end
in a throwaway bundle.

### Text recognition on the Mac

The sidecar finds the helper under `vision-ocr/.build/release/unname-vision-ocr`
or through `PRESIDIO_VISION_OCR`; in the package only `assets/vision-ocr/` counts.
The very first call per helper and macOS build compiles Vision for the Neural
Engine (≈ 50 s), so the sidecar warms up at start. For A/B measurements,
`PRESIDIO_OCR_ENGINE=tesseract` selects Tesseract in development (never in a
frozen build). Quality is measured by `scripts/quality/ocr_gate.py` against a
baseline per engine (`testdata/quality/baseline.ocr.*.json`, report in
`acceptance/ocr-mac.json`):

```bash
uv run --project sidecar python -m scripts.quality.ocr_gate run --engine apple-vision --output /tmp/ocr.json
uv run --project sidecar python -m scripts.quality.ocr_gate check --report /tmp/ocr.json
```

On Windows the release package brings Tesseract along. Development uses the
self-built one from `.bundle-inputs/tesseract` first (see "Building
distributions"), otherwise the one on `PATH`. The language data comes from the
system or, once downloaded under Models & storage, from the "text recognition"
component (which takes precedence; with `PRESIDIO_MODELS_STRICT=1` only it
counts). If either is missing, the sidecar reports a warning under `model_load`
at start, and a scan is rejected with `ocr_unavailable` – also a PDF of which
only one page is a scan. Reading only the other pages would mean silently
leaving the scanned page unprotected.

### Models in development

The package contains no models. The shell downloads them during first-run setup
(`/setup`) or under Settings › Models & storage and stores them under
`<app data>/models` (`app/src-tauri/src/models.rs`):

- **Manifest:** `app/src-tauri/models.lock.json` is built into the signed app
  with `include_str!` – component, files, size, SHA-256, addresses per source and
  the allowed hosts. Create it with `scripts/models/modelle.py manifest`.
- **Components:** name detection (GLiNER multi-PII as ONNX, only the embeddings
  int8, 0.6 GB), the language packs German and English (the small spaCy models
  `de_core_news_sm` / `en_core_web_sm` 3.8.0, about 14 MB each, as wheels; only
  the files of the data folder are unpacked, each with its own hash) and the
  language data of text recognition (`deu`/`eng` from `tessdata_best`).
  Tesseract itself stays in the package as a program.
- **Download:** `reqwest` with `rustls` only in `models.rs`, only HTTPS, only to
  the hosts of the source (also on redirects), resumable, size and SHA-256 per
  file. A deviating file is discarded; a component is installed only once it is
  fully verified, by atomic rename. The HTTP client lives only as long as the
  download.
- **Gate:** until name detection and a language pack are ready, the sidecar
  reports `setup_required` and the shell keeps `can_upload` blocked.
- **Without internet:** `scripts/models/modelle.py paket` packs a zip
  (`<component>/<file>`); "Install model package from file" checks it against
  the same manifest.

The conversion to ONNX is done by `scripts/models/gliner-onnx.py` from the local
snapshot (safetensors only, never `pytorch_model.bin`); it compares the hits with
PyTorch. It is the only thing that needs PyTorch and `gliner` (`--group
modellbau`); at runtime the sidecar computes name detection with its own runner
(`detection/gliner_onnx.py`) using only `tokenizers` and `onnxruntime`, always on
the CPU.

The sidecar itself never loads a model from the network: it reads only files from
the shell's model folder, the package or `sidecar/models/gliner-onnx`. If the model
is missing, the spaCy model `de_core_news_lg` takes over on the CPU.

There are two ways to get the model for development. Simple: download the
verified files from `app/src-tauri/models.lock.json` (component `ner`) to
`sidecar/models/gliner-onnx`. Or create it yourself: place the PyTorch snapshot
once, deliberately – this network call happens outside the application – and
convert it:

```bash
cd sidecar
uv run --group modellbau python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    "urchade/gliner_multi_pii-v1",
    revision="f334163a5d1dcdbacf365c4527538e3da3ecfb18",
    local_dir="models/gliner",
    ignore_patterns=["*.onnx", "*.msgpack", "*.h5"],
)
PY
cd .. && uv run --project sidecar --group modellbau --with onnx \
    python scripts/models/gliner-onnx.py \
    --snapshot sidecar/models/gliner --output sidecar/models/gliner-onnx
```

`sidecar/models/gliner-onnx` is the fixed location of the development
environment and is in `.gitignore`. A different path goes through
`PRESIDIO_GLINER_MODEL_DIR`. In a frozen build both are ignored. Models verified
and installed by the shell (`--models-dir`) always take precedence.
`PRESIDIO_MODELS_STRICT=1` hides the development models, so first-run setup can
be played through in development too.

`GET /health` reports under `nlp_engine.device` the device the active engine
computes on; that is always `cpu`.

## Checks

```bash
node scripts/check.mjs           # all automated checks
node scripts/check.mjs --fast # without the live gate, builds, browser, process and Rust tests
cd app && npm run test:smoke     # flows, axe per board in light/dark, reference images, offline
```

The test layers:

| Layer | Where | Command |
|---|---|---|
| Sidecar unit and integration tests, Ruff | `sidecar/tests` | `cd sidecar && uv run pytest -q && uv run ruff check` |
| UI unit tests | `app/src/**/*.spec.ts` | `cd app && npm test` |
| Type check, lint, design tokens | `app` | `npm run check`, `npm run lint` |
| Browser smoke tests, axe, reference images, offline start | `app/tests/smoke` | `cd app && npm run test:smoke` |
| Shell tests | `app/src-tauri` | `cd app/src-tauri && cargo test` (also `cargo fmt --check`, `cargo clippy -D warnings`) |
| Swift helpers | `vision-ocr`, `download-xpc` | `swift test --package-path <dir>` |
| Detection quality gate | `testdata/quality`, `scripts/quality` | part of `check.mjs`; see [testdata/quality/README.md](../testdata/quality/README.md) |
| Acceptance manifest | `acceptance/spec-11.json` | `sidecar/tests/test_acceptance_manifest.py`; see [acceptance.md](acceptance.md) |

`npm run test:smoke:update` regenerates the reference images; renaming a test
should rename its PNGs with `git mv` instead.

API types are generated: `node scripts/api-types.mjs` writes
`app/src/lib/sidecar/api.schema.json` and `api.gen.ts` from `api_models.py`;
`--check` fails on a deviation.

### A fresh start

`node scripts/reset-state.mjs --yes` deletes app data, caches, logs and WebView
data of the app – under the new identifier `org.unname.desktop` and the old
`org.presidio-ui.desktop`. Without `--yes` it only shows what would be deleted.
History, translation table and key are lost. Use it to test first-run setup.

### Measurements

- **Round trip of the code format through language models:**
  `scripts/llm-round-trip.py` measures against locally running Ollama models –
  only `127.0.0.1`; cloud models are rejected. The texts are invented and contain
  only codes; the result is in `acceptance/llm-round-trip.json`:

  ```bash
  uv run --project sidecar python scripts/llm-round-trip.py \
    --models qwen3:4b-instruct-2507-q4_K_M gpt-oss:20b --repeats 2 \
    --output acceptance/llm-round-trip.json
  ```

- **Performance with the real models** (50-page PDF, 9 files, 1,000 codes):
  `uv run --project sidecar python scripts/performance.py`; the result – times,
  counts and device data only – is in `acceptance/performance.json`.
- **Detection quality:** see [detection.md](detection.md#measuring-quality).

## Building distributions

On macOS, `build-distribution.mjs` also builds the download service
(`scripts/build-download-xpc.mjs`: `swift build`, bundle
`org.unname.desktop.download.xpc`, signed with `download-xpc/Entitlements.plist`
and – in a release – the Developer ID) into `app/src-tauri/binaries/`; Tauri puts
it into `Contents/XPCServices`.

The asset step deliberately downloads nothing. For macOS (`*-apple-darwin`) it
builds the Vision helper itself (`swift build`, or a prebuilt one through
`--vision-ocr`) and puts it into the package as the `binary` asset
`vision-ocr/unname-vision-ocr` – without Tesseract, tessdata and their licenses;
the trust entry then names only the `target`. It bundles **no models**: for
Windows it expects only a portable Tesseract with native libraries, whose source
tree hash must be entered for the target platform in the reviewed
`sidecar/packaging/bundle-inputs.lock.json` (an entry with `target` and
`tesseract_tree_sha256`); a mere revision name or artifact run does not count as
proof of trust. With `--with-models` (or `PRESIDIO_BUNDLE_MODELS=1`) it builds
the package with the GLiNER snapshot, `de_core_news_lg` and `deu`/`eng` from a
pinned `tessdata_best` commit; then all three source tree hashes belong in the
lock. The workflow `package-smoke.yml` builds this way because its smoke test
should detect without first-run setup.

`scripts/tesseract-portable.py` builds the portable Tesseract with vcpkg from a
fixed commit, statically against Leptonica and the image libraries. Afterwards it
needs only what macOS or Windows ships. Before reporting success, the script
checks that and has Tesseract read a sample text without `PATH`. It puts the
license texts of the linked libraries into `tesseract/licenses/`, and the asset
step takes them into the bundle. In CI the workflow `bundle-inputs.yml` (started
by hand) does this. It stores the artifacts that `package-smoke.yml` loads through
`BUNDLE_INPUTS_RUN_ID`, and writes the trust entries for the lock into its
summary. Locally on the target system:

```bash
uv run --no-project --with pillow==12.3.0 python scripts/tesseract-portable.py \
  --target aarch64-apple-darwin --out .bundle-inputs
```

```bash
export PRESIDIO_TESSERACT_ROOT=/absolute/path/to/tesseract
export PRESIDIO_ASSET_TRUST_LOCK="$PWD/sidecar/packaging/bundle-inputs.lock.json"
export PRESIDIO_ASSET_TRUST_ID=<windows-x64-or-macos-arm64>

node scripts/build-sidecar.mjs --target <host-triple>
node scripts/build-distribution.mjs --target <host-triple> --unsigned # CI smoke
node scripts/build-distribution.mjs --target <host-triple>            # release
```

After checking the origin,
`python scripts/prepare-sidecar-assets.py --target <triple> --trust-asset-id <id> --print-input-trust`
computes the lock entry without running Tesseract. Only the separately reviewed
entry may go into the trust lock.

### Signing

A release builds only host-native and aborts without signing data. Windows
expects an installed code-signing certificate through `WINDOWS_CERTIFICATE_SHA1`
and an HTTPS `WINDOWS_TIMESTAMP_URL`. macOS expects `APPLE_SIGNING_IDENTITY` and
either Apple ID or App Store Connect notarization data. The macOS sidecar is
signed beforehand with its inheritance entitlements and then placed in the app as
an already signed helper, so Tauri does not overwrite that signature. Without a
Developer ID, `node scripts/build-distribution.mjs --adhoc` builds an ad hoc
signed package with the real sandbox.

### CI

- `build-guard.yml` – builds with the guard against external assets.
- `quality-gate.yml` – the detection quality gate, the full sidecar suite with
  Ruff, and the Rust suite with `fmt` and warning-free Clippy.
- `bundle-inputs.yml` – builds the portable Tesseract and the bundle inputs (by
  hand), and publishes the Windows Tesseract as the release
  `bundle-inputs-<run id>` for `release.yml`.
- `release-pr.yml` and `release.yml` – the release PR from main into `release`
  and the pipeline its merge starts: version from the PR label, ad hoc signed
  DMG and unsigned Windows installers, built and started on hosted runners, then
  tag and draft release. See [releasing.md](releasing.md).
- `models-mirror.yml` – fills the GitHub release `models-v1` that
  `models.lock.json` lists as the second model source (by hand).
- `package-smoke.yml` – builds and starts the actual packages, by hand, and
  keeps the signed and notarized release path. The Windows 11
  acceptance runs only on a self-hosted x64 runner with the labels `windows-11`
  and `minimum-hardware` and checks exactly two logical cores and 8 GB RAM there.
  Large, pinned bundle inputs are provided as separate GitHub artifacts through
  `BUNDLE_INPUTS_RUN_ID`; models or certificates are never in the repository.
  The production hashes are not entered yet because the large external inputs are
  not available here; packaging stays fail-closed until that explicit, reviewable
  entry. `--verify-input-trust-only` checks an entered set without running any of
  its binaries.

## What the current state guarantees

Where each guarantee is implemented and where it is tested. The sections of
[spec.md](spec.md) give the reasons.

| Guarantee | Implemented in | Tested in |
|---|---|---|
| The sidecar binds to loopback only | [config.py](../sidecar/src/presidio_sidecar/config.py) | `tests/test_config.py`, `tests/test_process.py` |
| Random port, handed over on stdout | [\_\_main\_\_.py](../sidecar/src/presidio_sidecar/__main__.py) | `tests/test_process.py` |
| A bearer token per app start, every endpoint protected | [security.py](../sidecar/src/presidio_sidecar/security.py) | `tests/test_security.py` |
| CORS only for the Tauri origin | [app.py](../sidecar/src/presidio_sidecar/app.py) | — |
| Watchdog: process end **and** hung process | [sidecar.rs](../app/src-tauri/src/sidecar.rs) | by hand (see [roadmap.md](roadmap.md)) |
| Failure ⇒ no findings before the end, no partial result saved | [jobs.py](../sidecar/src/presidio_sidecar/jobs.py), [saving.rs](../app/src-tauri/src/saving.rs) | `test_jobs.py`, `test_analyze_api.py`, `review.spec.ts` |
| Dropping, saving and copying blocked without the sidecar; reading stays | [sidecar.rs](../app/src-tauri/src/sidecar.rs), [gate.svelte.ts](../app/src/lib/sidecar/gate.svelte.ts) | `cargo test`, `gate.spec.ts`, `review.spec.ts` |
| Diagnostics report from both sources | [diagnostics.rs](../app/src-tauri/src/diagnostics.rs), [diagnostics.py](../sidecar/src/presidio_sidecar/diagnostics.py) | `tests/test_handshake.py` |
| The CSP forbids external fonts and foreign targets | [tauri.conf.json](../app/src-tauri/tauri.conf.json) | — |
| The build fails on an external resource | [scripts/check-no-external-assets.mjs](../scripts/check-no-external-assets.mjs) | part of `npm run build:guarded` |
| Every bundled font is licensed byte for byte | [THIRD_PARTY_NOTICES/fonts/manifest.json](../THIRD_PARTY_NOTICES/fonts/manifest.json) | `npm run test:smoke` |
| The frozen sidecar uses only manifested local models and OCR data | [resources.py](../sidecar/src/presidio_sidecar/resources.py) | `test_bundle_resources.py`, `test_packaging_assets.py` |
| Models come only on click, only over HTTPS from the hosts of the chosen source; every file is checked against the SHA-256 in the signed app and a deviating one discarded; without name detection and a language, dropping stays blocked | [models.rs](../app/src-tauri/src/models.rs), [models.lock.json](../app/src-tauri/models.lock.json), [sidecar.rs](../app/src-tauri/src/sidecar.rs), [resources.py](../sidecar/src/presidio_sidecar/resources.py) | `cargo test`, `test_models.py`, `test_model_manifest.py`, `setup.spec.ts` |
| Saving is collision-free next to the originals, with a security scope on macOS; job data stays out of backups | [saving.rs](../app/src-tauri/src/saving.rs), [platform_security.rs](../app/src-tauri/src/platform_security.rs) | `cargo test`, `test_save_api.py` |
| A recall regression gate per starter profile and master data condition | [quality-gate.yml](../.github/workflows/quality-gate.yml), [gate.py](../scripts/quality/gate.py) | `node scripts/check.mjs` |
| History entries carry a random, immutable short reference; migrating the history schema neither extends a retention period nor writes non-atomically | [entries.py](../sidecar/src/presidio_sidecar/entries.py) | `test_entry_references.py`, `test_api_v2.py`, `history.spec.ts` |
| Acceptance criteria are traceable to concrete tests; manifest, tests and specification stay identical | [spec-11.json](../acceptance/spec-11.json) | `test_acceptance_manifest.py` |
| Text reaches 4.5:1 in light and dark; axe (WCAG 2.1 AA) with no findings on every board; focus order follows the picture | [tokens.css](../app/src/lib/styles/tokens.css), [ReviewView.svelte](../app/src/lib/features/review/ReviewView.svelte) | `contrast.spec.ts`, `npm run test:smoke` |
| Person and company master data stay separate and encrypted | [registry.py](../sidecar/src/presidio_sidecar/registry.py) | `test_registry.py`, `test_registry_crypto.py` |
| Rule, NER and suspicion hits stay separated by origin | [analysis.py](../sidecar/src/presidio_sidecar/analysis.py) | `test_analysis.py`, `test_analyze_api.py` |
| Long texts are split with overlap for GLiNER and spaCy | [ner.py](../sidecar/src/presidio_sidecar/detection/ner.py) | `test_detection_ner.py` |
| API offsets are explicitly UTF-16-compatible with the WebView | [api_common.py](../sidecar/src/presidio_sidecar/api_common.py) | `test_analyze_api.py`, `test_performance.py` |
| The vault secret and the encrypted mapping live privately outside the project | [vault.py](../sidecar/src/presidio_sidecar/vault.py) | `test_vault.py`, `test_services.py` |
| Five styles, categories instead of levels, locked account data; the level is only derived | [masking.py](../sidecar/src/presidio_sidecar/masking.py), [catalog.py](../sidecar/src/presidio_sidecar/catalog.py), [processing.py](../sidecar/src/presidio_sidecar/processing.py) | `test_api_v2.py`, `test_documents_v2.py`, `test_processing.py` |
| Mappings arise only on saving or copying; codes can be restored after a restart | [workflow.py](../sidecar/src/presidio_sidecar/workflow.py), [vault.py](../sidecar/src/presidio_sidecar/vault.py) | `test_api_v2.py`, `test_save_api.py`, `test_documents_v2.py` |
| AI answers (text, Word, Excel, Markdown) are restored in memory only; changed spellings only if unambiguous, otherwise reported as unknown; the AI's file is never overwritten | [restore.py](../sidecar/src/presidio_sidecar/restore.py), [restoring.rs](../app/src-tauri/src/restoring.rs) | `test_restore.py`, `restore.spec.ts`, `cargo test` |
| The backup of the translation table exists only with a password; name lists, terms and patterns are stored encrypted; a custom pattern with catastrophic backtracking or too broad hits is rejected | [vault_backup.py](../sidecar/src/presidio_sidecar/vault_backup.py), [vocabulary.py](../sidecar/src/presidio_sidecar/vocabulary.py) | `test_settings.py` |
| The residual-risk heuristic reports from two classes without blocking saving; open questions are masked on saving | [residual_risk.py](../sidecar/src/presidio_sidecar/residual_risk.py), [workflow.py](../sidecar/src/presidio_sidecar/workflow.py) | `test_residual_risk.py`, `test_api_v2.py` |
| "Anonymous" is assigned only by the sidecar under a rule; the word appears in no Svelte file | [masking.py](../sidecar/src/presidio_sidecar/masking.py), [result-policy.ts](../app/src/lib/sidecar/result-policy.ts) | `test_api_v2.py`, `result-policy.spec.ts` |
| CUDA/MPS only after a test inference; fallbacks stay visible in the diagnostics report | [hardware.py](../sidecar/src/presidio_sidecar/hardware.py) | `test_hardware.py` |
| Document and OCR parallelism follows the core/RAM matrix | [workers.py](../sidecar/src/presidio_sidecar/workers.py), [ocr.py](../sidecar/src/presidio_sidecar/extraction/ocr.py) | `test_workers.py`, `test_ocr_limiter.py` |
| A 50-page PDF, 9 files and 1,000 codes stay usable; saving grows linearly with the number of spots | [document_workflow.py](../sidecar/src/presidio_sidecar/document_workflow.py) | `test_performance.py`, `review.spec.ts`, `scripts/performance.py` |
| History titles never name a person; history, lists, terms and patterns are stored encrypted | [titles.py](../sidecar/src/presidio_sidecar/titles.py), [entries.py](../sidecar/src/presidio_sidecar/entries.py) | `test_api_v2.py` |
| The UI is fully in German and English | [de.ts](../app/src/lib/i18n/de.ts), [en.ts](../app/src/lib/i18n/en.ts) | `translate.spec.ts` |

### Fail-closed, the principle behind it

The most dangerous state is a filter that silently stops filtering. So the
initial state is **blocked** everywhere, and only a positive reply opens the UI —
not the absence of an error. `can_upload` and `can_export` are derived only in the
Rust shell; the UI reads them and formulates no rule of its own.

### Checks by hand

Two further acceptance checks cannot sensibly be automated:

1. **The lock is visible:** start the app, review an entry, `kill -9` the sidecar
   process. The window must show the message "Currently unavailable", dropping and
   saving must be blocked, the document must stay readable and the restart must
   appear in the diagnostics report.
2. **No network traffic:** a capture of the running app, for example
   `sudo tcpdump -i any -n "not host 127.0.0.1 and not host ::1"`. Outside a model
   download the user started, no outgoing call may appear; during the download
   only connections to the hosts of the chosen source.
