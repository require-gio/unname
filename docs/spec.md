# Specification: local desktop app for PII detection and document redaction

**Version:** 4.0
**Target platforms:** Windows 10/11 (x64), macOS Apple Silicon (arm64), optionally Intel Mac (x64, CPU only)

This is the behavioral contract of unname. It says what the app promises and
how those promises are measured. How the parts fit together is in
[architecture.md](architecture.md), how detection works in
[detection.md](detection.md), and how the acceptance criteria in section 11
are proven in [acceptance.md](acceptance.md). Terms follow
[glossary.md](glossary.md).

*This is not legal advice. Have a lawyer or the responsible chamber assess the
data protection side.*

---

## 1. Goal

unname processes documents entirely on the user's device. Users drop documents
or paste text, see exactly what the result will look like beforehand, decide
the few uncertain spots, and save a protected copy next to the original. For AI
workflows there is a way back: the answer of a language model is translated back
to plain text locally, even days later and with codes from all earlier entries.

The foundation is **Presidio**. The project moved from Microsoft to the
independent organization **Data Privacy Stack**
(`github.com/data-privacy-stack/presidio`, docs at
`presidio.dataprivacystack.org`). The PyPI package names `presidio-analyzer` and
`presidio-anonymizer` are unchanged; containers now come from
`ghcr.io/data-privacy-stack/`. The license is still MIT.

The architecture is **CPU-first and GPU-optional**. All core functions must run
without a dedicated GPU. A GPU only speeds up the NLP stage; regex, context and
checksum recognizers stay CPU work.

---

## 2. Architecture in brief

```text
Tauri desktop app "unname" (SvelteKit 3, static)
│
├─ UI: history, new (documents | text), detection running, review,
│  saved, restore, settings, first-run setup
├─ Shell (Rust): file inputs, saving into the target folder, model download
│  (the only network access, started by the user; on the Mac inside the
│  download service in Contents/XPCServices, with its own sandbox)
├─ Talks to the sidecar only through the shell: on macOS over a Unix domain
│  socket (0600) in the app container, on Windows over 127.0.0.1; the UI calls
│  the `unname-api` scheme, only the shell knows the token
│
└─ local Python sidecar
   ├─ FastAPI / Uvicorn on the Unix socket (macOS) or a random port (Windows), API v2
   ├─ Presidio Analyzer + Anonymizer
   ├─ name lists, custom terms and patterns        ← stage 0 of detection
   ├─ base profile, templates (YAML v4), category catalog
   ├─ translation table (HMAC + encrypted SQLite)
   ├─ history (encrypted), settings, drafts
   ├─ document extraction and OCR, language per paragraph
   ├─ second opinion (local classifier, optional)
   ├─ CPU worker pool, optional GPU NER worker
   └─ temporary, job-isolated storage
```

### Communication rules

- Bind to loopback only (`127.0.0.1`), never `0.0.0.0`.
- Random port, handed to the Tauri app over stdio.
- A bearer token per app start, never persisted.
- CORS only for the local Tauri origin.
- No remote URLs as input.

On macOS these rules are tighter:

- Instead of a port, a **Unix domain socket** (`--uds`, 0600) in a private
  folder (0700) that is new on every start, inside the app container. Before
  binding, the sidecar checks the path length (≤ 103 bytes) and the owner and
  permissions of the folder, and refuses to start otherwise. The ready message
  names the socket instead of the port; the shell accepts exactly the path it
  specified. The app therefore carries no `network.server` entitlement, and the
  inherited sidecar cannot listen on TCP under the sandbox at all.
- The UI does not talk to the sidecar itself but through the shell's
  **`unname-api`** scheme (`unname-api://localhost`, on Windows
  `http://unname-api.localhost`). The shell forwards method, path, query, body
  and a few headers and sets the token itself; the WebView knows neither token
  nor port.
- Windows stays with TCP on 127.0.0.1, but the UI goes through the scheme there
  too.

### Fail-closed

The most dangerous state is a filter that silently stops filtering.

- If the sidecar dies during a job, the file is marked **failed** or expired. No
  partial result is saved.
- If the sidecar is unreachable, the UI blocks dropping, saving and copying with
  a calm message. Reading and looking through stay possible; saving "without
  review" does not.
- Open questions do not block saving; they are **masked** on save ("when in
  doubt, mask", 9.3). That is the protecting direction.
- If name detection or a language pack is missing, dropping stays blocked until
  first-run setup has downloaded them.
- Every automatic fallback (GPU → CPU, MPS fallback) is logged in the
  diagnostics report and **never** silently counted as success.

---

## 3. Core stack

| Layer | Decision | Note |
|---|---|---|
| Desktop shell | Tauri 2 | |
| **Frontend** | **SvelteKit 3 + Svelte 5 (runes)**, static, no SSR | |
| **Components** | **own components** (`#lib/ui`) | no shadcn-svelte; see 9.7 |
| **Primitives** | **Bits UI** | focus management, keyboard handling, ARIA |
| **Styling** | **Tailwind v4** with own design tokens (`tokens.css`) | light and dark |
| Sidecar | Python 3.12 + FastAPI + Uvicorn | API v2 |
| PII engine | `presidio-analyzer`, `presidio-anonymizer` | from Data Privacy Stack, ≥ 2.2.363 |
| Structured data | **`presidio-structured`** | for CSV |
| NLP (default) | **GLiNER multi-PII as ONNX** on ONNX Runtime | multilingual; a download, not in the installer |
| NLP (fallback, sentence splitting) | spaCy `de` and `en` (small) | per language pack |
| Second opinion | small classifier (ONNX, int8) | optional, a download |
| NLP GPU | GLiNER on CUDA or MPS | optional, after a test inference |
| PDF | PyMuPDF | |
| Office | `python-docx`, `openpyxl` | |
| OCR (macOS) | **Apple Vision** (`VNRecognizeTextRequest`, revision 3, `de-DE`, `en-US`) through the `unname-vision-ocr` helper | part of the system; nothing to download |
| OCR (Windows) | Tesseract + `deu`, `eng` | language data as a download |
| Audit | SQLite | without content |

### Hardware requirements (measured)

Measured with `de_core_news_md` on **one CPU core and 3 GB RAM**: 428 MB RSS,
8.9 s model load (once at start), 0.10 s per text page (~2,250 characters),
~23,600 characters/s.

| Mode | RAM | Cores | SSD | Note |
|---|---|---|---|---|
| **Minimum (CPU, spaCy)** | 8 GB | 2 | 3 GB | runs; batches are slow |
| **Recommended (CPU, GLiNER)** | 16 GB | 4 | 5 GB | target for office and small-business laptops |
| **Batch + OCR in parallel** | 16–32 GB | 8 | 8 GB | |
| **GPU path (transformer)** | 16 GB + 8 GB VRAM | 4 | 10 GB | optional |

---

## 4. Windows

A signed MSI/NSIS installer with the sidecar and Tesseract bundled (Tesseract
only in the Windows package; on the Mac Apple Vision does the recognition, see
5). The detection models are too large for the installer: first-run setup
downloads them once and checks every file against the SHA-256 checksums in the
signed app (10); alternatively a model package file installs them without
internet. No Docker for end users. A capability check runs on first start. GPU
errors fall back to CPU without loss of function.

For the CUDA path: Presidio documents a **silent fallback** when `cupy` is
missing or does not match the driver version; spaCy then switches to CPU
without a word. The capability check therefore runs a real test inference and
shows its result instead of relying on `torch.cuda.is_available()`.

| Condition | NLP path | Workers |
|---|---|---|
| 2 cores, 8 GB, no GPU | rule PII + spaCy CPU | 1 document worker, no parallel OCR |
| 4 cores, 16 GB, no GPU | rule PII + GLiNER CPU | 2 workers, 1 OCR job |
| 8+ cores, 16–32 GB, no GPU | rule PII + GLiNER CPU | 3–4 workers, at most 2 OCR jobs |
| NVIDIA < 8 GB VRAM | CPU NER by default | GPU opt-in only |
| NVIDIA ≥ 8 GB VRAM | rule PII on CPU + transformer NER on GPU | 1 GPU NER worker, 2–4 CPU workers |
| CUDA/cupy broken | full CPU fallback | no loss of function, diagnostics entry |

The OCR lanes in this matrix apply to Tesseract; Apple Vision has its own (5).

---

## 5. macOS

Separate signed bundles per architecture, Developer ID signature,
notarization, hardened runtime, security-scoped bookmarks, temporary job data
excluded from iCloud and backup through `NSURLIsExcludedFromBackupKey`;
originals are never changed.

`PRESIDIO_DEVICE` accepts any valid PyTorch device string and takes precedence
over automatic detection:

```bash
export PRESIDIO_DEVICE=mps    # force
export PRESIDIO_DEVICE=cpu    # reproducible CPU tests
export PRESIDIO_DEVICE=cuda:0 # a specific GPU
```

MPS is detected automatically on Apple Silicon without extra dependencies.
Standard spaCy models do **not** benefit from a GPU (data transfer overhead); a
GPU pays off only for transformers, GLiNER, Stanza and spaCy transformer models.
`PYTORCH_ENABLE_MPS_FALLBACK=1` stays permitted as a compatibility mode but must
be visible in the diagnostics report.

| Hardware | Default | GPU | Note |
|---|---|---|---|
| M1/M2, 8 GB | rule PII + spaCy CPU | off | works; GLiNER only for single documents |
| M1/M2, 16 GB | rule PII + GLiNER CPU | optional MPS | target configuration |
| M3/M4 Pro/Max, 32 GB+ | rule PII + GLiNER/transformer on MPS | automatic after a short test | conservative batch size |
| Intel Mac | rule PII + spaCy CPU | disabled | complete, just slower |
| MPS test fails | CPU | automatic fallback | diagnostics entry, no end-user error |

### Text recognition on the Mac (Apple Vision)

The Mac package contains no Tesseract. Recognition runs on Apple Vision
(`VNRecognizeTextRequest`, revision 3 fixed, `.accurate`, `de-DE` and `en-US`,
language correction off) in a small helper of its own (`unname-vision-ocr`,
Swift) that receives bytes on stdin and returns words with boxes in reading
order, one process per page. If revision 3 or a language is missing, the
helper does not switch silently; text recognition counts as unavailable. Pages
are additionally read in tiles (from 1,000 px at least two per direction) so
small print is not missed; rotated or upside-down text is recognized upright.
See [vision-ocr/README.md](../vision-ocr/README.md).

| Device | OCR lanes | Note |
|---|---|---|
| 8 GB and more | 2 | measured: 175–270 MB per call; two parallel calls give 1.5× throughput, four no more |
| under 8 GB | 1 | |

The first call per helper, location and macOS build compiles Vision for the
Neural Engine (≈ 50 s, locally, by `ANECompilerService`). The sidecar therefore
warms up at start, and the UI shows "Preparing text recognition (once)". A new
OS build appears in the diagnostics. An OCR quality gate with its own baseline
per engine and macOS major version blocks regressions
(`scripts/quality/ocr_gate.py`).

---

## 6. Detection strategy

### 6.1 Why NER alone is not enough

Measured on German business text with `de_core_news_md`/`lg`:

| Test case | Result without master data |
|---|---|
| `Tischlerei Josef Brandstetter e.U.` | **person name not recognized at all** |
| `Gebrüder Wachtelhuber` | **not recognized at all** (not a single entity) |
| `Muster` (German for *sample*) | wrongly recognized as PERSON |
| `Meier Spielwaren GmbH` | not recognized, yet it contains a customer's surname |

The literature agrees: about **78 %** recall on person names for
`de_core_news_lg`, against ~95 % for GLiNER. A fifth of misses is unusable for
a protection filter. Presidio documents the same problem itself: for
`DE_HANDELSREGISTER` it says commercial register entries directly identify
natural persons (sole proprietors). Exactly this case – a person name inside a
company name – is the systematic weakness.

### 6.2 Four-stage pipeline

**Stage 0 – master data match (load-bearing).** An exact match against known
names from the source system (ERP, client management, HR), implemented as a
`PatternRecognizer` with a `deny_list` and score 1.0. Whoever knows their
customers, clients and suppliers needs no statistical model for them. The match
also hits names inside company names, where NER fails.

- Separate lists for persons and organizations. A shared list would label
  `Meier Spielwaren GmbH` as a person.
- Several lists, each with its own date (Settings › Names & terms); import from
  CSV, JSON or text, update by hand or from a configured nightly export. A list
  older than a week is flagged.
- Lists are personal data and are stored encrypted. They never go into a
  template (8).
- Minimum length 3 characters, otherwise false hits flood the result.
- Plus **custom terms** (always mask, with a kind) and **custom patterns**
  (regular expressions for numbers with a fixed structure). Patterns run with a
  time limit against catastrophic backtracking; a pattern that matches almost
  any text is rejected. They are tested with the sidecar's regex engine, not in
  the browser.

**Stage 1 – deterministic detection.** Regex, context and checksums for
structured data, always active:

- Global: `IBAN_CODE` (with checksum), `EMAIL_ADDRESS`, `PHONE_NUMBER`,
  `IP_ADDRESS`, `URL`, `CREDIT_CARD`
- Germany (present in Presidio and verified): `DE_TAX_ID` (ISO 7064 checksum),
  `DE_TAX_NUMBER`, `DE_PASSPORT`, `DE_ID_CARD`, `DE_SOCIAL_SECURITY`,
  `DE_HEALTH_INSURANCE`, `DE_KFZ`, `DE_HANDELSREGISTER`, `DE_PLZ`
- `PhoneRecognizer` with `supported_regions=["DE","AT"]`

> **`DE_PLZ`:** base confidence is **0.05** according to the Presidio docs,
> with an explicit warning about a high false-positive risk; usable only with
> address context. With a global `needs_review: 0.55` this recognizer would
> never fire, so it needs its own threshold per entity or must only be active
> in an address context.
>
> **`DE_HEALTH_INSURANCE`** is health data under Art. 9 GDPR and therefore
> belongs to the locked category "Account, ID & access data" (7.2): always
> masked, keepable only per occurrence.
>
> **Warning about custom regex recognizers.** A hand-written IBAN regex
> (`[A-Z]{2}\d{2}[\sA-Z0-9]{11,30}`) matched greedily across word boundaries in
> a test, swallowed `AT61 1904 3002 3457 3201 von Petra` as one unit, let the
> surname through and destroyed the text. Rule: **where a built-in recognizer
> with checksum validation exists, it takes precedence.** Every custom
> recognizer needs negative tests with following words.

**Stage 2 – semantic detection.** NER for names, organizations and places.
GLiNER is the default; `de_core_news_lg` only as a fallback. Configured for
recall: better one hit too many that the user deselects than one too few. In
addition, a **stopword list** of German business terms that are regularly
misclassified as person names (*Muster, Rechnung, Angebot, Auftrag, Lieferung,
Palette, Container, Sendung, Firma, Kunde …*); without it, false hits make the
text unusable downstream.

**Stage 3 – suspicion check (mandatory).** Everything that looks like a name but
was caught by no stage is flagged and reported. Implementation: two consecutive
capitalized words that no entity covers, minus a stopword list for
sentence-initial words (*Sehr, Mit, Bitte, Vielen, Anbei, Betreff …*). In a
test this heuristic found `Gebrüder Wachtelhuber`, the name for which the NER
model returned **nothing**. The rule is cheap, runs on CPU only and closes
exactly the gap that would otherwise stay invisible.

**Stage 4 – human review.** Uncertain spots become numbered **questions**:
"Looks like a name – but was not recognized" (suspicion) and "Recognized as a
name – but uncertain". The user masks or keeps, keeps single occurrences
("Keep only here"), adds what was missed by selecting text, and corrects
boundaries (9.3). Detection always runs with **all** types; which are replaced
is decided only by the masking spec, so switching recalculates without
detecting again.

### 6.3 Choosing models

| Situation | Recommendation | Reason |
|---|---|---|
| Structured receipts, invoices | master data + rule PII; NER in addition | IBANs, tax numbers and IDs are captured better deterministically |
| Letters, contracts, case files | master data + GLiNER | free text with many names |
| Large batches with a GPU | transformer/GLiNER on CUDA or MPS | a GPU pays off only with notable NLP load |
| Weak device | name lists + rule PII + spaCy (small) | works, but the suspicion check matters most here |

### 6.4 Document languages and countries

Name detection (GLiNER) is multilingual. Per language, a **language pack**
brings sentence splitting (spaCy, small), stopwords and suspicion words, month
names, field labels, placeholders and invented names; German and English ship.

- **Language per paragraph:** mixed documents are detected paragraph by
  paragraph and checked with the rules of their language. Can be switched off
  (then one language per document).
- **Countries independent of language:** tax, social security and ID numbers
  belong to the country. An English contract from Vienna contains Austrian
  numbers. Shipped: DE, AT; CH, UK and US follow.
- **Placeholders in the language of the paragraph:** an English paragraph gets
  `[Company]`, a German one `[Firma]`. Codes are language-independent.

### 6.5 Second opinion (local classifier)

A small classifier on the device judges every question about names, companies
and places with four fixed answers (person, company or product, place, term)
plus a confidence. It writes no text and sends nothing. What it may decide on
its own is set by the user:

| Policy | Effect |
|---|---|
| **Toward protection only** (default) | From 80 % confidence for name/company/place it masks by itself; everything else stays a question. The result does not change, since open questions would be masked anyway – only what needs a look becomes less. Exception: if only the capital-letter heuristic fired (no model, no rule, no name list, no repetition of a recognized value), it also lets the spot stand from 80 % "term". |
| **Also "keep" from 95 %** | Additionally lets terms stand. Fewer questions; a mistake could leave a name in place. |
| **Suggest only** | It decides nothing; its suggestion appears on every question card. |

If it takes a name suspicion from 80 % to be a company, product or place, the
spot counts as such: it is masked if the spec includes companies or places.

Every AI decision appears under "Resolved by the local AI", can be reversed with
one click and is counted separately in the review report. A user decision
always wins. Without a model, or when switched off, questions appear without an
assessment.

---

## 7. Masking, categories and the way back

A **masking spec** determines the result of an entry – for all its files
together, and identical in the frontend and the sidecar (`MaskingSpec`):

```jsonc
{
  "method": "reversible",        // "reversible" | "irreversible"
  "style": "codes",              // reversible: "codes" | "surrogates"; irreversible: "placeholder" | "mask" | "remove"
  "categories": ["finance_ids", "names", "addresses", "contact", "numbers"],
  "anonymize": false             // exactly when irreversible ∧ all categories; the sidecar checks it
}
```

- **Method and style** → processing mode (7.1).
- **Categories** → which types are handled (7.2). The protection level is
  derived from them and kept only in the audit and the review report.
- **"Anonymize"** is not a third control but shorthand for "irreversible and
  everything", and the precondition for the "Anonymous" label (7.3).

The spec is stored as the default ("applies to everything new") and can be
changed per entry during review; switching recalculates without detecting
again.

### 7.1 Processing modes

| Mode | Result in the text | Way back | Legal classification |
|---|---|---|---|
| **Code** `<<PERSON_ba903855>>` | replaced, recognizable | yes, through the translation table | pseudonymization |
| **Surrogate** `Thomas Bauer` | replaced by a realistic substitute name | yes, through the translation table | pseudonymization |
| **Encrypted** | replaced, reversible with a key | yes, with the key | pseudonymization |
| **One-way checksum** | replaced, not reversible | no | **still pseudonymization** |
| **Placeholder** `[Name]` | the kind stays recognizable | no | toward anonymization |
| **Masked** `••••` | length is preserved | no | toward anonymization |
| **Removed** | the spot disappears completely | no | toward anonymization |

**Style → mode:** codes → code · invented names → surrogate · placeholder →
placeholder · blackout → masked · remove → removed. "Encrypted" and "one-way
checksum" stay in the sidecar and are tested; the UI does not offer them.

> **A common mistake:** a one-way checksum (hash) does **not** make data
> anonymous. The same input gives the same hash, so linkability is fully kept,
> and with a manageable set of candidates (a business with 200 customers) the
> hash can simply be tried out. Legally this is pseudonymization.

**Surrogate mode** suits language model tasks where the text must stay
readable, such as drafting a reply letter. Grammar and reading flow are kept
where codes disturb. The price: real and invented names can no longer be told
apart in the result. So the label says "contains invented names", the review
report notes it, and invented names are restored only on explicit request.

**Removed in PDFs.** The spot must be removed from the content stream
(`apply_redactions()` in PyMuPDF), not covered with a black rectangle. A
painted rectangle can be selected and copied in any viewer. The render and text
extraction check after the export is **mandatory** in this mode.

**Confirm irreversibility.** On the first irreversible save per entry, the UI
shows a compact dialog naming what is lost. The confirmation applies to the
method and style it was given for; anyone who switches on a saved entry
confirms again on the next irreversible save. The original is always kept; a
new file `<name>_protected.<ext>` is saved (`_geschützt` in the German UI; on conflict `… (2)`).

**Scans and images stay what they are.** A scanned PDF is saved as a PDF – the
pixels under every spot removed, the code as visible text on top, plus an
invisible text layer from the OCR (like OCRmyPDF), so the file is searchable
and an AI reads the text including codes. A photo or screenshot stays PNG/JPG
with codes, invented names and placeholders as pixels. In both cases OCR reads
back every drawn text; if the result is not exactly the inserted text, the copy
becomes a text file and "Saved" says so. Blackout and remove only fill.

### 7.2 Categories

The UI offers **categories** instead of levels. The catalog lives in the
sidecar (`GET /catalog`) and is the only source; a contract test keeps it equal
to the classified types (`ENTITY_MINIMUM_LEVEL`).

| Category | Types | Former level | Default |
|---|---|---|---|
| Account, ID & access data | `IBAN_CODE`, `BIC`, `CREDIT_CARD`, `CRYPTO`, `DE_TAX_ID`, `DE_TAX_NUMBER`, `DE_VAT_ID`, `AT_UID`, `DE_SOCIAL_SECURITY`, `AT_SVNR`, `DE_PASSPORT`, `DE_ID_CARD`, `DE_HEALTH_INSURANCE`, `SECRET`, `API_KEY`, `ACCESS_TOKEN`, `PASSWORD` | 1 | **always** (cannot be switched off) |
| Names | `PERSON` | 2 | on |
| Addresses | `ADDRESS`, `STREET_ADDRESS` | 2 | on |
| Phone & email | `PHONE_NUMBER`, `EMAIL_ADDRESS`, `IP_ADDRESS` | 2 | on |
| Customer & case numbers | `CUSTOMER_NUMBER`, `CASE_NUMBER`, `DE_KFZ`, custom patterns | 2 | on |
| Web addresses | `URL` | 3 | off |
| Companies | `ORGANIZATION`, `DE_HANDELSREGISTER`, `AT_FIRMENBUCH` | 3 | off |
| Places *(circumstances)* | `LOCATION`, `DE_PLZ` | 3 | off |
| Dates *(circumstances)* | `DATE_TIME` | 3 | off |
| Other attributes *(circumstances)* | `NRP`, `JOB_TITLE`, `PROFESSION`, `OTHER` | 3 | off |

The default equals the former level 2, all categories equal level 3. The price
of the "circumstances" stays visible: a customs document without places and
dates is no longer a customs document. Before saving, the UI therefore shows
how much of the text is masked, and the residual-risk check (7.4) says what is
left.

**Exceptions instead of overrides per type.** "Keep only here" leaves exactly
one occurrence in place; "Keep all names" takes the category out for the whole
entry; locked categories can only be kept per occurrence. Both appear in the
review report.

### 7.3 Labels: when may "Anonymous" appear on screen?

Blacking out feels final. Legally it rarely is. Recital 26 GDPR asks whether
identification is still possible with **all means reasonably likely to be
used**. A receipt with all names removed that still says "customer number 4711,
500 pieces of article 90210, delivery to Vienna, 3 April 2026" can concern
exactly one person in a business with 200 customers.

Every saved file carries exactly one of three labels. The sidecar assigns it
(`labels` per result); the UI only displays it and never derives it itself.

| Label | Condition | In the review report |
|---|---|---|
| **Reversibly masked** | method reversible; with "invented names" additionally "contains invented names" | pseudonymization |
| **Irreversibly masked** | method irreversible, "Anonymous" not met | blackout – not yet anonymous |
| **Anonymous** | **all** at once: "Anonymize" (irreversible and all categories), only modes without a way back (placeholder, masked, removed), **no** plaintext exception ("Keep only here" and "Keep" count), residual-risk check without a finding | anonymization (only without a finding) |

If a condition is missing, the sidecar names the reason (`anonymous_blockers`)
and the label stays "Irreversibly masked". The strongest term appears as a
word in no Svelte file (tested); it reaches the screen only through the label.

This rule is not formalism: a false anonymity claim in the UI is exactly the
assurance a user later relies on – and which, in doubt, the company bears, not
the tool's maker.

### 7.4 Residual-risk check

After processing, the app checks the cleaned text for remaining indirect
attributes and counts their classes:

- dates and times
- amounts and quantities
- article, contract and case numbers
- places and regions
- rare technical terms or job titles

If **two or more classes** remain, a notice appears: the combination could
single out a person even though no name is left. This finding prevents the
"Anonymous" label (7.3) but not saving. The check is a heuristic and is
labeled as one. It does not replace a human assessment – it makes sure the
question is asked at all.

### 7.5 Separating determinism from reversibility

An early draft contradicted itself: the mapping was to stay "in RAM only by
default" while placeholders were to be "consistent". Across sessions that is
impossible. The two properties can be separated technically:

| Property | How achieved | Needs storage? |
|---|---|---|
| **Determinism** – the same name always gives the same code | `HMAC-SHA256(local secret, type + normalized value)`, truncated | **no**, only the secret |
| **Reversibility** – code back to the plain name | local mapping table | yes |

Consequently:

- Irreversible masking writes **no** mapping and still keeps the promise of
  consistent replacement.
- Reversible masking writes mappings into **one shared**, encrypted translation
  table across all entries – so "Restore" recognizes codes from any earlier
  entry, and the same name gets the same code everywhere.

**Saving is the commit.** Apply is a dry run: codes are computed (HMAC,
deterministic) but not written to the table. Only "Save" (documents) or "Copy"
(text) writes the mappings – together with export and audit, per code with the
entries it was saved in and its last use. Anyone who toggles ten times and then
discards leaves nothing in the table. A retention period (6/12 months/never)
deletes unused mappings.

### 7.6 Code format

The Presidio default operators are unsuitable for LLM workflows: `encrypt`
produces long Base64 strings that eat context windows and are often mangled by
language models.

Format: **`<<TYPE_xxxxxxxx>>`** with 8 hex characters, for example
`<<PERSON_ba903855>>`. It is short, visibly a placeholder, passed through
unchanged by language models, and safe to restore with a regex. Measured on
24 October 2026 with four local models (`acceptance/llm-round-trip.json`): the
format stays.

**Tolerant restoring:** missing or single brackets, other capitalization and
spaces (`<PERSON_ba903855>`, `PERSON_ba903855`, `<< person_ba903855 >>`) are
recognized – but only if unambiguous; otherwise they are reported as unknown.

### 7.7 Three tested pitfalls

All three actually occurred in the reference implementation.

**Normalization.** `Hans Meier`, `hans  meier` and `HANS MEIER` must give the
same code, otherwise one person falls apart into three pseudonyms and every
analysis is worthless. Solution: NFKC, collapse whitespace, lowercase – before
the HMAC.

**Canonical spelling.** The flip side: when writing back, only *one* spelling
can be output. Without a countermeasure, an all-caps field seen first in a scan
permanently decides the spelling in all outputs. Solution: a ranking in which
mixed case beats all caps and lowercase.

**Span trimming and overlap.** NER often returns offsets with a leading space or
trailing punctuation; if that ends up in the code, it is lost on write-back.
Hits also overlap (PERSON inside ORG). Solution: trim spans, the highest score
wins on overlap, replace **from back to front** so the offsets of the remaining
hits stay valid.

### 7.8 Key management

- Generate the secret once, file permissions `0600`, outside the project and
  export folders.
- **Changing the key changes all codes.** Old outputs can then no longer be
  resolved. The UI names the consequences before confirming ("New key …",
  "Clear table …").
- Taking it to another device: table and key as a **password-protected backup**
  (scrypt + AES-GCM); importing replaces the device's state after asking.
- Storing the secret in the OS keychain is planned; until then a file with
  `0600` or a private ACL.
- Never store or send the table and the saved files together.

---

## 8. Base profile and templates

The UI knows no profiles. The profile loader in the sidecar stays, with a new
role.

**Base profile** (internal, exactly one, `base_profile.py`): formats and sizes
(PDF, DOCX, XLSX, CSV, TXT, MD, PNG, JPG, up to 50 MB per file), pipeline (OCR
automatic, `deu`/`eng`), thresholds per type, suspicion check, residual-risk
check and export rules (original always kept, PDF render and text extraction
check mandatory). It builds on Presidio's native YAML no-code configuration;
detection always runs with **all** types, and the masking spec decides which
are replaced (7).

**Template** (Settings › Names & terms › Template): custom terms, custom
patterns and optionally a masking default under one name – "Insurance", "Law
firm", "No template". Export and import as a file for teams. **Name lists
never belong in a template**: a template is a plaintext file that travels by
email or in a team folder; lists contain customer names and stay on the device
that imported them.

```yaml
format: unname-vorlage    # the format identifier stays German: it is part of the file format
version: 4
title: Insurance
masking:                      # optional
  method: reversible
  style: codes
  categories: [finance_ids, names, addresses, contact, numbers]
terms:
  - text: Projekt Falke
    type: OTHER
patterns:
  - name: Policy number
    regex: 'POL-\d{6,8}'
    type: CUSTOMER_NUMBER
    enabled: true
```

**Example templates:** the three earlier profiles (`profiles/*.yaml`: invoice &
receipt, law firm & consulting, LLM upload) are example templates, plus
"Insurance" with sample patterns. Examples cannot be overwritten; a changed
version is saved as a template of its own. A template from another version or
with a pattern that is too broad is rejected with a reason. See
[profiles/README.md](../profiles/README.md).

---

## 9. User interface

Guiding ideas: history instead of a profile wizard, see beforehand instead of
checking afterwards, a few questions instead of long hit lists. Explanatory
text does not sit permanently on the pages but comes on request (9.7). The UI
ships in German (the reference) and English.

### 9.1 History and "New"

On the left the **sidebar**: "New", the navigation **"Open"**, **"Completed"**
and **"All entries"** with entry counts, below it **"Recently created"** with at
most three entries (by creation time; opening or renaming does not reorder
them), and "Settings" at the bottom. How long the history is does not decide
how long the sidebar is. Collapsed, "New", the three history destinations as
labeled icons and "Settings" stay reachable.

**State of an entry.** One rule assigns every entry to exactly one area; "All
entries" is the union of both. Open are "Processing" (also legacy entries
without a state), "To review", "Error" and "Changed since saving"; completed
are "Saved" and "Copied". An irreversibly saved result is simply called "Saved"
in the history – labels, restores and export details stay in the entry views
(9.4). The state is always text, never color alone.

**Overview** (`/history?view=open|completed|all`, `sort=newest|oldest`;
without a valid area it shows "All entries"). Per entry: title and short
reference, scope (format, "PDF scan" only with text recognition, known page
count; for several files the count, at most two formats and a page total only
if every page count is known; "Text" for text entries), created (today/yesterday
or a date) and state, plus "Details" (used space and deletion date) and
"Rename". Search runs locally over title, reference (with or without "#"),
format and date – never over file names, paths, document text or findings; all
words must match, case and accents do not count, an exactly matching reference
comes first. The search text stays only in the session's memory. The first 50
hits are shown, "Show more" adds 50; the navigation counts always cover the
whole history. "Back to history" in an entry opened from there restores area,
sort, search, scope and scroll position.

**Title and short reference.** Titles come from the document kind and the file
names – **never from person names** ("Angebot_Hofer.docx" → "Offer"); renaming
is possible, changes neither state nor draft, and the sidecar never overwrites
a custom title. Every entry also carries a **short reference** of six
characters (alphabet without 0/O, 1/I/L, for example "#K7M2QX"): assigned at
random, never derived from content or names, immutable and unique only among
the entries of this device – no replacement for the entry ID in routes and the
API.

The history is stored encrypted in the sidecar and deleted after 7/30/90 days
or never – together with the entry its draft, that is the saved copies of the
originals (10); the translation table has its own retention period. An entry's
details name the used space and the deletion date; Settings › History shows the
total used space and the free space on the drive. Before reading in, the app
warns about low free space without blocking.

The **start page** ("New") offers "Documents | Text":

- **Documents:** a drop area for files and whole folders, "Choose files",
  "Whole folder". The shell reads the files and hands them to the sidecar; the
  UI gets no file system access. Below it the **masking card** with an example
  ("Invoice.pdf → Invoice_protected.pdf") that follows the choice, and "Adjust
  masking".
- **Text:** type or paste on the left, the result live on the right. Live
  masking saves nothing. Only **"Copy"** writes the mappings and creates a
  history entry "Text · copied"; the text itself is never saved.

### 9.2 Choosing the masking

One menu, the same everywhere (start page, review, settings): **Reversible**
(restorable, technical term pseudonymization) or **Irreversible** (technical
term blackout), below it **"Anonymize"** – "Mask everything, including places
and dates" –, the categories as chips ("Personal data" and "Circumstances"),
and under "Fine-tune" the style. Three design rules:

- **The price sits next to the choice.** Every option says in one sentence what
  it means; "Anonymous" is promised only without a residual-risk finding.
- **Usability meter:** a line "x % of the text masked" in the menu.
- **Recoverability on the label:** reversible and irreversible carry icon and
  word; before the first irreversible save comes the confirmation (7.1).

What is switched on the start page becomes the default for everything new;
during review a change applies to the entry and can be applied "As default".

### 9.3 Detection running and review

**Detection running:** per file the phase and progress, "Found so far" per
category, "Cancel". "Review" appears as soon as the first file is done.

**Review** shows the document the way it will be saved:

- **View.** Files with fixed pages (PDF, scan, image) appear as a **page
  image** – with logo, lines, tables and original font; codes stand as an
  overlay where the export puts them, in the size and font class of the
  original. **Word files** appear from a cleaned preview copy (without links,
  external references and metadata) with pages, tables, headers and footers;
  codes stand in the same run in which the export inserts them. A paragraph
  that cannot be mapped safely appears simplified and is named. If saved page
  breaks are missing, the view breaks pages itself; missing Office fonts are
  replaced by bundled, metrically equal ones (Carlito, Caladea, Arimo, Tinos,
  Cousine), other missing fonts are named. **"Page-faithful"** (optional) shows
  Word files as pages of a layout engine – from exactly the DOCX that saving
  would write; the view appears only if an engine is present, names it and names
  spots that cannot be located in the page image. **Excel and CSV files** appear
  as a grid with sheet tabs, column widths, row heights, merged cells, frozen
  areas, font, fill, borders and number formats; codes stand in the cell the
  export changes. Conditional formatting and hidden sheets are named. **"Text"**
  (a "Page | Text" switch, remembered) stays the alternative: for text files, as
  a fallback and as a low-barrier view with all functions.
- **Viewer without a header bar**, file tabs on top (back, tabs with the count of
  open questions, "+" for more files) and a floating bar at the bottom: result ·
  side by side · original, the masking menu with kind and number of spots, zoom.
  Holding Space shows the original.
- **Codes in the text:** a click opens **one** shared menu: "Keep only here",
  "Keep all names", "Mask again". The pointer shows the original.
- **Questions** (6.2, stage 4) stand numbered in the text and as cards in the
  right panel: "Mask" or "Keep". Decided ones stay visible with "Change",
  "Resolved by the local AI" lists what the second opinion decided (6.5), "Accept
  all AI suggestions" takes suggestions at once.
- **When in doubt, mask:** open questions do not block saving. They are
  **masked** on save and counted in the review report as "masked without a
  decision on saving". That stays fail-closed because the protecting direction
  wins.
- **Adding:** select text → kind (name, place, company, number, other) → Mask,
  on request "All n occurrences in m files". Selecting across a code offers
  "Extend spot".
- **Keyboard:** Tab through the codes in reading order, Enter opens the menu,
  Alt+N jumps to the next open question, Alt+M adds, Escape closes. The tab
  order follows the picture – tabs on top, document, bottom bar, question panel
  – although the bars float above the document.
- **Narrow windows:** below 1200 px the question panel starts as a rail, below
  1040 px the sidebar is collapsed.

Without this view, review is only confirmation, not correction – so adding and
boundary correction belong to the core, not to the extras.

### 9.4 Saving and Saved

"Save all n files" is the commit (7.5): the sidecar recomputes the state the
window shows, checks PDF exports by rendering and text extraction – scans
additionally by OCR: no original value in image or text layer, every code
readable and as often in the text layer as placed –, writes audit and mappings
and provides the result; the shell checks hash and size and publishes without
collisions – by default into a `protected` folder (named in the app language) next to the originals, otherwise
after a folder dialog. Originals stay unchanged.

**Saved** shows the file list with a label per file (7.3), the counts (masked,
decided by you, resolved by the local AI, masked when in doubt), "Open folder",
"Save review report" and "Edit again" – which leads straight into review, even
after a restart: the draft holds original, extraction and detection, so the
originals need not be present anymore. Only entries from before drafts and files
whose saved copy is no longer readable need the original once more ("Choose
original"); the shell reads it again and reapplies the stored decisions, and if
a file changed, the view says so.

A saved entry stays a draft with exports. In review the button reads "Save
again", the method can be switched (anonymize, pseudonymize, reversible), and
every save lands next to the earlier one – same-named copies are numbered, never
overwritten. On top is the latest save, below it "Earlier saves" with time,
method, file count, "Open folder" and a review report per save (a content-free
export history, at most 25 per entry). If work continued since, Saved and the
history say "Changed since saving"; merely viewing does not count. If the
detection of a file no longer matches the settings (languages, second opinion,
text recognition, detection model), review offers "Detect again" – from the
saved copy, never by itself; decisions stay where the spots stayed the same. For
"Reversible", the card "Restore an AI answer" follows directly; it stays as
long as one of the saves was reversible.

The **review report** appears in the app language and names the masking, the
derived level, the counts, the residual risk, the label with its technical term,
the PDF check ("Scan painted over, with text layer" or "Saved as text – code in
the image not reliably readable") and the checksum – never original values,
replacements or text excerpts.

### 9.5 Restoring

Paste an AI answer or drop it as a file (DOCX, XLSX, Markdown, text); the codes
are replaced through the shared translation table, tolerant of changed spelling
(7.6). The count names the origin: "5 from here · 1 from »Rental agreement
(scan)« · 1 unknown – not in the translation table: another device or an older
key". Inserted spots can be highlighted. Files are replaced inside the file and
the formatting stays; a new file `<name>_with_names.<ext>` is created (`_mit_Namen` in the German UI). All in
memory only – answers are not saved. With irreversible masking the view explains
why there is nothing to restore.

### 9.6 Settings and first-run setup

A dialog with nine sections: **General** (app language DE/EN, appearance
light/dark/system), **Masking** (default with an example sentence), **Document
languages** (6.4), **Second opinion** (6.5), **Names & terms** (name lists,
custom terms, custom patterns with "Try out", template), **History**
(retention, "Clear history …"), **Translation table** (key figures, retention,
password-protected backup, "New key …", "Clear table …"), **Models & storage**
(components with size and state, source, checksums, check, remove, package file)
and **Technical** (diagnostics report without content, developer mode).
Consequential actions name their consequences before confirming and start on
"Cancel".

**First-run setup** appears as long as name detection or a language pack is
missing: choose components with size and source, download with progress and
check, "What is needed is here – you can get started", alternatively "Install
model package from file". "Set up later" leaves history and restoring usable;
dropping stays blocked until then.

### 9.7 Frontend

**Decision:** SvelteKit 3 with Svelte 5 (runes), static (`adapter-static`, no
SSR), own components in `#lib/ui` on **Bits UI** as the primitive library,
styled with **Tailwind v4** and own design tokens. The components live in the
repository and are readable and checkable – no runtime dependency, no CDN, no
late loading. Tauri serves static files; a server adapter would make the app
unable to start.

**Components** (one per pattern, reference images on the developer page
"Components"): Button, IconButton, Segmented, ChipSelect, Chip, Switch, Select,
TextInput, Textarea, Field, Dialog, ConfirmDialog, Popover, Menu, Tooltip,
Toaster, DropZone, FileRow, FileBadge, HistoryList, MaskLabel, MaskingControl,
SegmentText, Meter, Progress, Spinner, Skeleton, StatGrid, Section, SettingRow,
CoachMarks, Logo. A segment renderer (`SegmentText`) with a style map shows all
text views – review, text mode, restore, pattern test.

**Design tokens instead of entity colors.** Colors live only in `tokens.css`,
one value per token for light and dark; a build guard forbids palette classes,
`dark:` in components and inline colors. The UI colors by **state** (masked,
question, kept, by AI, restored), not by type; the type always appears as text
or in the `aria-label` as well. A test checks every allowed text/surface pair
against its minimum ratio (text 4.5:1) in both modes. Dark mode runs through
`mode-watcher`.

**Brand.** Name "unname", logo "D2 · Fading" (#1F1846), app icons from
`unname-logo/`.

**Languages.** A typed dictionary of its own (`#lib/i18n`), German as the
reference, English complete (tested); plural forms through `Intl.PluralRules`,
`<html lang>` follows the app language. App language ≠ document language (6.4).
The diagnostics report stays German because the log messages of shell and
sidecar are German.

**Help on request.** "?" shows numbered hints (coach marks) at the places they
explain; the target points to the note through `aria-describedby`. On the very
first start they appear once by themselves on the start page.

**Fonts – bundled, not loaded.** All fonts ship as WOFF2 files in the app bundle
(Fontsource packages, only the needed styles, `latin` + `latin-ext`):
**Instrument Sans** (UI), **Instrument Serif** (headings), **Newsreader**
(document text), **JetBrains Mono** (codes). No `<link>` to Google Fonts, no
`@import` from a CDN, no loading at runtime. This is not a matter of style: a
font request when opening a document transmits time and IP address to a third
party and contradicts the promise "processing only on this device". The fonts
must have a license that allows redistribution (SIL OFL; license texts in
`THIRD_PARTY_NOTICES/fonts/`), full German character coverage including umlauts,
eszett and low quotation marks, and tabular figures (`tabular-nums`) wherever
numbers stand under each other. The review view encodes meaning through the
font style – codes monospaced, running text proportional – and a substitute
font would destroy that distinction, so the fonts are bundled rather than taken
from the system.

**Secured structurally, not on trust.** The content security policy in
`tauri.conf.json` forbids external sources:

```json
{
  "app": {
    "security": {
      "csp": "default-src 'self'; font-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' unname-api: http://unname-api.localhost"
    }
  }
}
```

**Proof in the build.** `scripts/check-no-external-assets.mjs` scans the built
files for external sources and matches every bundled font with its license
notice; a hit breaks the build. In addition a Playwright test starts the app
without a network and compares the rendering with a reference image.

**Accessibility.** A visible focus ring on every operable element, review fully
with the keyboard (9.3), the type of every spot readable by screen readers,
color never alone. Whoever chooses "reduce motion" in the system gets no
transitions and animations. axe-core (WCAG 2.1 AA, loaded offline) checks every
board in light and dark with no findings.

---

## 10. Privacy and security

- No cloud call, no cloud fallback, no default telemetry.
- Source files are only read, never overwritten.
- Helper programs (text recognition, the office engine for "page-faithful") only
  from the signed package; the office engine and the Vision helper receive bytes
  instead of paths and start without the session's environment. System
  frameworks (Apple Vision) count as part of the platform. On the first start of
  a new binary, macOS checks it itself (Gatekeeper, `syspolicyd`); that network
  traffic belongs to the system, not the app – Vision downloads nothing (capture
  of 1 November 2026).
- **Network rights in the Mac package:** the app carries `network.client` but no
  `network.server`; `network.client` stays because WKWebView under the sandbox
  does not load the UI without it. The sidecar, the Vision helper and the office
  helper carry no network rights and inherit the app's sandbox. Models are
  downloaded only by the download service `org.unname.desktop.download` (own
  sandbox, only `network.client`, no `inherit`): it checks HTTPS, host and every
  redirect and passes the bytes through a pipe to the shell, which checks size
  and SHA-256 and writes. If it is missing from the package, there is no
  fallback to a download in the shell. The package smoke test checks this
  distribution on the signature. See
  [download-xpc/README.md](../download-xpc/README.md).
- A job-isolated random folder in the cache; a job leaves memory after a short
  idle time and its working folder is deleted.
- **Drafts:** original, extraction and detection of every file stay until the
  entry is deleted or its retention period ends – encrypted (AES-256-GCM, block
  by block, one key per entry, wrapped with a master key of its own), in the app
  data folder under `drafts.noindex`, excluded from Spotlight and backup. Page
  images, layout maps and apply results are never stored but regenerated.
  Deleting ("Delete entry", "Clear history", retention) removes entry and key in
  one atomic write (crypto-shredding), then jobs, memory and files; leftovers
  without an entry are cleaned up by the sidecar at start and hourly. The delete
  dialog and the settings name the saved copies of the originals.
- The audit log holds metadata only: time, masking spec, entry ID, hit count per
  type and category, decisions by origin (user / local AI / "masked without a
  decision"), export hash – no original values, no text excerpts.
- History, name lists, terms and patterns are stored encrypted in the sidecar
  state; settings contain no personal data; the short reference of a history
  entry is random and carries no document data (9.1).

**The promise:** "Documents never leave the device; a one-time, verified model
download, no network traffic afterwards."

**Model download.** The detection models are too large for the installer. They
are downloaded exactly when the user starts it in first-run setup or under
"Models & storage" – only from the hosts of the chosen source, only over HTTPS,
redirects only to those hosts. Every file is checked against the SHA-256
checksum embedded in the signed app (`models.lock.json`); if it deviates, it is
discarded and reported. Installation is atomic, only safetensors or ONNX, never
executable code. The HTTP client exists only for the duration of a download; the
sidecar runs with `HF_HUB_OFFLINE=1`. Without internet, a model package file
installs the same, equally checked files. Nothing is uploaded in the process –
no documents, no usage data.

**Pseudonymization is not anonymization.** The Court of Justice of the EU held
in **C-413/23 P** (EDPS v SRB, 4 September 2025) that pseudonymized data is not
personal data in every case and for every party – what matters is whether the
**recipient** can re-identify with reasonable means. The architecture pays into
exactly that: if the secret and the table stay local, the recipient cannot. The
EDPB's guidelines 01/2025 take the opposite view; the question is not settled.
The app therefore makes **no anonymity claim** for **reversibly** masked
results.

**Wording rule for the UI (consistent with 7.3):** "Reversibly masked"
(pseudonymization) or "Irreversibly masked" (blackout – not yet anonymous).
"Anonymous" appears exactly under the rule in 7.3 – "Anonymize", only modes
without a way back, no plaintext exception, residual-risk check without a
finding – and never otherwise.

**The mapping table is a risk of its own.** Whoever has the table and the secret
can re-identify everything. Store it encrypted, take it along only as a
password-protected backup, never deliver it together with the saved files.

---

## 11. Acceptance criteria

Baseline requirements:

- Installation and first start without Docker and without a developer setup
- A complete CPU mode on Windows 11 x64 and Apple Silicon
- GPU initialization never blocks a job; a reliable CPU fallback
- Formats: PDF, DOCX, XLSX, CSV, TXT, Markdown, PNG, JPG
- Local review, correction, saving as a new file

The criteria are numbered with stable IDs. The proof per criterion – test file,
test name and what it shows – lives in
[`acceptance/spec-11.json`](../acceptance/spec-11.json); a test keeps manifest,
tests and this section identical. "External" means the repository tests check
the preconditions, but the real proof is produced outside the repository. See
[acceptance.md](acceptance.md).

| ID | Criterion | Proof |
|---|---|---|
| **A-01** | **Name lists demonstrably effective:** `Tischlerei Josef Brandstetter e.U.` is recognized with the list; without the list the test may fail – with it, not. | automated |
| **A-02** | **Suspicion check demonstrably effective:** an unlisted, unusual name appears as a question. | automated |
| **A-03** | **Fail-closed:** if processing fails during a job, no partial result is saved; dropping and saving are blocked, reading stays possible. | automated |
| **A-04** | **All styles work:** codes, invented names, placeholders, blackout and remove give the expected result for the same document; encryption and one-way checksum stay tested in the sidecar. | automated |
| **A-05** | **PDF "remove":** after saving, the removed text can be neither selected nor extracted; the render check is mandatory. | automated |
| **A-06** | **Categories take effect:** the default leaves places and dates, all categories replace them too; account, ID and access data are always included; "Keep only here" and "Keep all …" work per occurrence and per entry. | automated |
| **A-07** | **"Anonymous" only per 7.3:** with a residual-risk finding or a plaintext exception, "Anonymous" appears nowhere; the term is in no Svelte file. | automated |
| **A-08** | **Residual-risk check:** a document that still contains a date, an amount and an article number after blackout triggers the notice. | automated |
| **A-09** | **Way back:** mask text, send the codes through a language model, resolve the answer again – lossless, tolerant of changed spelling, also after a restart. | automated |
| **A-10** | **Three manual interventions** (keep, add, correct a boundary) end up in result, review report and audit log. | automated |
| **A-11** | **Negative tests for every custom recognizer**, in particular across word boundaries. | automated |
| **A-12** | **No outgoing network traffic** from the running native app – except during a model download the user started, and then only to the hosts of the source. | external (capture) |
| **A-13** | **Accessibility:** review fully with the keyboard; focus order follows the picture, also in the floating bars; contrast 4.5:1 in light and dark; axe (WCAG 2.1 AA) without findings on every board. | automated |
| **A-14** | **Hardware acceptance on 8 GB RAM / 2 cores** under Windows 11 x64, not on 32 GB. | external (device) |
| **A-15** | **Mappings only on save:** toggle ten times, discard → table unchanged; save → exactly the used codes. | automated |
| **A-16** | **When in doubt, mask:** an open question is masked in the output and counted in the review report as "masked without a decision". | automated |
| **A-17** | **Restoring** text and DOCX with codes from two entries and one unknown code. | automated |
| **A-18** | **History:** titles contain no recognized name; entries disappear after their retention period, the table stays. | automated |
| **A-19** | **Document languages:** a mixed DE/EN document paragraph by paragraph; an English contract from Vienna with Austrian numbers; placeholders in the language of the paragraph. | automated |
| **A-20** | **Models:** a file with a deviating checksum is discarded and reported; without models, dropping is blocked. | automated |
| **A-21** | **Second opinion:** fewer open questions at equal or better recall; every AI decision visible and reversible. | automated (invented corpus) |
| **A-22** | **Recall measurement as a release gate:** per area of use a versioned corpus of at least 200 real, previously pseudonymized documents; precision, recall, false positives and **false negatives**, separately with and without name lists, plus the second opinion on the same corpus. Without this number the protective effect cannot be shown to a supervisory authority. | external (real corpus) |
| **A-23** | **Performance:** a 50-page PDF, an entry with 9 files and a file with 1,000 codes stay usable; measured with and without models (`acceptance/performance.json`). | automated |
| **A-24** | **Bilingual:** English complete, the app language applies at once; coach marks appear once by themselves on first start. | automated |
| **A-25** | **Layout fidelity for fixed pages:** PDF, scan and image appear in review as a page image; every marker sits on its word (≤ 1 px at 100 %), also on rotated pages. | automated |
| **A-26** | **View = file:** for PDFs the result view matches the rendering of the saved file (pixel comparison of the placement areas); areas, baseline, size, font and color of the replacement text sit in the file as the apply planned them. | automated |
| **A-27** | **Word and Excel from the file itself:** tables, headers and footers, merged cells; every replacement sits where the export puts it; paragraphs that cannot be mapped visibly fall back to text. | automated |
| **A-29** | **Scans stay scans:** a reversibly masked scan is saved as a PDF, OCR reads every code back, no original value in image or text layer; an image stays an image with the codes drawn in, otherwise a text file with a notice. | automated |

A-28 and A-30 are reserved and not used. For **fonts** (9.7) it also holds: the
build check for external sources breaks on a hit, the app starts without a
network identical to the reference image, the CSP blocks `font-src` outside
`'self'`, and every font file has its license notice.

---

## 12. Roadmap

1. **MVP 0.2 (done):** Windows x64 + Apple Silicon, CPU mode, name lists,
   suspicion check, review with questions, saving as a commit, restoring,
   settings.
2. **Models, languages, second opinion (done):** download with checksum and
   package file, document languages DE/EN, countries DE/AT, local classifier.
3. **Acceptance (open):** network capture, limit device, real corpus (A-12,
   A-14, A-22); hosting the models under an organization of our own with a
   mirror; secret in the OS keychain.
4. **More countries:** CH, UK, US with recognizers and negative tests.
5. **GPU:** measurement per hardware class, batch sizes.
6. **Enterprise:** signed templates, central distribution through Intune/Jamf,
   offline operation stays possible.

The detailed open items are in [roadmap.md](roadmap.md).

---

## Sources

Verified in July 2026:

- Presidio project transition to Data Privacy Stack —
  `presidio.dataprivacystack.org/project_transition/`
- GPU acceleration, `PRESIDIO_DEVICE`, engine overview —
  `presidio.dataprivacystack.org/analyzer/nlp_engines/gpu_usage/`
- Supported entities including German recognizers and the `DE_PLZ` confidence —
  `presidio.dataprivacystack.org/supported_entities/`
- Changelog 2.2.363 of 28 June 2026 — `github.com/data-privacy-stack/presidio`
- Figures for memory, load time and throughput: own measurement, Python 3.12,
  `de_core_news_md`, 1 core / 3 GB RAM
