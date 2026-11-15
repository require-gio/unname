"""Second opinion: corpus, teacher labels, training and evaluation.

The steps run sequentially, each writes a file that the next reads
(see ``README.md`` in this folder):

1. ``korpus``     – synthetic business texts with gold spans (local LLM)
2. ``fragen``     – the real sidecar detection generates questions from them
3. ``lehrer``     – a large local LLM labels each question once
4. ``trainieren`` – a small multilingual encoder learns the labels,
                    exported to ONNX int8
5. ``messen``     – evaluate questions and recall with and without the classifier
"""

#: Fixed classifier labels in the order they are output.
LABELS = ("person", "organization_or_product", "location", "term")
