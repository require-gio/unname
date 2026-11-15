"""The API v2 JSON schema derived from the Pydantic models (source for ``api.gen.ts``).

Call ``uv run python -m presidio_sidecar.api_schema`` to write the schema
to stdout. ``scripts/api-types.mjs`` saves it to
``app/src/lib/sidecar/api.schema.json`` and generates
TypeScript types from it. ``tests/test_api_schema.py`` fails if the saved
schema no longer matches the models.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pydantic.json_schema import models_json_schema

from .api_models import SCHEMA_MODELS

#: Requests are described by the validation schema (fields with defaults
#: are optional), responses by the serialization schema (everything is present).
_REQUEST_SUFFIX = "Request"


def build_schema() -> dict[str, Any]:
    models = [
        (model, "validation" if _is_request(model) else "serialization") for model in SCHEMA_MODELS
    ]
    keys, schema = models_json_schema(models, ref_template="#/$defs/{model}")  # type: ignore[arg-type]
    definitions = {
        name: {**_tuples(_without_titles(definition)), "title": definition.get("title", name)}
        for name, definition in schema.get("$defs", {}).items()
    }
    root_properties = {
        model.__name__: {"$ref": keys[(model, mode)]["$ref"]}  # type: ignore[index]
        for model, mode in models
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "UnnameApiV2",
        "description": "Typen der Sidecar-API v2 – erzeugt, nicht von Hand bearbeiten.",
        "type": "object",
        "properties": root_properties,
        "required": sorted(root_properties),
        "additionalProperties": False,
        "$defs": dict(sorted(definitions.items())),
    }


def _without_titles(node: Any) -> Any:
    """Removes the field titles that Pydantic appends to each property.

    ``json-schema-to-typescript`` turns each title into a separate type
    (``Id1``, ``Title2`` …). Named types should only be the models.
    """

    return _strip_titles(node, names=False)


_NAMED_CHILDREN = frozenset({"properties", "patternProperties", "$defs"})


def _strip_titles(node: Any, *, names: bool) -> Any:
    # ``names``: The keys of this object are field names, not
    # schema keywords – a ``title`` field must remain.
    if isinstance(node, dict):
        return {
            key: _strip_titles(value, names=not names and key in _NAMED_CHILDREN)
            for key, value in node.items()
            if names or key != "title"
        }
    if isinstance(node, list):
        return [_strip_titles(item, names=False) for item in node]
    return node


def _tuples(node: Any) -> Any:
    """Writes tuples (``prefixItems``) into the format known to the TS generator.

    ``json-schema-to-typescript`` reads tuples only as ``items: [...]`` (up
    to Draft 2019-09); it turns ``prefixItems`` into ``unknown``.
    """

    if isinstance(node, dict):
        converted = {key: _tuples(value) for key, value in node.items()}
        if "prefixItems" in converted:
            converted["items"] = converted.pop("prefixItems")
        return converted
    if isinstance(node, list):
        return [_tuples(item) for item in node]
    return node


def _is_request(model: type) -> bool:
    return model.__name__.endswith(_REQUEST_SUFFIX)


def render() -> str:
    return json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    sys.stdout.write(render())


if __name__ == "__main__":
    main()
