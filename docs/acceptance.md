# Acceptance

The acceptance criteria are section 11 of [spec.md](spec.md). This page explains
how they are proven and records the measurements that go with them.

The machine-readable evidence lives in [`acceptance/spec-11.json`](../acceptance/spec-11.json).
It assigns every criterion concrete test files and test names;
`sidecar/tests/test_acceptance_manifest.py` checks that every reference exists
and that the manifest and the specification name the same criteria. The evidence
distinguishes repository coverage from a real-world test that was actually run
externally; a configured CI stage is not yet a passed acceptance protocol.

Run everything with `node scripts/check.mjs`.

| ID | Criterion | State | Direct core evidence |
|---|---|---|---|
| A-01 | Name lists A/B (Brandstetter) | automated | `test_master_data_reference_case_is_confirmed_only_with_registry` |
| A-02 | Suspicion as a question (Wachtelhuber) | automated | `test_reference_case_wachtelhuber_brothers_detected_as_suspicion` |
| A-03 | Fail-closed without a partial result | automated | `test_findings_before_completion_not_output` |
| A-04 | Five styles, seven modes in the sidecar | automated | `test_each_ui_style_returns_its_result` |
| A-05 | PDF neither selectable nor extractable | automated | `test_pdf_remove_leaves_no_markable_or_extractable_text` |
| A-06 | Categories and exceptions | automated | `test_categories_extend_to_location_and_date` |
| A-07 | "Anonymous" only per spec 7.3 | automated | `test_anonymize_with_residual_risk_is_not_anonymous` |
| A-08 | Residual risk after blackout | automated | `test_residual_risk_after_redaction_finds_date_amount_and_article_number` |
| A-09 | Way back after a restart, tolerant | automated | `test_code_round_trip_survives_llm_change_and_restart` |
| A-10 | Three manual interventions | automated | `test_all_three_interventions_end_in_result_and_audit_log` |
| A-11 | Recognizer word boundaries | automated | `test_word_boundary_matrix_covers_every_custom_recognizer` |
| A-12 | No outgoing network traffic | **external evidence missing** | `starts without external network and matches visual reference` |
| A-13 | Keyboard, focus, contrast, axe | automated | `Review works fully with the keyboard` |
| A-14 | 8 GB / 2 cores, Windows 11 | **external evidence missing** | `test_runtime_matrix_scales_both_pools` |
| A-15 | Mappings only on save | automated | `test_toggling_ten_times_then_discarding_leaves_table_unchanged` |
| A-16 | When in doubt, mask | automated | `test_open_question_is_masked_on_save` |
| A-17 | Restoring from two entries | automated | `test_answer_with_codes_from_two_entries_and_one_unknown` |
| A-18 | History without names, retention | automated | `test_titles_never_contain_a_name_from_the_filename` |
| A-19 | Document languages and countries | automated | `test_mixed_document_is_separated_paragraph_by_paragraph` |
| A-20 | Models with checksums | automated | `tampered_file_is_discarded` |
| A-21 | Second opinion | automated | `test_policy_protect_decides_only_toward_protection` |
| A-22 | Recall on the real corpus | **external evidence missing** | `apply_gate` (harness exists) |
| A-23 | Performance: 50 pages, 9 files, 1,000 codes | automated | `test_fifty_page_pdf_is_detected_redacted_and_rendered_checked` |
| A-24 | Bilingual, coach marks | automated | `English is complete` |

A-25 to A-29 (layout fidelity, view = file, Word and Excel, scans stay scans)
are in the manifest with their evidence. The German Playwright test titles in
the last column are the locators the manifest points at.

Of the criteria in the table, 21 of 24 are fully automated in the repository.
For A-12, A-14 and A-22 the checkmark may only be set after the artifact
described in the JSON file has been filed (a capture, a workflow run on the limit
device, a content-free measurement report on the real corpus). What is left to do
is in [roadmap.md](roadmap.md).

## Performance

[`performance.json`](../acceptance/performance.json) measures, with the real
models of the development environment, a 50-page PDF, an entry with 9 files and a
file with 1,000 codes – detection, apply and saving. Created with
`scripts/performance.py`; the texts are invented, and only times, counts and
device data are stored. Result of 26 October 2026 on an Apple Silicon Mac (10
cores, 64 GB, GLiNER on MPS): 50 pages with 1,050 spots detected in 16.6 s and
saved, including redaction and render check, in 4.3 s; 9 files in 1.4 s; 1,000
codes detected in 12.3 s, apply 0.05 s, saving 0.06 s. The measurement on the
2-core / 8 GB device belongs to A-14.

## LLM round trip of the code format

[`llm-round-trip.json`](../acceptance/llm-round-trip.json) measures how many codes
come back unchanged, or recognizable to the sidecar's tolerant matching, after a
round through a language model, per format and model. It decided the code format.
Created with `scripts/llm-round-trip.py` against local Ollama models; the texts
are invented and contain only codes, and the raw answers are not in the
repository. Result of 24 October 2026: `<<TYPE_xxxxxxxx>>` stays.
`llm-round-trip-scan.json` does the same for the text layer of a saved scan.

## Second opinion

[`second-opinion.json`](../acceptance/second-opinion.json) measures open
questions, recall and precision with and without the classifier, per policy and
language, plus the accuracy and calibration of student and teacher. Created with
`scripts/second_opinion/measure.py` on the test part of the invented corpus in
`testdata/zweitmeinung/` (never trained on, never calibrated on), against the
gold from the construction. Result of 26 October 2026: "toward protection only"
lowers the open questions by 41 %, "also keep from 95 %" by 55 %, and recall
stays 1.0 in both cases. The measurement on real, previously pseudonymized
documents belongs to A-22.

## Other measurements

| File | What it measures |
|---|---|
| [`drafts.json`](../acceptance/drafts.json) | disk use per format as a factor of the original, load time of an entry with 10 files, memory after opening 20 entries in a row (`scripts/draft-measurement.py`) |
| [`ocr-mac.json`](../acceptance/ocr-mac.json) | the OCR gate report for Apple Vision (`scripts/quality/ocr_gate.py`) |
| [`office-conversion.json`](../acceptance/office-conversion.json) | how long the DOCX → PDF conversion of the page-faithful Word view takes, for documents of 10, 50 and 150 pages (`scripts/office-conversion-measurement.py`) |
