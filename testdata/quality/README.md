# Detection quality: corpus and release gate

This directory deliberately separates two classes of data:

- `synthetic-v1/` holds 200 deterministically generated, model-free fixtures per
  starter profile. They contain **no real documents** and only show that the
  corpus contract, the rule/master-data path, the measurement and the CI gate
  work reproducibly.
- A professional acceptance corpus must be imported as `real_pseudonymized`.
  Such documents are currently not in the repository. The synthetic corpus
  therefore does not meet the specification's requirement of 200 real,
  previously pseudonymized documents per profile.

## Privacy and file contract

Every profile has a SHA-256-bound `manifest.json`, a `documents.jsonl` and
separate master data lists. A document contains only:

```json
{"document_id":"doc-0001","text":"…","annotations":[{"entity_type":"PERSON","start":0,"end":8}]}
```

Offsets are Python/Unicode code points and half-open (`[start, end)`). Gold and
prediction match only for the same entity and the exact same span. Predictions
contain no text, no context and no original values. This keeps the measurement
report from creating a second copy of the corpus.

A real corpus is accepted fail-closed only if the manifest attests prior
pseudonymization, a data protection review, a role-based approval and the
absence of a re-identification key. These statements do not replace an actual
review; they make the review versioned and traceable. Original documents and
mapping tables must lie neither in the staging package nor in the repository.

## Importing real data safely

1. Pseudonymize and annotate real documents outside the repository. At least 200
   documents **per starter profile** are needed.
2. For all three profiles, create an import specification following
   `real-import-spec.example.json`. The paths may lie outside the repository.
   `approved_by_role` is a role, not a person's name.
3. Import into a new, immutable version:

```bash
python -m scripts.quality.import_real \
  --spec /secure/staging/path/import.json \
  --output testdata/quality/real-v1
python -m scripts.quality.gate validate \
  --catalog testdata/quality/real-v1/catalog.json \
  --minimum-documents 200 --require-real
```

The import never overwrites an existing version. It first builds in a private
temporary folder, validates hashes, schema, count and offsets, and only then
renames the complete corpus atomically.

## Measuring and gating

The runner creates separate prediction files per profile, with and without master
data. Even for the LLM profile, which has no master data in production, the
`with_master_data` variant is switched on deliberately as a measurement
condition; the `without_master_data` variant corresponds to the profile default
there.

```bash
uv run --project sidecar python -m scripts.quality.run_sidecar \
  --catalog testdata/quality/synthetic-v1/catalog.json \
  --profiles profiles --engine model-free --output /tmp/quality-predictions

python -m scripts.quality.gate evaluate \
  --catalog testdata/quality/synthetic-v1/catalog.json \
  --predictions /tmp/quality-predictions \
  --config testdata/quality/gates/synthetic.json \
  --baseline testdata/quality/baseline.synthetic.json \
  --output /tmp/quality-report.json
```

For the real corpus `--engine profile` is mandatory. The gate requires at least
200 documents per profile, measures micro-aggregated precision, recall, FP and
FN per entity and overall, and allows at most a **0.5 percentage point recall
drop** against the accepted baseline. In addition, the recall lower bounds set
in `gates/real-pseudonymized.json` apply: 95 % with and 85 % without master
data. Changes to these professional thresholds must be reviewed like code.

The first real run naturally has no baseline yet. Its report is versioned as the
baseline only after a professional review; until then the configured gate stays
red on purpose. The CI workflow runs the synthetic live run on every push and
pull request and publishes only the text-free measurement report. Two parallel
mandatory jobs also run the full sidecar suite including Ruff and the Rust suite
including `fmt` and warning-free Clippy. The synthetic job deliberately stays
`model-free`; `--engine profile` is only the documented acceptance path that is
blocked until the real corpus and its professionally accepted baseline exist.
