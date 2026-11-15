# Second opinion: corpus, distillation, measurement

The second opinion is a small classifier that reads every open question of
detection (a suspicion or an uncertainly recognized name, company, place) with
a bit of context and picks one of four fixed labels: `person`,
`organization_or_product`, `location`, `term`. It writes no text. At runtime
only ONNX int8 runs, on the CPU
(`sidecar/src/presidio_sidecar/detection/second_opinion.py`).

Every step runs locally. Language models are asked only through Ollama on
`127.0.0.1`; `ollama.py` rejects models with `:cloud` in the name.

| Step | Module | reads | writes |
|---|---|---|---|
| 1 Corpus | `corpus.py` | – | `testdata/zweitmeinung/korpus.jsonl` |
| 2 Questions | `questions.py` | corpus | `testdata/zweitmeinung/fragen.jsonl` |
| 3 Teacher | `teacher.py` | corpus, questions | `testdata/zweitmeinung/lehrer.jsonl` |
| 4 Training | `train.py` | questions, teacher | `sidecar/models/second-opinion/` |
| 5 Measurement | `measure.py` | questions, teacher, model | `acceptance/second-opinion.json` |

The data folder `testdata/zweitmeinung/` is data and keeps its German name.

```bash
uv run --project sidecar python -m scripts.second_opinion.corpus --model gemma4:26b-nvfp4
uv run --project sidecar python -m scripts.second_opinion.questions
uv run --project sidecar python -m scripts.second_opinion.teacher --model qwen3.8:27b-mlx
uv run --project sidecar --group modellbau --with onnx python -m scripts.second_opinion.train
uv run --project sidecar python -m scripts.second_opinion.measure
python3 scripts/models/modelle.py zweitmeinung --classifier sidecar/models/second-opinion
```

## Gold by construction

The LLM writes the texts (letters, contracts, invoices, emails, minutes … in
German and English) but **no names**: it gets placeholders (`{P1}`, `{F1}`,
`{X1}`, `{O1}`) with a short description, and `corpus.py` inserts invented
values (Faker de_AT/de_DE/en_GB/en_US, rare surnames and surnames that are
ordinary words, companies with and without a legal form, made-up names for
products, places from DE/AT/UK/US). A span therefore belongs to the masked set
exactly when it comes from a placeholder. Letting the LLM mark freely was
unusable: it marked "Zirbenholz" (stone pine wood) as a company.

The corpus is invented and contains no real people. It is also the training set;
an independent measurement on real, previously pseudonymized documents belongs
to acceptance criterion A-22 ([docs/spec.md](../../docs/spec.md#11-acceptance-criteria)).

## Distillation

The teacher (a large local LLM) sees the whole document and the list of
questions, not the gold. Its labels are the training targets of the student
(`intfloat/multilingual-e5-small` with a classification head). Names, companies
and places that were recognized with confidence join at half weight with their
gold label. The split is fixed per document ID: 65 % training, 10 % calibration
(temperature), 25 % test.

## What the measurement says

`measure.py` compares on the test part against the gold: open questions, recall
and precision on saving when everything open stays open ("when in doubt, mask" –
the worst case), and how often the AI kept something itself that deserved
masking. The result is in `acceptance/second-opinion.json`; it is explained in
[docs/acceptance.md](../../docs/acceptance.md#second-opinion).
