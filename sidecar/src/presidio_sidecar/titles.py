"""Title without content for the entry history.

An entry should be identifiable without naming a person. Therefore the title
is taken from a fixed list of document types (e.g. “Rechnung”, “Angebot”,
“Vertrag” …) instead of the filename. Anything not on this list never appears
in the title. “Angebot_Hofer.docx” becomes “Angebot”; an unknown filename
becomes “Dokument”.

This approach deliberately uses a whitelist rather than a filter that strips
recognized names from the filename: a filter would need to know every name,
whereas a whitelist simply excludes unknown ones. Detected values are still
checked against the list – a company named “Rechnung GmbH” must not become a
document-type title.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: Keyword (casefold, without umlaut variants) → title per language. The
#: order decides when multiple matches occur: specific before generic
#: (“Mietvertrag” before “Vertrag”).
_DOCUMENT_TYPES: Final[tuple[tuple[tuple[str, ...], str, str], ...]] = (
    (("mietvertrag",), "Mietvertrag", "Tenancy agreement"),
    (("arbeitsvertrag",), "Arbeitsvertrag", "Employment contract"),
    (("kaufvertrag",), "Kaufvertrag", "Purchase agreement"),
    (("vertrag", "contract", "agreement"), "Vertrag", "Contract"),
    (("kostenvoranschlag",), "Kostenvoranschlag", "Cost estimate"),
    (("angebot", "offer", "quotation", "quote"), "Angebot", "Offer"),
    (("lieferschein", "delivery note"), "Lieferschein", "Delivery note"),
    (("gutschrift", "credit note"), "Gutschrift", "Credit note"),
    (("mahnung", "reminder"), "Mahnung", "Reminder"),
    (("rechnung", "invoice"), "Rechnung", "Invoice"),
    (("kontoauszug", "bank statement"), "Kontoauszug", "Bank statement"),
    (
        ("gehaltsabrechnung", "lohnabrechnung", "verdienstabrechnung", "payslip"),
        "Gehaltsabrechnung",
        "Payslip",
    ),
    (("protokoll", "minutes"), "Protokoll", "Minutes"),
    (("bewerbung", "application"), "Bewerbung", "Application"),
    (("lebenslauf", "curriculum vitae", "resume"), "Lebenslauf", "CV"),
    (("kuendigung", "kündigung", "termination"), "Kündigung", "Termination"),
    (("schadensmeldung", "schadenmeldung", "claim"), "Schadensmeldung", "Claim"),
    (("bescheid",), "Bescheid", "Notice"),
    (("arztbrief", "befund", "medical report"), "Arztbrief", "Medical report"),
    (("vollmacht", "power of attorney"), "Vollmacht", "Power of attorney"),
    (("bestellung", "purchase order"), "Bestellung", "Order"),
    (("bericht", "report"), "Bericht", "Report"),
    (("antrag",), "Antrag", "Application form"),
    (("brief", "schreiben", "letter"), "Schreiben", "Letter"),
)

_FALLBACK: Final = {"de": "Dokument", "en": "Document"}
_SCAN: Final = {"de": "Scan", "en": "scan"}
_TEXT: Final = {"de": "Text", "en": "Text"}
_EMAIL: Final = {"de": "E-Mail-Entwurf", "en": "Email draft"}
_MORE: Final = {"de": "{title} + {n} weitere", "en": "{title} + {n} more"}
_SEVERAL: Final = {"de": "{n} Dokumente", "en": "{n} documents"}

_SALUTATION: Final = re.compile(
    r"^\s*(sehr geehrte[rs]?|liebe[rs]?|hallo|guten tag|servus|hi|dear|hello)\b",
    re.IGNORECASE | re.MULTILINE,
)
_CLOSING: Final = re.compile(
    r"^\s*(mit freundlichen gr(ü|ue)(ß|ss)en|viele gr(ü|ue)(ß|ss)e|beste gr(ü|ue)(ß|ss)e|"
    r"liebe gr(ü|ue)(ß|ss)e|freundliche gr(ü|ue)(ß|ss)e|lg|mfg|best regards|kind regards|"
    r"regards|sincerely|cheers)\b",
    re.IGNORECASE | re.MULTILINE,
)

#: Only the start of the document counts for the type; a “Rechnung” hidden in
#: fine print of a contract must not rename it.
_TEXT_WINDOW: Final = 600


@dataclass(frozen=True, slots=True)
class TitleSource:
    filename: str
    text: str = ""
    ocr_used: bool = False


def _fold(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return folded.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")


def _words(value: str) -> str:
    """Word sequence with a single space; filename separators count."""

    return " " + " ".join(re.findall(r"[^\W\d_]+", _fold(value))) + " "


def _document_type(haystack: str, excluded: str, language: str) -> str | None:
    best: tuple[int, int, str] | None = None
    for priority, (keywords, title_de, title_en) in enumerate(_DOCUMENT_TYPES):
        for keyword in keywords:
            needle = _fold(keyword)
            # Compound words count (“Handwerkerrechnung”), but only at word end
            # or as a whole word – “Briefkasten” is not a letter.
            match = re.search(rf"(?:\s|[^\W\d_]){re.escape(needle)}\s", haystack)
            if match is None:
                continue
            if f" {needle} " in excluded:
                continue
            candidate = (match.start(), priority, title_de if language == "de" else title_en)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
    return best[2] if best is not None else None


def _title_for_file(source: TitleSource, excluded: str, language: str) -> str:
    title = _document_type(_words(Path(source.filename).stem), excluded, language)
    if title is None:
        title = _document_type(_words(source.text[:_TEXT_WINDOW]), excluded, language)
    title = title or _FALLBACK[language]
    if source.ocr_used:
        title = f"{title} ({_SCAN[language]})"
    return title


def documents_title(
    sources: Sequence[TitleSource],
    *,
    exclude_values: Iterable[str] = (),
    language: str = "de",
) -> str:
    """Title for a document entry.

    ``exclude_values`` are detected values (names, companies …). A keyword that
    appears as a whole word in any of them is not considered a document type.
    """

    lang = language if language in _FALLBACK else "de"
    excluded = " ".join(_words(value) for value in exclude_values)
    if not sources:
        return _FALLBACK[lang]
    titles = [_title_for_file(source, excluded, lang) for source in sources]
    if len(titles) == 1:
        return titles[0]
    known = [title for title in titles if not title.startswith(_FALLBACK[lang])]
    if not known:
        return _SEVERAL[lang].format(n=len(titles))
    return _MORE[lang].format(title=known[0], n=len(titles) - 1)


def text_title(text: str, *, language: str = "de") -> str:
    """\"E-Mail-Entwurf\" (email draft) with a salutation and a closing, otherwise \"Text\"."""

    lang = language if language in _TEXT else "de"
    if _SALUTATION.search(text) and _CLOSING.search(text):
        return _EMAIL[lang]
    return _TEXT[lang]


__all__ = ["TitleSource", "documents_title", "text_title"]
