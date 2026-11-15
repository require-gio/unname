# Detection

How unname finds personal data, and how the quality of that is measured. The
reasoning and the exact rules are in [spec.md](spec.md#6-detection-strategy);
this page is the map of the code, the data and the scripts.

## Why four stages

NER alone misses too much: about 78 % recall on German person names for
`de_core_news_lg`, and nothing at all for names inside company names such as
`Tischlerei Josef Brandstetter e.U.`. So detection is a pipeline in which each
stage closes the gap of the one before. It always runs with **all** entity types;
the masking spec decides afterwards which are replaced, so switching the spec
recomputes without detecting again.

| Stage | What | Code |
|---|---|---|
| 0 | **Master data match.** Exact match against the customer, client and supplier lists of the user, as a `PatternRecognizer` with a deny list and score 1.0. Persons and organizations are separate lists. | `registry.py`, `vocabulary.py`, [master-data/README.md](../master-data/README.md) |
| 1 | **Deterministic recognizers.** Regex, context and checksums: IBAN, email, phone (DE/AT), tax and ID numbers, `DE_PLZ`, and so on. | `deterministic_recognizers.py`, `detection/pipeline.py` |
| 2 | **Semantic detection.** NER with GLiNER (ONNX, CPU) or spaCy as a fallback, recall-oriented, followed by a stopword filter for business terms that are misclassified as names. | `detection/ner.py`, `detection/gliner_onnx.py`, `detection/word_classes.py`, [stopwords/README.md](../stopwords/README.md) |
| 3 | **Suspicion check.** Two consecutive capitalized words that no entity covers become a question, minus sentence-initial words. | `detection/suspicion.py` |
| 4 | **Human review.** Questions are numbered; the user masks or keeps. Open questions are masked on save. | UI: `app/src/lib/features/review` |

Between stages 3 and 4 the **second opinion** (below) can settle some questions.

Rules that keep the stages honest:

- **Built-in recognizers with checksum validation win** over custom ones. Every
  custom recognizer needs negative tests with following words (A-11): a greedy
  IBAN regex once swallowed a following surname.
- Overlapping hits are resolved by score, spans are trimmed, and replacements are
  applied from back to front (`detection/spans.py`).
- A hit from the model, a rule, a name list and the suspicion check stay
  separated by origin, so the review can say why a spot was flagged
  (`analysis.py`).

## Languages and countries

Name detection is multilingual. Per language, a **language pack**
(`languages.py`) brings sentence splitting (spaCy, small), the stopword and
sentence-start lists, month names, field labels, placeholders and invented names.
German and English ship. The language is decided per paragraph
(`detection/language.py`), so mixed documents work.

Countries are independent of languages: tax, social security and ID numbers
belong to the country (DE, AT today). An English contract from Vienna contains
Austrian numbers.

To add a language, see "Adding a language" in
[stopwords/README.md](../stopwords/README.md).

## Base profile and templates

The UI has no profiles. The sidecar has one internal **base profile**
(`base_profile.py`: formats, sizes, OCR, thresholds, residual-risk check, export
rules) and **templates** (custom terms, custom patterns, an optional masking
default) that teams share as YAML. The YAML files in [profiles/](../profiles) are
the source of the example templates. Name lists never go into a template. See
[profiles/README.md](../profiles/README.md).

## Master data (name lists)

Name lists are the most effective lever: whoever knows their customers needs no
statistical model for them. They are personal data, so they never enter the
repository; on import they are normalized and stored as AES-256-GCM ciphertext.
A list that exists but cannot be read blocks upload (HTTP 503,
`master_data_unavailable`) instead of silently passing as "no master data". See
[master-data/README.md](../master-data/README.md).

## Second opinion

A small local classifier (ONNX int8, on the CPU) reads every open question about
a name, company or place with some context and picks one of four labels:
`person`, `organization_or_product`, `location`, `term`. It writes no text. What
it may decide alone depends on the policy in the settings: toward protection only
(default), also keep from 95 %, or suggest only. Every decision it makes is shown
under "Resolved by the local AI", can be reversed with one click and is counted
separately in the review report.

The label order is part of the trained model: the model outputs label *indices*,
and a test pins the order (`person, organization_or_product, location, term`).

The classifier is distilled from a large local LLM on an invented corpus; the
gold comes from construction, not from the LLM. The steps, commands and the
measurement are in [scripts/second_opinion/README.md](../scripts/second_opinion/README.md).

## Text recognition

Scans and photos go through OCR: Apple Vision on the Mac (the
[vision-ocr](../vision-ocr/README.md) helper, with tiling, rotation handling and
reading-order reconstruction) and Tesseract on Windows. If a scanned page cannot
be recognized, the whole file is rejected instead of silently leaving that page
unprotected.

## Measuring quality

Three separate instruments:

| Instrument | Measures | Where |
|---|---|---|
| **Detection quality gate** | Precision, recall, FP and FN per entity, with and without master data, against a baseline; at most a 0.5 point recall drop | `scripts/quality/gate.py`, [testdata/quality/README.md](../testdata/quality/README.md) |
| **OCR gate** | OCR quality per engine and macOS major version against a baseline | `scripts/quality/ocr_gate.py`, `acceptance/ocr-mac.json` |
| **Second opinion measurement** | open questions, recall and precision with and without the classifier | `scripts/second_opinion/measure.py`, `acceptance/second-opinion.json` |

The corpus in the repository (`testdata/quality/synthetic-v1`, 200 invented
documents per starter profile) only shows that the harness works. The release
gate for real protection (acceptance criterion A-22) needs at least 200 real,
previously pseudonymized documents per area of use, imported with
`scripts/quality/import_real.py`; those never leave the operator's device. See
[acceptance.md](acceptance.md) and [roadmap.md](roadmap.md).

Regression cases from real documents with replaced data live in
`testdata/regression/`; see [testdata/README.md](../testdata/README.md).
