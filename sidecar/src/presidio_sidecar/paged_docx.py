"""Page-faithful Word view: map engine PDF codes to source text.

The Office engine (``office_engine.py``) splits DOCX into pages; the output is a PDF.
The review view shows those pages as images – using the same page view as for PDFs.
For each word it needs a bounding box and the source-text range that the
segments and highlights refer to. The engine does not know the source text;
therefore we map via the text:

- For each text unit (paragraph, ``docx_units``) we have the text of that view
  page – original source text, and result text after replacements – and for
  each character the source range it originates from.
- Search is performed without whitespace and after NFKC (ligatures, protected
  spaces): line and page breaks from the engine do not interfere.
- Header and footer first, on each page, only in the top or bottom quarter;
  their characters are then excluded. Then the body text in order, each unit
  following the previous.
- Replacement text in the result receives the source range of its position,
  distributed proportionally over its characters – a code ``<<PERSON_…>>``
  therefore covers the whole name, a fabricated two-word name splits it.

What cannot be found (engine splits syllables, field with different value,
missing glyphs) ends up in ``unplaced`` – the view calls it, never silently
incorrect. Words exist per unit only for the first occurrence; headers and
footers of subsequent pages are shown by the page image itself.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from .document_workflow import replacement_spans
from .extraction import ExtractedDocument
from .layout import PDF_SCALES, LayoutError, Utf16Offsets, docx_unit_segments, page_scales
from .processing import ProcessingResult

Side = Literal["original", "result"]

#: Header and footer are in the top or bottom quarter of the page.
_STORY_MARGIN: Final = 0.25
#: Characters that are ignored during comparison.
_IGNORED: Final = frozenset("­​‌‍⁠﻿")


@dataclass(frozen=True, slots=True)
class SideUnit:
    """A text unit as it appears on a view page."""

    text: str
    #: Per character of ``text``: source range ``[lo, hi)`` (Python offsets).
    sources: tuple[tuple[int, int], ...]
    #: Header or footer: may appear on multiple pages.
    story: bool
    #: Source-text range of the unit.
    start: int
    end: int


@dataclass(slots=True)
class _Char:
    page: int
    box: tuple[float, float, float, float]
    line: int
    used: bool = False


@dataclass(slots=True)
class _Stream:
    characters: list[str] = field(default_factory=list)
    chars: list[_Char] = field(default_factory=list)
    #: Running line number across all pages.
    lines: int = 0


def side_units(
    document: ExtractedDocument,
    source_text: str,
    side: Side,
    processing: ProcessingResult | None,
) -> list[SideUnit]:
    """The text units of a view page in source-text order."""

    changes = (
        sorted(replacement_spans(source_text, processing))
        if side == "result" and processing is not None
        else []
    )
    units: list[SideUnit] = []
    cursor = 0
    for segment in docx_unit_segments(document):
        text: list[str] = []
        sources: list[tuple[int, int]] = []
        position = segment.start
        while cursor < len(changes) and changes[cursor][1] <= segment.start:
            cursor += 1
        for start, end, replacement in changes[cursor:]:
            if start >= segment.end:
                break
            if start < position or end > segment.end:
                raise LayoutError("Eine Ersetzung liegt nicht in einer Texteinheit.")
            for offset in range(position, start):
                text.append(source_text[offset])
                sources.append((offset, offset + 1))
            length = len(replacement)
            for k, character in enumerate(replacement):
                text.append(character)
                sources.append(
                    (
                        start + k * (end - start) // length,
                        start + (k + 1) * (end - start) // length,
                    )
                )
            position = end
        for offset in range(position, segment.end):
            text.append(source_text[offset])
            sources.append((offset, offset + 1))
        units.append(
            SideUnit(
                text="".join(text),
                sources=tuple(sources),
                story=segment.source_ref.startswith("part:"),
                start=segment.start,
                end=segment.end,
            )
        )
    return units


def paged_layout(
    pdf: bytes,
    units: Sequence[SideUnit],
    source_text: str,
    *,
    max_image_pixels: int,
) -> dict[str, Any]:
    """Pages, words and unplaced entries for ``LayoutPagedResponse``."""

    import fitz

    try:
        opened = fitz.open(stream=pdf, filetype="pdf")
    except Exception as error:
        raise LayoutError("Das PDF der Office-Engine laesst sich nicht oeffnen.") from error
    try:
        pages: list[dict[str, Any]] = []
        stream = _Stream()
        heights: list[float] = []
        for index in range(opened.page_count):
            page = opened.load_page(index)
            width, height = float(page.rect.width), float(page.rect.height)
            heights.append(height)
            pages.append(
                {
                    "index": index,
                    "width": round(width, 2),
                    "height": round(height, 2),
                    "unit": "pt",
                    "source": "text",
                    "scales": list(
                        page_scales(width, height, PDF_SCALES, max_pixels=max_image_pixels)
                    ),
                }
            )
            _read_page(page, index, stream)
    finally:
        opened.close()

    occurrences: dict[int, list[list[int]]] = {}
    for index, unit in enumerate(units):
        if unit.story:
            occurrences[index] = _find_story(_needle(unit.text)[0], stream, heights)
    body = [i for i, char in enumerate(stream.chars) if not char.used]
    haystack = "".join(stream.characters[i] for i in body)
    cursor = 0
    for index, unit in enumerate(units):
        if unit.story:
            continue
        needle, _ = _needle(unit.text)
        if not needle:
            continue
        found = haystack.find(needle, cursor)
        if found < 0:
            occurrences[index] = []
            continue
        occurrences[index] = [[body[found + k] for k in range(len(needle))]]
        cursor = found + len(needle)

    offsets = Utf16Offsets(source_text)
    words: list[tuple[int, int, list[float | int]]] = []
    unplaced: list[tuple[int, int]] = []
    for index, unit in enumerate(units):
        needle, origin = _needle(unit.text)
        if not needle:
            continue
        found = occurrences.get(index, [])
        if not found:
            unplaced.append((unit.start, unit.end))
            continue
        for start, end, page, box, line in _unit_words(unit, origin, found[0], stream):
            words.append((start, end, [page, *box, start, end, line]))
    words.sort(key=lambda word: (word[0], word[1]))
    return {
        "pages": pages,
        "words": [
            [word[0], *word[1:5], offsets(start), offsets(end), word[7]]
            for start, end, word in words
        ],
        "unplaced": [[offsets(start), offsets(end)] for start, end in unplaced],
    }


def _normalized(character: str) -> str:
    if character in _IGNORED or character.isspace():
        return ""
    return "".join(c for c in unicodedata.normalize("NFKC", character) if not c.isspace())


def _needle(text: str) -> tuple[str, list[int]]:
    """Search text without whitespace; per character the index in the entry text."""

    needle: list[str] = []
    origin: list[int] = []
    for index, character in enumerate(text):
        for normalized in _normalized(character):
            needle.append(normalized)
            origin.append(index)
    return "".join(needle), origin


def _read_page(page: Any, index: int, stream: _Stream) -> None:
    """Characters of a page in text order, boxes in display space."""

    import fitz

    matrix = page.rotation_matrix
    raw = page.get_text("rawdict", flags=fitz.TEXT_MEDIABOX_CLIP)
    for block in raw.get("blocks", []):
        for raw_line in block.get("lines", []):
            line = stream.lines
            stream.lines += 1
            for span in raw_line.get("spans", []):
                for char in span.get("chars", []):
                    normalized = _normalized(char.get("c", ""))
                    if not normalized:
                        continue
                    rect = fitz.Rect(char["bbox"]) * matrix
                    box = (rect.x0, rect.y0, rect.x1, rect.y1)
                    for character in normalized:
                        stream.characters.append(character)
                        stream.chars.append(_Char(page=index, box=box, line=line))


def _find_story(needle: str, stream: _Stream, heights: Sequence[float]) -> list[list[int]]:
    """All occurrences of a header or footer, per page top or bottom."""

    if not needle:
        return []
    text = "".join(stream.characters)
    found: list[list[int]] = []
    start = text.find(needle)
    while start >= 0:
        indices = list(range(start, start + len(needle)))
        chars = [stream.chars[i] for i in indices]
        pages = {char.page for char in chars}
        if len(pages) == 1 and not any(char.used for char in chars):
            height = heights[chars[0].page]
            middle = (chars[0].box[1] + chars[-1].box[3]) / 2
            if middle < height * _STORY_MARGIN or middle > height * (1 - _STORY_MARGIN):
                for char in chars:
                    char.used = True
                found.append(indices)
                start = text.find(needle, start + len(needle))
                continue
        start = text.find(needle, start + 1)
    return found


def _unit_words(
    unit: SideUnit,
    origin: Sequence[int],
    indices: Sequence[int],
    stream: _Stream,
) -> list[tuple[int, int, int, tuple[float, float, float, float], int]]:
    """Words of an occurrence: split on unit whitespace and PDF line breaks."""

    words: list[tuple[int, int, int, tuple[float, float, float, float], int]] = []
    group: list[int] = []

    def close() -> None:
        if not group:
            return
        chars = [stream.chars[indices[k]] for k in group]
        lo = min(unit.sources[origin[k]][0] for k in group)
        hi = max(unit.sources[origin[k]][1] for k in group)
        if hi > lo:
            box = (
                round(min(char.box[0] for char in chars), 2),
                round(min(char.box[1] for char in chars), 2),
                round(max(char.box[2] for char in chars), 2),
                round(max(char.box[3] for char in chars), 2),
            )
            words.append((lo, hi, chars[0].page, box, chars[0].line))
        group.clear()

    for k in range(len(indices)):
        if group:
            previous = group[-1]
            gap = unit.text[origin[previous] + 1 : origin[k]]
            same_line = stream.chars[indices[previous]].line == stream.chars[indices[k]].line
            if any(character.isspace() for character in gap) or not same_line:
                close()
        group.append(k)
    close()
    return words
