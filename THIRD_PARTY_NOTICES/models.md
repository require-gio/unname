# Downloadable models

The app ships no models. First-run setup downloads them once and checks every
file against `app/src-tauri/models.lock.json`, which holds the origin, revision,
size and SHA-256 of every file.

| Component | Origin | License |
|---|---|---|
| Name detection (`ner`) | [`urchade/gliner_multi_pii-v1`](https://huggingface.co/urchade/gliner_multi_pii-v1) @ `f334163a5d1dcdbacf365c4527538e3da3ecfb18`, converted to ONNX with `scripts/models/gliner-onnx.py` (embeddings int8); encoder `microsoft/mdeberta-v3-base` | Apache-2.0 (GLiNER), MIT (mDeBERTa) |
| Language pack German (`de`) | [`explosion/spacy-models` `de_core_news_sm` 3.8.0](https://github.com/explosion/spacy-models/releases/tag/de_core_news_sm-3.8.0); the sources of the training data are listed in the model's `LICENSES_SOURCES` | MIT |
| Language pack English (`en`) | [`explosion/spacy-models` `en_core_web_sm` 3.8.0](https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0); sources of the training data in `LICENSES_SOURCES` | MIT |
| Second opinion (`classifier`, optional) | trained by ourselves with `scripts/second_opinion/` on an invented corpus; encoder [`intfloat/multilingual-e5-small`](https://huggingface.co/intfloat/multilingual-e5-small) @ `614241f622f53c4eeff9890bdc4f31cfecc418b3` with a classification head, ONNX int8 | MIT (e5-small and the fine-tuned model) |
| Text recognition (`ocr`) | [`tesseract-ocr/tessdata_best`](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a915945e4c28e237a9b52bc4a8f39a0cec`, `deu` and `eng` | Apache-2.0 |

The Apache-2.0 license text is at `sidecar/packaging/licenses/Apache-2.0.txt`.
Tesseract itself is a program and stays in the signed package (Apache-2.0, see
`THIRD_PARTY_NOTICES.json` in the sidecar bundle). It is built statically
(`scripts/tesseract-portable.py`) and contains Leptonica and its image and
compression libraries; their license texts are in the bundle under
`licenses/tesseract/`, the versions in `BUILD-INFO.json` of the bundle input
artifact.

A package built with `--with-models` (the smoke test) still contains
`de_core_news_lg` 3.8.0 (MIT) as the German language model.
