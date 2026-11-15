"""Create a preview copy of a DOCX file for the review view.

The view renders Word files in a WebView using docx-preview, but never the
original file; instead, it uses this copy:

- **Anchor:** At the start of each text unit in the extraction, there is a
  bookmark ``unn_u{n}`` (``w:bookmarkStart`` + ``w:bookmarkEnd``); docx-preview
  converts this to ``<span id="unn_u{n}">``. ``n`` counts the units in the
  order defined by :func:`docx_units` – the same order from which the
  extraction derives the source text and the export derives its targets.
- **Sanitized like the export:** No hyperlinks or external relationships
  (no network access during rendering), no metadata, no field codes
  (``_sanitize_docx_document``). The copy thus shows the structure that the
  saved file also has.

The copy is created only in memory. If the file no longer matches the
extraction (different units, different text), no copy is created; the view
then falls back to "Text" instead of showing replacements in the wrong place.
"""

from __future__ import annotations

from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from typing import Final

from docx.oxml.ns import qn
from lxml import etree

from .extraction.docx_units import docx_units
from .layout import LayoutError

#: Prefix for the bookmarks; a leading ``_`` would make it a hidden bookmark in Word.
ANCHOR_PREFIX: Final = "unn_u"


def anchor_name(index: int) -> str:
    return f"{ANCHOR_PREFIX}{index}"


def build_docx_preview(source: Path | str, expected_texts: Sequence[str] | None = None) -> bytes:
    """Preview copy with anchors as DOCX bytes.

    ``expected_texts`` are the texts of the extraction segments in their
    order. If the file deviates from this, the function raises
    :class:`LayoutError`.
    """

    from docx import Document

    from .exporting import (
        ExportError,
        ExportValidationError,
        _docx_has_revision_markup,
        _sanitize_docx_document,
        _validate_supported_docx_content,
    )

    try:
        document = Document(Path(source))
        _validate_supported_docx_content(document)
        if _docx_has_revision_markup(document):
            raise LayoutError("Die DOCX-Datei enthaelt Aenderungsverfolgung.")
        units = docx_units(document)
    except (ExportError, ExportValidationError) as error:
        raise LayoutError(str(error)) from error
    except LayoutError:
        raise
    except Exception as error:
        raise LayoutError("Die DOCX-Datei laesst sich nicht mehr oeffnen.") from error
    if expected_texts is not None and [unit.text for unit in units] != list(expected_texts):
        raise LayoutError("Die DOCX-Datei passt nicht zur Extraktion.")

    first_id = _next_bookmark_id(document)
    for index, unit in enumerate(units):
        _insert_anchor(unit.paragraph._p, anchor_name(index), first_id + index)  # noqa: SLF001

    _sanitize_docx_document(document)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _next_bookmark_id(document: object) -> int:
    """First free bookmark ID across all parts (header, footer, body)."""

    highest = -1
    attribute = qn("w:id")
    for part in document.part.package.parts:  # type: ignore[attr-defined]
        root = getattr(part, "element", None)
        if root is None:
            continue
        for element in root.iter(qn("w:bookmarkStart"), qn("w:bookmarkEnd")):
            raw = element.get(attribute, "")
            if raw.lstrip("-").isdigit():
                highest = max(highest, int(raw))
    return highest + 1


def _insert_anchor(paragraph: etree._Element, name: str, bookmark_id: int) -> None:
    """Lesezeichen als erste Kinder des Absatzes, hinter ``w:pPr``."""

    start = etree.Element(qn("w:bookmarkStart"))
    start.set(qn("w:id"), str(bookmark_id))
    start.set(qn("w:name"), name)
    end = etree.Element(qn("w:bookmarkEnd"))
    end.set(qn("w:id"), str(bookmark_id))
    properties = paragraph.find(qn("w:pPr"))
    position = 0 if properties is None else paragraph.index(properties) + 1
    paragraph.insert(position, start)
    paragraph.insert(position + 1, end)


__all__ = ["ANCHOR_PREFIX", "anchor_name", "build_docx_preview"]
