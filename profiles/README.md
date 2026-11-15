# Profiles → example templates

Since specification 4.0 the UI knows no profiles (see
[docs/spec.md](../docs/spec.md#8-base-profile-and-templates)). Two things take
their place:

- the **base profile** in the sidecar
  (`sidecar/src/presidio_sidecar/base_profile.py`): formats and sizes, OCR,
  thresholds, suspicion and residual-risk checks, export rules – exactly once,
  for everything;
- **templates** (YAML, format v4, `templates.py`): custom terms, custom patterns
  and optionally a masking default under one name, to be passed on in a team.
  Name lists never belong in a template.

The three YAML files here remain the **source of the example templates**
("Invoice and receipt (DE/AT)", "Law firm and consulting", "LLM upload"; plus
"Insurance" in the code). Their masking default is derived from the profile's
mode and protection level. Examples cannot be overwritten; a changed version is
saved as a template of its own.

The loader in `sidecar/src/presidio_sidecar/profiles.py` still validates every
file completely with Pydantic and rejects unknown fields. A typo in a security
rule therefore shows up at start instead of silently activating a default.

## Template format v4

```yaml
format: unname-vorlage        # the identifier stays German: it is part of the file format
version: 4
title: Insurance
masking:                      # optional
  method: reversible          # reversible | irreversible
  style: codes                # codes | surrogates | placeholder | mask | remove
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

A pattern that would match almost any text or is too expensive to evaluate is
rejected on import (`vocabulary.py`).

## Bundled profiles (source of the example templates)

| File | Purpose | Formats | Way back |
|---|---|---|---|
| `de-at-rechnung-v3.yaml` | Invoice and receipt (DE/AT) | PDF, DOCX, XLSX, CSV, PNG, JPG | no |
| `kanzlei-beratung-v1.yaml` | Law firm and consulting | PDF, DOCX, TXT | yes |
| `llm-upload-v1.yaml` | Cleaned text for external LLM input | TXT, Markdown, JSON, CSV | yes, mandatory |

## Layers of the schema

- Presidio: `languages`, `execution`, `entities`, `thresholds`, `phone_regions`.
  `to_presidio_analyzer_config()` derives the native base configuration from
  them; `presidio_entities` is the entity filter for `AnalyzerEngine.analyze`.
- Document pipeline: `input`, `pipeline`.
- App policy: `master_data`, `protection`, `mode`, `policies`,
  `residual_risk_check`, `review`, `vault`, `export`.

The spelling `ORG` from the specification example stays in the YAML. At the
Presidio and processing boundaries it is normalized, as documented, to
`ORGANIZATION`.

Phone regions, entity-specific thresholds and the profile-bound suspicion word
list are applied per job. Unsupported execution variants are rejected in a
controlled way instead of silently falling back to global defaults.

For CSV, `structured_data: presidio_structured` uses the local
`presidio-structured` reader for a tabular view. The actual hit pipeline also
works on the offset-faithful raw text, so review spans and format-faithful
export point exactly back at the source file.

## Usage in the sidecar

```python
from presidio_sidecar.base_profile import base_profile
from presidio_sidecar.profiles import load_profiles, profile_by_id
from presidio_sidecar.templates import example_templates

# The base profile checks every submitted file (extension and byte limit).
base_profile().validate_upload("input.md", size_bytes)

# The profiles here become example templates.
profiles = load_profiles("/path/to/profiles-folder")
templates = example_templates(profiles)
```

Without an explicit path, `load_profiles()` uses this folder in the development
tree. A bundle can set the path through `PRESIDIO_PROFILES_DIR`.

The size limit `max_file_mb` is evaluated as MiB (`1024 * 1024` bytes). The limit
itself is allowed; the first byte above it is rejected.
`review.block_export_on_unreviewed` is no longer evaluated by API v2: open
questions are masked on save ("when in doubt, mask", spec 9.3).

## Security invariants

- `mode.default` must be contained in `mode.allowed`.
- With `user_selectable: false`, `allowed` may only contain the default mode.
- Policies accept only the seven processing modes or `inherit`.
- `vault.reversible` and `vault.persist_mapping` are set together.
- Profiles with PDF input must enable `export.pdf_render_check`.
- `export.preserve_original` is always `true`.
- Duplicate profile IDs prevent the whole catalog from loading.
