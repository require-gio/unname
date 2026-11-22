# Sidecar

The local Python service for PII detection. It is the only process that sees
document content. How it fits into the app is described in
[docs/architecture.md](../docs/architecture.md).

## Fixed guarantees

| Guarantee | Implementation |
|---|---|
| Binds to loopback only | `config.py` rejects any non-loopback address at start |
| Random port | `--port 0`; the port is only known after `bind()` |
| No port on the Mac | `--uds PATH`: a Unix domain socket with 0600 in the private folder the shell specifies; path length, owner and permissions of the folder are checked before binding, otherwise exit 2 |
| Bearer token per app start | `secrets.token_urlsafe(32)`, never written to disk |
| Every endpoint protected | middleware instead of `Depends` per route |
| CORS only for the Tauri origin | `DEFAULT_ALLOWED_ORIGINS` in `config.py` |
| No orphaned process | a stdin watcher ends the service when the shell goes away |
| No runtime downloads | GLiNER loads strictly `local_files_only`; email TLDs come from the package snapshot |
| Master data protected | separate lists, AES-256-GCM image, private key and atomic re-import |
| Working state encrypted | one key per entry, wrapped with a master key (`drafts.py`); deleting removes the key atomically |
| Token vault private | a 256-bit secret and an AES-256-GCM-encrypted SQLite image outside the project; restrictive file permissions |
| Processing fail-closed | unknown entity types are not silently released; result facts drive the UI and the save dialog |
| Document export stays content-free | PDFs are truly redacted, rendered, checked by text and, for OCR pages, checked visually again; Office side channels are removed or rejected in a controlled way |
| GPU stays optional | CUDA/MPS counts as active only after a local test inference; every fallback appears in the diagnostics report and falls back to CPU without loss of function |

## Protocol on stdout

`stdout` belongs exclusively to the protocol — exactly one JSON line per event.
Log output goes to `stderr`.

```json
{"event":"ready","protocol":2,"port":51234,"token":"…","pid":4711,"version":"0.1.0"}
{"event":"ready","protocol":2,"socket":"…/unname-AbC123/s","token":"…","pid":4711,"version":"0.1.0"}
{"event":"diagnostic","protocol":2,"category":"startup","severity":"info","message":"…","details":{},"timestamp":"…"}
{"event":"stopping","protocol":2,"reason":"shutdown"}
```

The ready message names exactly one of `port` (TCP, Windows) and `socket`
(`--uds`, macOS). For the socket, the shell accepts only exactly the path it
specified.

## Endpoints

The HTTP contract is generated from `api_models.py`:
`node scripts/api-types.mjs` writes `app/src/lib/sidecar/api.schema.json` and
`api.gen.ts`; `--check` and `tests/test_api_schema.py` fail on a deviation.

The UI does not talk about profiles, protection levels and modes but about a
**masking spec** (`MaskingSpec`: method, style, categories, derived `anonymize`).

| Endpoint | Content |
|---|---|
| `GET /health` | version, protocol, package versions, NLP engine state, device choice, `models` (per component `ready`/`missing` and origin) and `setup_required` |
| `GET /diagnostics` | the diagnostics report of this session — operating data only, never document content |
| `GET /catalog` | categories ↔ types, labels de/en, default, locked, styles per method, placeholders per language (`catalog.py`) |
| `GET` / `PUT /settings` | settings with a schema version; `PUT` takes over only the named sections (`settings_store.py`) |
| `GET` / `POST` / `DELETE /entries` | read the history · create an entry · clear the history (`?confirm=true`) |
| `GET` / `PATCH` / `DELETE /entries/{id}` | read an entry · rename, masking, state · delete (entry, key, drafts and memory in one path) |
| `GET /storage` | space used by drafts per entry and in total |
| `GET` / `PUT /entries/{id}/view` | the per-entry view state (active file, view, page, anchor) |
| `POST /entries/{id}/files` | several files as multipart `file`, one job per file, extraction in the worker; rejections per file. The shell reads the bytes — the sidecar accepts no paths. The original is stored encrypted before the sidecar answers `202` |
| `GET /entries/{id}/progress` | per file the phase, progress and a running count per category |
| `POST /entries/{id}/occurrences` | the same character sequence (whole word) in all files of the entry |
| `POST /entries/{id}/files/{file_id}/replace` | read the original in again ("Choose original" for legacy entries or unreadable drafts); the review state follows, `changed` reports a different hash |
| `POST /entries/{id}/files/{file_id}/retry` | retry a failed file from its stored original, without a file dialog |
| `GET` / `POST /entries/{id}/redetect` | whether detection no longer matches the settings · "Detect again" from the saved copy |
| `DELETE /entries/{id}/files/{file_id}` | remove a file from the entry |
| `GET /jobs/{id}/findings` | source text plus confirmed and suspected hits with UTF-16 offsets and origin |
| `POST /jobs/{id}/apply` | dry run for a `{ masking, review }` body: computes codes and surrogates but writes nothing to the translation table |
| `GET /jobs/{id}/layout`, `/layout/paged`, `/layout/document`, `/layout/sheets/{sheet}` | the layout map (word boxes, anchors, cells) per format |
| `GET /jobs/{id}/pages/{page}` · `…/pages/{page}/result` | page images and the control image of the result |
| `POST /text/mask` | detect and mask text without saving anything |
| `POST /text/commit` | "Copy": write the mappings, a history entry "Text · copied", a content-free audit (hash of the copied text) |
| `POST /entries/{id}/save` | "Save all n files": recompute the final state per file (open questions always masked), export into a private staging folder (PDFs with a render check), then commit the mappings and audit each file. Irreversible masking needs `confirm_irreversible` once per entry. Files without a spot get no copy (`unchanged`) |
| `POST /entries/{id}/outputs` | the shell reports the published paths; only now does the entry count as `saved` |
| `GET /entries/{id}/report` | facts for the review report: spec, counts per file and in total, origin of decisions, check data — never content |
| `POST /restore/text` | restore an AI answer, in memory only: tolerant of brackets, case and spaces, spans per spot with origin (`here` / `entry` / `earlier`), count including unknown codes. `record` counts a restore on the entry (`restores`) and notes `last_used_at` of the codes (`restore.py`) |
| `POST /restore/file` | an AI's file (DOCX, XLSX, Markdown, text) as multipart `file`, `?entry_id=` and `?surrogates=`: codes replaced run- or cell-exactly, formatting stays; a preview with spans. The sidecar keeps the new file `<name>_mit_Namen.<ext>` (`_with_names` in English) in memory only (30 min, at most 8) |
| `GET` / `DELETE /restore/file/{id}` | the shell fetches the bytes for "Save as …" (`X-Content-SHA256`; counts once as a restore) · "Remove" |
| `GET /vault/status` | key figures of the translation table: mappings, entries with mappings (`entry_count`), key date — never the secret or plain values |
| `POST /vault/rotate` | confirmed key change; earlier mappings become unusable |
| `POST /vault/clear` | "Clear table …" only with `{"confirm": true}`; the key stays, so the same name still gets the same code |
| `POST /vault/backup` | backup of table **and** key, encrypted with a password (scrypt + AES-256-GCM, `vault_backup.py`); the response is `application/octet-stream`, the shell writes it to the chosen place |
| `POST /vault/import` | read a backup (Base64) with its password and `confirm`: replaces key and table of this device; only after a complete check |
| `GET /lists` · `POST /lists?kind=` · `POST /lists/{id}/refresh` · `DELETE /lists/{id}` | name lists (persons, companies) from CSV, JSON or text as multipart `file` from the shell; a date per list, `stale` after 7 days (`vocabulary.py`) |
| `GET` / `PUT /terms` | custom terms with a kind (name, company, place, customer number, case number, other) |
| `GET` / `PUT /patterns` · `POST /patterns/test` | custom patterns with the sidecar's regex engine: rejected are patterns that match nothing, match half of an ordinary sentence or exceed the time limit on sample texts; "Try out" returns hits with a placeholder without saving |
| `GET` / `POST /templates` · `POST /templates/{id}/apply` · `POST /templates/export` · `POST /templates/import` | templates (YAML, format v4, `templates.py`): terms, patterns, optionally a masking default; the three profiles and "Insurance" as examples. Name lists never belong in a template |
| `GET /models` | state per component (`ner`, `de`, `en`, `ocr`, optionally `classifier`): ready or missing, origin (`download` from the shell's model folder, `bundle`, `local`), size, `setup_required` (the classifier does not count towards it) |

Fixed rules:

- **Apply is a dry run.** `POST /jobs/{id}/apply` with `{ masking, review }`
  computes codes and surrogates but writes nothing to the translation table
  (`TokenVault.prepare_*`). Only saving or copying calls `TokenVault.commit` and
  notes the entry per code (the origin for restoring).
- **When in doubt, mask.** `review.unresolved` defaults to `mask`: open questions
  (a suspicion or an uncertain hit) are replaced and counted in the report as
  `unresolved_masked_count`. `keep` is only for a preview.
- **Detection always runs with all types** through the internal base profile
  (`base_profile.py`); the spec decides which types are replaced.
- **Label** (`labels.label`): `reversible`, `irreversible` or `anonymous`;
  `anonymous_blockers` names what prevents "anonymous".
- **Saving is the commit.** `POST /entries/{id}/save` writes only into a staging
  folder under the job root (in the shell's temp area) and accepts no path. The
  shell checks location, size and SHA-256 and publishes without collisions
  (`… (2)`); if one file fails, nothing is created. The name of the copy follows
  the app language (`_geschützt`/`_protected`); if OCR cannot read the codes
  back from a scan or an image, the copy becomes a text file (`readback_failed`).
- **Restoring does not guess.** A spelling is recognized only if it leads without
  doubt to exactly one code (`<PERSON_…>`, `«PERSON_…»`, `[PERSON_…]`,
  `<< person_… >>`, without brackets only with a known type). Another type name or
  a damaged check value is reported as unknown, never guessed. Spots that cannot
  be replaced safely in a file (fields, change tracking, runs with images, sheet
  names) are counted as `unreplaced`. Excel files with charts or images are
  rejected because openpyxl does not write them back.
- **Audit** (`audit.py`): one record per saved file or copied text with the masking
  spec, entry ID and decisions by origin (user, AI suggestion, "masked without a
  decision") — still without values, excerpts or paths.

### Manual corrections in review

`manual_findings` adds spots that detection missed (spec 9.3). Each entry names
`entity_type`, `start`, `end` in UTF-16 code units and optionally its own
`mode`:

```json
{
  "mode": "remove",
  "decisions": [{ "finding_id": "f_…", "action": "reject" }],
  "manual_findings": [{ "entity_type": "ORGANIZATION", "start": 27, "end": 50 }]
}
```

Three properties are chosen deliberately:

- **A boundary correction is not a separate operation** but a pair of reject and
  re-add. That way "no two replacements at the same spot" remains the only rule
  to enforce. In the audit log it still appears as `adjust_span` instead of
  `add_manual` — recognized by the overlap with the rejected hit.
- **A manual entry beats the protection level.** It is replaced even if
  `keep_plain` or the level would have left its type in plain text; silently
  discarding an explicit instruction would be the more dangerous path. The review
  report lists them separately under `manual_counts_by_entity`.
- **Only profile-configured types** are allowed — otherwise replacements would
  arise that no profile ever released.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
uv run presidio-sidecar --keep-running-without-parent
```

`--keep-running-without-parent` is only needed for manual operation: without the
flag, the service ends as soon as stdin closes.

By default the vault uses the operating system's private application data
directory. For isolated tests, `PRESIDIO_STATE_DIR` can be set to a directory
outside the project.

Entity thresholds can be set individually:

```bash
uv run presidio-sidecar --ner-threshold PERSON=0.35 \
  --ner-threshold ORGANIZATION=0.40 \
  --ner-threshold LOCATION=0.45
```

Alternatively `PRESIDIO_NER_THRESHOLDS` accepts a JSON object. Models are loaded
only on the first job. The sidecar looks for them first in the shell's model
folder (`--models-dir`): only a folder with `.installed.json` counts as installed,
and the shell writes that marker only after verified hashes. Name detection is
stored there as ONNX (`model.int8.onnx`) and runs on ONNX Runtime on the CPU,
with a runner of its own (`detection/gliner_onnx.py`) that needs no `gliner`,
PyTorch or transformers. It is preferred from 7 GiB RAM; below that spaCy takes
over. The sidecar no longer loads a PyTorch snapshot. If name detection or every
language pack is missing, `/health` reports `setup_required: true` and the
warm-up is skipped.

The sidecar determines the document language per paragraph
(`detection/language.py`, function words per language; only the languages
enabled in `/settings` › `document_languages`). The spaCy fallback uses the model
of the language pack (`de`, `en`) per paragraph. The NER post-filter and the
suspicion check use the business terms and sentence starters of the respective
language (`stopwords/<language>_*.txt`, without uniting the language lists). The
mapping and the default language live in `languages.py`; usage and extension are
described in [`stopwords/README.md`](../stopwords/README.md). Placeholders and
invented names follow the paragraph's language on request. The countries
(`countries`: DE, AT) select numbers, IDs and phone regions — independent of the
language (`languages.py`).

After the suspicion check comes the **second opinion** (`assessments.py`,
`detection/second_opinion.py`): a small classifier (ONNX int8, without PyTorch)
reads every question about names, companies and places with some context and
picks one of four fixed labels (`person`, `organization_or_product`, `location`,
`term`). The findings carry this as `assessment` (label, confidence, suggestion,
`decided`); what the AI decides itself according to the policy in `/settings` ›
`second_opinion` (`protect`: only toward protection from 80 %,
`protect_and_keep`: additionally keeping from the threshold, `suggest`: nothing)
no longer counts as an open question and counts in text mode like a decision with
the origin `ai-suggestion`. The model lives in the shell's model folder
(`classifier`) or, in development, under `sidecar/models/second-opinion` (or
`PRESIDIO_CLASSIFIER_MODEL_DIR`); if it is missing or fails, jobs continue
without an assessment. Training and measurement: `scripts/second_opinion/`.
GLiNER never loads weights from the network. In the PyInstaller binary,
Tesseract, profiles and word lists (with `--with-models` also GLiNER,
`de_core_news_lg` and `deu`/`eng`) are resolved from the hashed
`asset-manifest.json`. An incomplete frozen bundle aborts before the ready
handshake; environment variables cannot redirect its signed assets to external
paths.

### Building the frozen binary

`scripts/prepare-sidecar-assets.py` accepts only inputs that are already local
and versioned and writes `sidecar/bundle-assets/` atomically. Before running the
bundled Tesseract, it verifies Tesseract (with `--with-models` also GLiNER and
tessdata) byte for byte against `sidecar/packaging/bundle-inputs.lock.json`.
Missing production hashes are deliberately not an unsafe fallback but block the
package build. After that, `scripts/build-sidecar.mjs` builds the sidecar under
`app/src-tauri/binaries/`:

```bash
node scripts/build-sidecar.mjs --target aarch64-apple-darwin    # folder presidio-sidecar-aarch64-apple-darwin/
node scripts/build-sidecar.mjs --target x86_64-pc-windows-msvc  # presidio-sidecar-x86_64-pc-windows-msvc.exe
```

On macOS it is a onedir folder (executable `presidio-sidecar` plus `_internal/`),
which the app bundle carries in `Contents/Resources/presidio-sidecar`. A onefile
binary cannot run there: its bootloader coordinates with its unpacked child
through a System V semaphore, and the App Sandbox denies that. Windows keeps the
onefile binary that Tauri expects as `externalBin`.

PyInstaller cannot cross-compile; every command must run on the matching
platform and architecture. On macOS, `--require-signing` demands a Developer ID;
PyInstaller signs every collected Mach-O file with the hardened runtime and
`SidecarEntitlements.plist`. An ad hoc build (`--codesign-identity -`) uses
`SidecarEntitlements.adhoc.plist` instead, which adds
`com.apple.security.cs.disable-library-validation`: without a Team ID, library
validation would refuse every library of the folder.

### Hardware and device choice

PyTorch is no longer part of the sidecar, and name detection always runs on the
CPU. Without PyTorch, detection finds no GPU, and the device choice below applies
only in an environment with the `modellbau` group. Whether it is removed is an
open decision (see [docs/roadmap.md](../docs/roadmap.md)).

At sidecar start, core count, RAM and CUDA/MPS devices are captured once. A GPU
does not become active on the basis of an availability flag: a small neural test
inference must run on the requested device and return a valid result there. Before
the ready handshake, a probe with the GLiNER adapter actually used follows; a
model-related fallback corrects the same finding to CPU. Only this cached final
state drives `GET /health`, the NER adapter and the device line together.

`PRESIDIO_DEVICE` takes precedence over the automatic choice:

```bash
PRESIDIO_DEVICE=cpu uv run presidio-sidecar --keep-running-without-parent
PRESIDIO_DEVICE=mps uv run presidio-sidecar --keep-running-without-parent
PRESIDIO_DEVICE=cuda:0 uv run presidio-sidecar --keep-running-without-parent
```

Without an override, the matrix picks CUDA automatically from 8 GiB VRAM. Smaller
NVIDIA GPUs stay visible but need an explicit opt-in with `PRESIDIO_DEVICE=cuda:n`.
MPS is tried automatically on Apple Silicon from 32 GiB; on smaller devices it
stays optional. A failed device inference or a later model inference switches to
CPU without reducing the job path and creates a `gpu_fallback` or `mps_fallback`
entry.

`PYTORCH_ENABLE_MPS_FALLBACK=1` is allowed. The compatibility mode appears both
in the health finding and once in the diagnostics report; it is not confused with
a fallback that actually occurred.

The CPU document workers follow the core/RAM matrix (1 to 4). OCR stays
functional on the minimum configuration but runs serially there; stronger devices
get at most two parallel OCR slots.

A note on the threshold: the spaCy model delivers no calibrated entity
probability. The adapter therefore marks its hits with the documented proxy score
`0.50`; a type-specific threshold above `0.50` deliberately suppresses that type
in the fallback path.
