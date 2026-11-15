# Test data

Test corpora, negative tests and reference documents. The measurement harness,
the explicitly synthetic CI smoke test and the import structure for the still
missing real, previously pseudonymized corpus are documented under
[`quality/`](quality/README.md).

## Regression cases

[`regression/`](regression/) holds extracted texts of real documents in which
person and company data have been replaced; line structure and number shapes
stay unchanged, because detection depends on exactly those.

- `lieferschein-moebelhaus.txt` — a delivery note with an invoice (October
  2026): phone numbers after "Tel:", article and order numbers, staircase/door
  in the address, phrases like "Ihr Online Team". Tested in
  `sidecar/tests/test_regression_delivery_note.py`.
- `abrechnungsexport-cloud.tsv` — a usage export of a cloud platform (October
  2026, an XLSX table as TSV, account and workspace IDs replaced): quantities
  with many decimals, timestamps, ISO dates. Apart from the dates there is
  nothing to mask. The test builds an XLSX from it again; tested in
  `sidecar/tests/test_regression_billing_export.py`.
- An invoice with a scanned page and a text page (October 2026): page 1 is a
  pure page image, page 2 a booking sheet with a text layer. Without text
  recognition the whole file is rejected; with it, every page contributes its
  part. The test builds the fixture itself, with invented values; tested in
  `sidecar/tests/test_regression_invoice_scan.py`.

The file names and the document text are German data and stay as they are.

## Layout corpus

[`layout/`](layout/) holds synthetic documents for the layout-faithful review
view: a delivery note replica with white tax text, the same page rotated
(90/180/270) and with a CropBox offset, a scan with and without `/Rotate`, a
photo with EXIF orientation 6, a screenshot, a Word letter and an Excel and a
CSV customer list. All values are invented.

Generated with `uv run --project sidecar python scripts/layout-corpus.py`; with
`--smoke` the fixtures of the Playwright probe are also created under
`app/tests/smoke/fixtures/layout/`. The sidecar tests
(`sidecar/tests/test_layout_map.py`) generate the corpus fresh on every run.
