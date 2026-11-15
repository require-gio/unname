"""Language-specific post-filter wordlists from the central language package registry.

The UTF-8 files are the sole source of words. New languages need neither
conditional logic nor a second copy of their lists in Python.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..languages import DEFAULT_DOCUMENT_LANGUAGE, pack
from ..resources import wordlists_directory


@dataclass(frozen=True, slots=True)
class LanguageWordlists:
    language: str
    business_terms: frozenset[str]
    sentence_starters: frozenset[str]


def normalize_term(value: str) -> str:
    """Normalize: NFKC, collapse whitespace, case-fold in a language-neutral way."""

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def normalize_terms(values: Iterable[str]) -> frozenset[str]:
    return frozenset(normalized for value in values if (normalized := normalize_term(value)))


def load_wordlist(path: Path, *, fallback: Iterable[str] | None = None) -> frozenset[str]:
    """Reads UTF-8; missing files are an error without an explicit fallback.

    Blank lines and comments are ignored. An existing empty file remains
    intentionally empty and is not replaced by defaults.
    """

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        if fallback is None:
            raise
        return normalize_terms(fallback)
    return normalize_terms(
        line for line in lines if line.strip() and not line.lstrip().startswith("#")
    )


def _load_override(path: Path | None, default: Path) -> frozenset[str]:
    if path is not None:
        try:
            return load_wordlist(path)
        except FileNotFoundError:
            pass
    return load_wordlist(default)


def load_business_terms(
    path: Path | None = None, *, language: str = DEFAULT_DOCUMENT_LANGUAGE
) -> frozenset[str]:
    """Business terms of the language; optionally replaced by a dedicated file."""

    selected = pack(language)
    return _load_override(path, wordlists_directory() / selected.business_terms_file)


def load_sentence_starters(
    path: Path | None = None, *, language: str = DEFAULT_DOCUMENT_LANGUAGE
) -> frozenset[str]:
    """Sentence starters for the language; fall back to the default language file if missing."""

    selected = pack(language)
    return _load_override(path, wordlists_directory() / selected.sentence_starters_file)


def load_language_wordlists(language: str = DEFAULT_DOCUMENT_LANGUAGE) -> LanguageWordlists:
    """Both wordlists of a registered language package."""

    return LanguageWordlists(
        language=language,
        business_terms=load_business_terms(language=language),
        sentence_starters=load_sentence_starters(language=language),
    )
