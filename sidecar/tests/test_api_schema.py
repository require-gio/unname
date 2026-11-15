"""Contract test: Pydantic models ↔ checked-in API v2 schema.

The schema file at ``app/src/lib/sidecar/api.schema.json`` is the source for the
TypeScript types ``api.gen.ts``. If a model changes without regenerating the
schema and types, this test fails – the Node side checks the second step with
``node scripts/api-types.mjs --check``.
"""

from __future__ import annotations

import json
from pathlib import Path

from presidio_sidecar.api_schema import build_schema

SCHEMA = Path(__file__).resolve().parents[2] / "app" / "src" / "lib" / "sidecar" / "api.schema.json"


def test_committed_schema_matches_models() -> None:
    committed = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert committed == build_schema(), (
        "api.schema.json ist veraltet. Neu erzeugen mit: node scripts/api-types.mjs"
    )


def test_schema_describes_requests_strictly_and_fields_without_own_titles() -> None:
    definitions = build_schema()["$defs"]
    for name, definition in definitions.items():
        if name.endswith("Request"):
            assert definition.get("additionalProperties") is False, name
        for field in definition.get("properties", {}).values():
            assert "title" not in field
    assert "title" in definitions["CreateEntryRequest"]["properties"]
