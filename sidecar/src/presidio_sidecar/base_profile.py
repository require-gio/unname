"""The internal base profile for the API v2.

The UI no longer knows about profiles. What a profile previously managed
besides protection level, mode, review, and vault – formats and sizes, OCR,
thresholds, suspicion and residual risk checks, export – is defined here
exactly once. Detection always runs with **all** types; which ones are
replaced is decided only by the masking instruction during Apply. This allows
the UI to switch categories without re-detecting.

The base profile is intentionally in code, not as YAML next to the three
example profiles from which the example templates are generated (``templates.py``).
The ``protection``, ``mode``, ``review``, and ``vault`` sections must still
be populated by the schema; API v2 does not read them.
"""

from __future__ import annotations

from functools import cache
from typing import Final

from .processing import ENTITY_MINIMUM_LEVEL
from .profiles import Profile, ProfileConfig

BASE_PROFILE_ID: Final = "grundprofil"


@cache
def base_profile() -> Profile:
    return ProfileConfig.model_validate(
        {
            "id": BASE_PROFILE_ID,
            "title": "Grundprofil",
            "version": "1.0.0",
            "languages": ["de", "en"],
            "input": {
                "allow": ["pdf", "docx", "xlsx", "csv", "txt", "md", "png", "jpg"],
                "max_file_mb": 50,
            },
            "master_data": {
                "enabled": True,
                "persons_file": "master-data/personen.csv",
                "orgs_file": "master-data/firmen.csv",
                "min_length": 3,
                "refresh": "daily",
            },
            "pipeline": {
                "ocr": "auto",
                "ocr_languages": ["deu", "eng"],
                "structured_data": "presidio_structured",
            },
            "execution": {
                "mode": "balanced",
                "nlp_engine": "gliner",
                "spacy_fallback": "de_core_news_lg",
                "gpu": "auto",
                "cpu_workers": "auto",
            },
            "entities": {"built_in": sorted(ENTITY_MINIMUM_LEVEL)},
            "phone_regions": ["DE", "AT"],
            "thresholds": {
                "default": {"auto_accept": 0.92, "needs_review": 0.55},
                # Five-digit numbers are mostly amounts and invoice numbers; as postal
                # codes alone they would cause a flood of questions. The NER recognizes
                # locations.
                "DE_PLZ": {"enabled": False},
                "PERSON": {"needs_review": 0.35},
            },
            "suspect_check": {
                "enabled": True,
                "pattern": "capitalized_bigram",
                "stopwords_file": "stopwords/de_sentence_start.txt",
            },
            "protection": {"level": 3, "level_locked": False},
            "mode": {
                "default": "token_deterministic",
                "user_selectable": True,
                "allowed": [
                    "token_deterministic",
                    "surrogate",
                    "placeholder",
                    "mask",
                    "remove",
                ],
                "confirm_irreversible": False,
            },
            "residual_risk_check": {"enabled": True, "warn_at_classes": 2},
            "policies": {},
            "review": {"required_entities": [], "block_export_on_unreviewed": False},
            "vault": {"reversible": True, "persist_mapping": True},
            "export": {
                "preserve_original": True,
                "write_audit_log": "minimized",
                "pdf_render_check": True,
            },
        }
    )


__all__ = ["BASE_PROFILE_ID", "base_profile"]
