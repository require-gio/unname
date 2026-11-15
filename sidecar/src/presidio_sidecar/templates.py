"""Templates as YAML, format v4.

A template bundles its own terms, patterns and optionally a masking
standard under a name – e.g. “Versicherung”, “Kanzlei” – and can be shared
as a file within a team. It replaces the old profiles (v3): what a profile
described about formats, pipeline and thresholds is now in the base profile
(`base_profile.py`); the only thing that still varies between teams is the
template content.

**Name lists are deliberately excluded from a template.** They contain
customer names; a template is a plain-text file that may be emailed or
placed in a shared folder. Lists stay on the device that imported them.

The three existing profiles become example templates, e.g. “Versicherung”
with the example patterns from the mockup. Examples are bundled in the code
and cannot be overwritten; a modified version is saved as a separate template.

The format::

    format: unname-vorlage
    version: 4
    title: Versicherung
    masking:                      # optional
      method: reversible
      style: codes
      categories: [names, addresses, contact, numbers]
    terms:
      - text: Projekt Falke
        type: OTHER
    patterns:
      - name: Polizzennummer
        regex: 'POL-\\d{6,8}'
        type: CUSTOMER_NUMBER
        enabled: true
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import cache
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .catalog import CATEGORIES, STYLES
from .profiles import ProcessingMode, Profile
from .settings_store import MaskingDefaults
from .vocabulary import (
    MAX_PATTERNS,
    MAX_TERMS,
    PatternIn,
    Template,
    Term,
    TermIn,
    TermType,
    VocabularyValidationError,
    validate_patterns,
)

FORMAT: Final = "unname-vorlage"
VERSION: Final = 4
MAX_TEMPLATE_BYTES: Final = 256 * 1024
EXAMPLE_PREFIX: Final = "beispiel-"


class _FileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _TermFile(_FileModel):
    text: str = Field(min_length=1, max_length=200)
    type: TermType


class _PatternFile(_FileModel):
    name: str = Field(min_length=1, max_length=80)
    regex: str = Field(min_length=1, max_length=500)
    type: TermType = "CUSTOMER_NUMBER"
    enabled: bool = True


class TemplateFile(_FileModel):
    format: Literal["unname-vorlage"]
    version: Literal[4]
    title: str = Field(min_length=1, max_length=120)
    masking: MaskingDefaults | None = None
    terms: tuple[_TermFile, ...] = Field(default=(), max_length=MAX_TERMS)
    patterns: tuple[_PatternFile, ...] = Field(default=(), max_length=MAX_PATTERNS)


def is_example(template_id: str) -> bool:
    return template_id.startswith(EXAMPLE_PREFIX)


def render(template: Template) -> str:
    """Render the template as YAML text for saving or sharing."""

    document: dict[str, object] = {"format": FORMAT, "version": VERSION, "title": template.title}
    if template.masking is not None:
        document["masking"] = template.masking.model_dump(mode="json")
    document["terms"] = [{"text": term.text, "type": term.entity_type} for term in template.terms]
    document["patterns"] = [
        {
            "name": pattern.name,
            "regex": pattern.regex,
            "type": pattern.entity_type,
            "enabled": pattern.enabled,
        }
        for pattern in template.patterns
    ]
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=100)


def parse(content: str, *, now: datetime | None = None) -> Template:
    """Parse a template; patterns must pass the same validation as in the UI."""

    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise VocabularyValidationError("template_invalid", "Die Vorlage ist zu gross.")
    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise VocabularyValidationError("template_invalid", "Die Datei ist kein YAML.") from error
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise VocabularyValidationError("template_invalid", "Die Datei ist keine unname-Vorlage.")
    if raw.get("version") != VERSION:
        raise VocabularyValidationError(
            "template_version", "Diese Fassung des Vorlagenformats wird nicht unterstuetzt."
        )
    try:
        parsed = TemplateFile.model_validate(raw)
    except ValidationError as error:
        raise VocabularyValidationError(
            "template_invalid", "Die Vorlage verletzt das Format."
        ) from error
    patterns = validate_patterns(
        [
            PatternIn(name=item.name, regex=item.regex, entity_type=item.type, enabled=item.enabled)
            for item in parsed.patterns
        ]
    )
    terms = _terms(TermIn(text=item.text, entity_type=item.type) for item in parsed.terms)
    return Template(
        id=secrets.token_hex(8),
        title=" ".join(parsed.title.split()),
        terms=terms,
        patterns=patterns,
        masking=parsed.masking,
        created_at=now or datetime.now(UTC),
    )


def file_name(template: Template) -> str:
    safe = "".join(
        character if character.isalnum() or character in "-_ " else "_"
        for character in template.title
    ).strip()
    return f"{safe or 'Vorlage'}.unname-vorlage.yaml"


def example_templates(profiles: Iterable[Profile]) -> tuple[Template, ...]:
    """Create example templates from existing profiles, plus an insurance example."""

    created = datetime(2026, 10, 24, tzinfo=UTC)
    examples = [
        Template(
            id=f"{EXAMPLE_PREFIX}{profile.id}",
            title=profile.title,
            masking=_masking_for(profile),
            created_at=created,
        )
        for profile in profiles
    ]
    examples.append(_insurance_example(created))
    return tuple(sorted(examples, key=lambda item: item.title.casefold()))


@cache
def _insurance_example(created: datetime) -> Template:
    return Template(
        id=f"{EXAMPLE_PREFIX}versicherung",
        title="Versicherung",
        terms=(),
        patterns=validate_patterns(
            [
                PatternIn(
                    id="pol",
                    name="Polizzennummer (Beispiel)",
                    regex=r"POL-\d{6,8}",
                    entity_type="CUSTOMER_NUMBER",
                ),
                PatternIn(
                    id="sch",
                    name="Schadennummer (Beispiel)",
                    regex=r"SCH-\d{4}-\d{5}",
                    entity_type="CASE_NUMBER",
                ),
            ]
        ),
        created_at=created,
    )


def _masking_for(profile: Profile) -> MaskingDefaults:
    """Use a profile's protection level and mode as the default masking settings."""

    level = profile.protection.level
    categories = tuple(category.id for category in CATEGORIES if category.level <= level)
    mode = profile.mode.default
    style_by_mode = {
        ProcessingMode.TOKEN_DETERMINISTIC: "codes",
        ProcessingMode.SURROGATE: "surrogates",
        ProcessingMode.PLACEHOLDER: "placeholder",
        ProcessingMode.MASK: "mask",
        ProcessingMode.REMOVE: "remove",
    }
    style = style_by_mode.get(mode, "codes")
    reversible = profile.vault.reversible and style in STYLES["reversible"]
    if not reversible:
        method = "irreversible"
        style = style if style in STYLES["irreversible"] else "placeholder"
    else:
        method = "reversible"
    return MaskingDefaults(method=method, style=style, categories=categories)


def _terms(items: Iterable[TermIn]) -> tuple[Term, ...]:
    result: list[Term] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(items):
        if len(item.text) < 3:
            raise VocabularyValidationError(
                "term_too_short", "Ein Begriff braucht mindestens 3 Zeichen.", index=index
            )
        key = (item.text.casefold(), item.entity_type)
        if key in seen:
            continue
        seen.add(key)
        result.append(Term(id=secrets.token_hex(6), text=item.text, entity_type=item.entity_type))
    return tuple(result)


__all__ = [
    "EXAMPLE_PREFIX",
    "FORMAT",
    "MAX_TEMPLATE_BYTES",
    "VERSION",
    "TemplateFile",
    "example_templates",
    "file_name",
    "is_example",
    "parse",
    "render",
]
