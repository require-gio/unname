"""Text units of a DOCX file – one pass for extraction, export, and preview.

A text unit is a paragraph: in body text (``body/p:N``), in a table cell
(``body/t:N/r:R/c:C/p:P``, nested further), or in a header or footer
(``part:word/header1.xml/p:N``). The extraction assembles the source text
from the units, the export finds the paragraph to change via the same
``target_id``, and the preview copy of the review view places a bookmark
at the start of each unit. To ensure all three see the same order and IDs,
there is only this one pass.

Text of a paragraph is the run view of python-docx (runs and runs in
hyperlinks, ``w:tab`` as ``\\t``, ``w:br`` as ``\\n``); the offsets of
the replacements refer to exactly this text.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any

from .models import SegmentKind

_STORY_ATTRIBUTES = (
    "header",
    "first_page_header",
    "even_page_header",
    "footer",
    "first_page_footer",
    "even_page_footer",
)


@dataclass(frozen=True, slots=True)
class DocxUnit:
    """A paragraph in extraction order.

    ``separator`` is the delimiter *within a table* (``\\t`` between cells of
    a row, ``\\n`` otherwise). For paragraphs and the first unit of a table
    it is empty; there the extraction separates with a line break once text
    already precedes it (:meth:`joined_separator`).
    """

    text: str
    target_id: str
    kind: SegmentKind
    paragraph: Any
    separator: str = ""

    def joined_separator(self, has_text: bool) -> str:
        if self.separator:
            return self.separator
        return "\n" if has_text else ""


def docx_paragraph_runs(paragraph: Any) -> tuple[Any, ...]:
    """Runs of a paragraph, including those in hyperlinks – the view for export."""

    runs: list[Any] = []
    for item in paragraph.iter_inner_content():
        nested_runs = getattr(item, "runs", None)
        if nested_runs is None:
            runs.append(item)
        else:
            runs.extend(nested_runs)
    return tuple(runs)


def docx_paragraph_text(paragraph: Any) -> str:
    return "".join(run.text for run in docx_paragraph_runs(paragraph))


def docx_story_containers(document: Any) -> tuple[tuple[str, Any], ...]:
    """Unique header and footer parts with their ID prefix."""

    containers: list[tuple[str, Any]] = []
    seen_parts: set[str] = set()
    for section in document.sections:
        for attribute in _STORY_ATTRIBUTES:
            container = getattr(section, attribute)
            part_name = str(container.part.partname).lstrip("/")
            if part_name in seen_parts:
                continue
            seen_parts.add(part_name)
            containers.append((f"part:{part_name}", container))
    return tuple(containers)


def docx_units(document: Any, *, include_empty: bool = False) -> tuple[DocxUnit, ...]:
    """All text units in extraction order.

    Without ``include_empty`` only paragraphs with text – exactly the segments
    of the extraction. The export also needs the empty ones: a replacement
    can empty a paragraph, and the re-check reads it as empty text.
    Merged cells count once (the first finding wins).
    """

    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    units: list[DocxUnit] = []
    seen_cells: set[object] = set()
    paragraph_index = 0
    table_index = 0
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            paragraph = Paragraph(child, document)
            _append_paragraph(
                units,
                paragraph,
                f"body/p:{paragraph_index}",
                SegmentKind.DOCX_PARAGRAPH,
                include_empty,
            )
            paragraph_index += 1
        elif isinstance(child, CT_Tbl):
            units.extend(
                _table_units(
                    Table(child, document),
                    prefix=f"body/t:{table_index}",
                    seen_cells=seen_cells,
                    include_empty=include_empty,
                )
            )
            table_index += 1

    for prefix, container in docx_story_containers(document):
        for index, paragraph in enumerate(container.paragraphs):
            _append_paragraph(
                units,
                paragraph,
                f"{prefix}/p:{index}",
                SegmentKind.DOCX_PARAGRAPH,
                include_empty,
            )
        for index, table in enumerate(container.tables):
            units.extend(
                _table_units(
                    table,
                    prefix=f"{prefix}/t:{index}",
                    seen_cells=seen_cells,
                    include_empty=include_empty,
                )
            )
    return tuple(units)


def iter_docx_paragraphs(document: Any) -> Iterator[tuple[str, Any]]:
    """``(target_id, paragraph)`` for each paragraph, also empty (Export, Cleanup)."""

    for unit in docx_units(document, include_empty=True):
        yield unit.target_id, unit.paragraph


def _append_paragraph(
    units: list[DocxUnit],
    paragraph: Any,
    target_id: str,
    kind: SegmentKind,
    include_empty: bool,
) -> None:
    text = docx_paragraph_text(paragraph)
    if text or include_empty:
        units.append(DocxUnit(text=text, target_id=target_id, kind=kind, paragraph=paragraph))


def _table_units(
    table: Any,
    *,
    prefix: str,
    seen_cells: set[object],
    include_empty: bool,
) -> list[DocxUnit]:
    """Units of a table: cells row by row, ``\\t`` between cells.

    Empty units (only with ``include_empty``) never carry a delimiter;
    the delimiters depend solely on the units with text.
    """

    units: list[DocxUnit] = []
    table_started = False
    for row_index, row in enumerate(table.rows):
        row_started = False
        for cell_index, cell in enumerate(row.cells):
            # The element itself is kept, not its ``id``: Python may reuse the ID
            # of a shared proxy object.
            cell_element = cell._tc  # noqa: SLF001 - python-docx does not provide a stable ID.
            if cell_element in seen_cells:
                continue
            seen_cells.add(cell_element)
            cell_prefix = f"{prefix}/r:{row_index}/c:{cell_index}"
            cell_units: list[DocxUnit] = []
            cell_has_text = False
            for paragraph_index, paragraph in enumerate(cell.paragraphs):
                text = docx_paragraph_text(paragraph)
                if not text and not include_empty:
                    continue
                cell_units.append(
                    DocxUnit(
                        text=text,
                        target_id=f"{cell_prefix}/p:{paragraph_index}",
                        kind=SegmentKind.DOCX_TABLE_CELL,
                        paragraph=paragraph,
                        separator="\n" if text and cell_has_text else "",
                    )
                )
                cell_has_text = cell_has_text or bool(text)
            for table_index, nested_table in enumerate(cell.tables):
                nested_units = _table_units(
                    nested_table,
                    prefix=f"{cell_prefix}/t:{table_index}",
                    seen_cells=seen_cells,
                    include_empty=include_empty,
                )
                first = _first_with_text(nested_units)
                if first is not None:
                    if cell_has_text:
                        nested_units[first] = replace(nested_units[first], separator="\n")
                    cell_has_text = True
                cell_units.extend(nested_units)

            first = _first_with_text(cell_units)
            if first is not None:
                separator = ""
                if table_started:
                    separator = "\t" if row_started else "\n"
                cell_units[first] = replace(cell_units[first], separator=separator)
                table_started = True
                row_started = True
            units.extend(cell_units)
    return units


#: Notes that Word creates in every ``footnotes.xml``/``endnotes.xml``: the
#: separator lines above the notes. They carry no text from the document.
_SEPARATOR_NOTE_TYPES = frozenset({"separator", "continuationSeparator", "continuationNotice"})
_NOTE_PARTS = frozenset({"word/footnotes.xml", "word/endnotes.xml"})


def docx_has_notes(document: Any) -> bool:
    """Does the file have real footnotes or endnotes (or references to them)?

    Neither extraction nor export reads notes; a file with notes is therefore
    rejected so that no text remains unverified in the output.
    The separator lines that Word creates even without any note do not count.
    """

    from lxml import etree

    for part in document.part.package.parts:
        in_notes = str(part.partname).casefold().lstrip("/") in _NOTE_PARTS
        root = getattr(part, "element", None)
        if root is None and in_notes:
            # python-docx loads note parts as raw bytes, not as XML.
            try:
                root = etree.fromstring(part.blob, etree.XMLParser(resolve_entities=False))
            except etree.XMLSyntaxError:
                return True
        if root is None:
            continue
        for element in root.iter():
            tag = getattr(element, "tag", "")
            name = tag.rpartition("}")[2] if isinstance(tag, str) else ""
            if name in {"footnoteReference", "endnoteReference"}:
                return True
            if in_notes and name in {"footnote", "endnote"}:
                note_type = element.get(f"{tag.rpartition('}')[0]}}}type")
                if note_type not in _SEPARATOR_NOTE_TYPES:
                    return True
    return False


def _first_with_text(units: list[DocxUnit]) -> int | None:
    return next((index for index, unit in enumerate(units) if unit.text), None)


__all__ = [
    "DocxUnit",
    "docx_has_notes",
    "docx_paragraph_runs",
    "docx_paragraph_text",
    "docx_story_containers",
    "docx_units",
    "iter_docx_paragraphs",
]
