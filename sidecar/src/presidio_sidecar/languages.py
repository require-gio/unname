"""Language packs for document languages.

A language pack bundles everything the detection needs for a document language.
It consists of two parts:

* **loadable** (shell, ``models.lock.json``): the small spaCy model for
  sentence splitting and fallback detection (component ``de`` or ``en``).
* **in the signed app**: stopwords and suspicion words
  (``stopwords/<sprache>_*.txt``), function words for language detection,
  rule context words, month names for dates, placeholders (``catalog.PLACEHOLDERS``),
  the locale for generated names, the Tesseract language and threshold calibration.
  This is a few kilobytes of text; loading it separately only requires another
  place where something could be missing.

Countries (numbers and IDs) are deliberately *not* tied to language: an
English contract from Vienna contains Austrian numbers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from .catalog import LANGUAGES

DocumentLanguage = Literal["de", "en"]
Country = Literal["DE", "AT"]

#: Countries with their own recognizers. CH, UK and US will follow.
COUNTRIES: Final[tuple[Country, ...]] = ("DE", "AT")


@dataclass(frozen=True, slots=True)
class LanguagePack:
    code: DocumentLanguage
    #: spaCy packages in preferred order for bundle and development. Loaded
    #: via the shell the small model (``…_sm``) is always used.
    spacy_models: tuple[str, ...]
    tesseract: str
    faker_locale: str
    business_terms_file: str
    sentence_starters_file: str
    #: Frequent short words that appear almost only in this language. The
    #: language detection counts them per line (``detection/language.py``).
    function_words: frozenset[str]
    #: Month names and abbreviations → month; for „3. März 2024" bzw.
    #: „March 3, 2024", which Presidios date rule does not recognize.
    months: Mapping[str, int]
    #: Field labels before customer or case numbers (regular expressions).
    customer_labels: tuple[str, ...]
    case_labels: tuple[str, ...]
    #: Calibration: language-specific NER thresholds per type. Empty means
    #: the base profile thresholds apply unchanged. Values are added after
    #: a corpus measurement (spec §11).
    thresholds: Mapping[str, float]


def _months(entries: Mapping[str, int]) -> Mapping[str, int]:
    return MappingProxyType({name.casefold(): month for name, month in entries.items()})


GERMAN: Final = LanguagePack(
    code="de",
    spacy_models=("de_core_news_lg", "de_core_news_sm"),
    tesseract="deu",
    faker_locale="de_DE",
    business_terms_file="de_business.txt",
    sentence_starters_file="de_sentence_start.txt",
    function_words=frozenset(
        [
            "der",
            "die",
            "das",
            "und",
            "ist",
            "nicht",
            "ein",
            "eine",
            "einen",
            "einem",
            "einer",
            "eines",
            "dem",
            "den",
            "des",
            "mit",
            "für",
            "auf",
            "von",
            "zu",
            "bei",
            "aus",
            "nach",
            "über",
            "unter",
            "wir",
            "sie",
            "ich",
            "du",
            "er",
            "es",
            "ihr",
            "ihre",
            "ihren",
            "ihnen",
            "uns",
            "unser",
            "unsere",
            "sehr",
            "wird",
            "werden",
            "wurde",
            "wurden",
            "sind",
            "sein",
            "haben",
            "hat",
            "habe",
            "hatte",
            "hätte",
            "kann",
            "können",
            "muss",
            "müssen",
            "soll",
            "sollen",
            "darf",
            "dass",
            "oder",
            "aber",
            "auch",
            "noch",
            "nur",
            "schon",
            "wie",
            "wenn",
            "weil",
            "bis",
            "durch",
            "gegen",
            "ohne",
            "zum",
            "zur",
            "vom",
            "beim",
            "im",
            "ins",
            "diese",
            "dieser",
            "dieses",
            "diesem",
            "jede",
            "jeder",
            "alle",
            "mehr",
            "hier",
            "dort",
            "heute",
            "gemäß",
            "sowie",
            "bzw",
            "bitte",
            "vielen",
            "dank",
            "geehrte",
            "geehrter",
            "freundlichen",
            "grüßen",
            "anbei",
            "betreff",
            "ihnen",
            "eines",
            "keine",
            "kein",
            "nichts",
            "sich",
        ]
    ),
    months=_months(
        {
            "Januar": 1,
            "Jänner": 1,
            "Jan": 1,
            "Jän": 1,
            "Februar": 2,
            "Feber": 2,
            "Feb": 2,
            "März": 3,
            "Maerz": 3,
            "Mär": 3,
            "Mrz": 3,
            "April": 4,
            "Apr": 4,
            "Mai": 5,
            "Juni": 6,
            "Jun": 6,
            "Juli": 7,
            "Jul": 7,
            "August": 8,
            "Aug": 8,
            "September": 9,
            "Sept": 9,
            "Sep": 9,
            "Oktober": 10,
            "Okt": 10,
            "November": 11,
            "Nov": 11,
            "Dezember": 12,
            "Dez": 12,
        }
    ),  # fmt: skip
    customer_labels=(r"kunden[ _-]?(?:nummer|nr\.?)", r"debitoren[ _-]?(?:nummer|nr\.?)"),
    case_labels=(
        r"aktenzeichen",
        r"aktenz\.?",
        r"az\.?",
        r"geschaeftszahl",
        r"geschäftszahl",
        r"gz",
    ),
    thresholds=MappingProxyType({}),
)

ENGLISH: Final = LanguagePack(
    code="en",
    spacy_models=("en_core_web_sm",),
    tesseract="eng",
    faker_locale="en_GB",
    business_terms_file="en_business.txt",
    sentence_starters_file="en_sentence_start.txt",
    function_words=frozenset(
        [
            "the",
            "and",
            "is",
            "are",
            "of",
            "to",
            "for",
            "with",
            "on",
            "at",
            "from",
            "by",
            "this",
            "that",
            "these",
            "those",
            "which",
            "who",
            "whom",
            "whose",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "not",
            "no",
            "can",
            "could",
            "should",
            "would",
            "shall",
            "may",
            "might",
            "must",
            "we",
            "you",
            "they",
            "he",
            "she",
            "it",
            "its",
            "our",
            "their",
            "your",
            "my",
            "his",
            "her",
            "them",
            "us",
            "a",
            "there",
            "here",
            "than",
            "then",
            "any",
            "each",
            "every",
            "such",
            "into",
            "upon",
            "about",
            "after",
            "before",
            "between",
            "hereby",
            "herein",
            "thereof",
            "whereas",
            "pursuant",
            "dear",
            "regards",
            "sincerely",
            "kind",
            "please",
            "thank",
            "thanks",
            "yours",
            "if",
            "or",
            "as",
            "what",
            "when",
            "where",
            "were",
        ]
    ),
    months=_months(
        {
            "January": 1,
            "Jan": 1,
            "February": 2,
            "Feb": 2,
            "March": 3,
            "Mar": 3,
            "April": 4,
            "Apr": 4,
            "May": 5,
            "June": 6,
            "Jun": 6,
            "July": 7,
            "Jul": 7,
            "August": 8,
            "Aug": 8,
            "September": 9,
            "Sept": 9,
            "Sep": 9,
            "October": 10,
            "Oct": 10,
            "November": 11,
            "Nov": 11,
            "December": 12,
            "Dec": 12,
        }
    ),  # fmt: skip
    customer_labels=(
        r"customer[ _-]?(?:number|no\.?|id|ref(?:erence)?)",
        r"client[ _-]?(?:id|number|no\.?)",
        r"account[ _-]?(?:number|no\.?)",
    ),
    case_labels=(
        r"case[ _-]?(?:number|no\.?)",
        r"file[ _-]?(?:number|no\.?|ref\.?)",
        r"docket[ _-]?(?:number|no\.?)",
        r"reference[ _-]?(?:number|no\.?)",
        r"(?:our|your)[ _-]?ref\.?",
    ),
    thresholds=MappingProxyType({}),
)

PACKS: Final[Mapping[str, LanguagePack]] = MappingProxyType({"de": GERMAN, "en": ENGLISH})
DEFAULT_DOCUMENT_LANGUAGE: Final = next(iter(PACKS))

if tuple(PACKS) != LANGUAGES:  # pragma: no cover - contract between catalog and packs
    raise RuntimeError("Sprachpakete und Katalog-Sprachen weichen voneinander ab.")


def pack(code: str) -> LanguagePack:
    try:
        return PACKS[code]
    except KeyError as error:
        raise ValueError(f"Unbekannte Dokumentsprache: {code!r}") from error


def packs(codes: Iterable[str] | None = None) -> tuple[LanguagePack, ...]:
    """Packages in catalog order; ``None`` means all."""

    if codes is None:
        return tuple(PACKS.values())
    wanted = set(codes)
    unknown = wanted - set(PACKS)
    if unknown:
        raise ValueError("Unbekannte Dokumentsprache: " + ", ".join(sorted(unknown)))
    return tuple(item for code, item in PACKS.items() if code in wanted)


@dataclass(frozen=True, slots=True)
class DocumentLanguageOptions:
    """Information required by detection about document languages and countries.

    ``languages`` in catalog order; the first is the default for sections whose
    language cannot be determined.
    """

    languages: tuple[str, ...] = LANGUAGES
    per_paragraph: bool = True
    countries: tuple[str, ...] = COUNTRIES

    def __post_init__(self) -> None:
        if not self.languages:
            raise ValueError("Mindestens eine Dokumentsprache ist noetig.")
        if not self.countries:
            raise ValueError("Mindestens ein Land ist noetig.")
        ordered = tuple(item.code for item in packs(self.languages))
        unknown = set(self.countries) - set(COUNTRIES)
        if unknown:
            raise ValueError("Unbekanntes Land: " + ", ".join(sorted(unknown)))
        countries = tuple(code for code in COUNTRIES if code in set(self.countries))
        object.__setattr__(self, "languages", ordered)
        object.__setattr__(self, "countries", countries)

    @property
    def default_language(self) -> str:
        return self.languages[0]

    def fingerprint(self) -> str:
        """Short key for cache (text mode)."""

        return "|".join(
            (",".join(self.languages), "p" if self.per_paragraph else "d", ",".join(self.countries))
        )


__all__ = [
    "COUNTRIES",
    "DEFAULT_DOCUMENT_LANGUAGE",
    "ENGLISH",
    "GERMAN",
    "PACKS",
    "Country",
    "DocumentLanguage",
    "DocumentLanguageOptions",
    "LanguagePack",
    "pack",
    "packs",
]
