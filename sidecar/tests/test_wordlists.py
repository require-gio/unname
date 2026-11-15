"""Language selection, custom lists and extension without special cases in the loader."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from presidio_sidecar import languages
from presidio_sidecar.detection import find_name_suspicions, wordlists


def test_german_and_english_have_separate_terms_and_sentence_starters() -> None:
    german = wordlists.load_language_wordlists("de")
    english = wordlists.load_language_wordlists("en")

    assert german.language == "de" and english.language == "en"
    assert "rechnung" in german.business_terms and "invoice" not in german.business_terms
    assert "invoice" in english.business_terms and "rechnung" not in english.business_terms
    assert "bitte" in german.sentence_starters and "please" not in german.sentence_starters
    assert "please" in english.sentence_starters and "bitte" not in english.sentence_starters
    assert wordlists.load_language_wordlists() == german


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize(
    "loader", [wordlists.load_business_terms, wordlists.load_sentence_starters]
)
def test_custom_list_replaces_default_and_missing_list_uses_its_language(
    tmp_path: Path, language: str, loader: Callable[..., frozenset[str]]
) -> None:
    custom = tmp_path / "custom.txt"
    # Example comment line
    custom.write_text("# Kommentar\n  Ｃｕｓｔｏｍ   Term  \n\nCustom Term\n", encoding="utf-8")
    assert loader(custom, language=language) == frozenset({"custom term"})
    # Intentionally empty
    custom.write_text("# Absichtlich leer\n", encoding="utf-8")
    assert loader(custom, language=language) == frozenset()
    custom.unlink()
    assert loader(custom, language=language) == loader(language=language)


def test_unknown_language_does_not_fall_back_to_german_even_with_custom_file(
    tmp_path: Path,
) -> None:
    custom = tmp_path / "custom.txt"
    custom.write_text("Test", encoding="utf-8")
    for loader in (wordlists.load_business_terms, wordlists.load_sentence_starters):
        with pytest.raises(ValueError, match="Unbekannte Dokumentsprache"):
            loader(custom, language="zz")


def test_missing_required_file_is_error_instead_of_german_fallback_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wordlists, "wordlists_directory", lambda: tmp_path)
    for language in languages.PACKS:
        for loader in (wordlists.load_business_terms, wordlists.load_sentence_starters):
            with pytest.raises(FileNotFoundError):
                loader(language=language)
    # Optional lists may specify an explicit fallback.
    assert wordlists.load_wordlist(tmp_path / "optional.txt", fallback=("DEFAULT",)) == {"default"}


def test_new_language_pack_needs_only_registration_and_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    french = replace(
        languages.ENGLISH,
        code="fr",
        business_terms_file="fr_business.txt",
        sentence_starters_file="fr_sentence_start.txt",
    )
    monkeypatch.setattr(languages, "PACKS", {**languages.PACKS, french.code: french})
    monkeypatch.setattr(wordlists, "wordlists_directory", lambda: tmp_path)
    (tmp_path / french.business_terms_file).write_text("Facture\nClient\n", encoding="utf-8")
    (tmp_path / french.sentence_starters_file).write_text("Bonjour\nMerci\n", encoding="utf-8")

    loaded = wordlists.load_language_wordlists("fr")

    assert loaded.language == "fr"
    assert loaded.business_terms == {"facture", "client"}
    assert loaded.sentence_starters == {"bonjour", "merci"}
    assert "rechnung" not in loaded.business_terms
    assert [
        finding.text for finding in find_name_suspicions("Bonjour Jean Dupont", language="fr")
    ] == ["Jean Dupont"]
