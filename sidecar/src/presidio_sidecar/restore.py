"""Restore text and files with source per code (spec §6, implementation).

A KI response contains the codes that were stored or copied into the shared
translation table – often slightly altered. Matching is therefore tolerant
but never guesses:

* Detects ``<<PERSON_ba903855>>`` and variants that unambiguously map back to a
  single code: missing, simple or alternative brackets (``<PERSON_ba903855>``,
  ``«PERSON_ba903855»``, ``[PERSON_ba903855]``, Markdown-escaped
  ``\\<\\<…\\>\\>``), different case and spaces inside the brackets.
* Anything that would require a guess – a different type name or a missing
  part of the digest – is reported as *unknown*.
* Without brackets a string counts as a code only if its type is one of the
  known entity types. ``build_12345678`` in a code example therefore remains
  plain text.

For each replaced span the result records the source: from this entry
(“here”), from another entry in the history (with its title, which never
contains a name) or from an earlier, now deleted entry.

All processing happens in memory. Responses are not persisted; a restored
file lives only in the sidecar’s memory until the shell writes it to the
location chosen by the user. We count only that a restore occurred (on the
entry) and when a code was last used (in the table) – both without storing
content.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Literal

from .catalog import CATEGORY_BY_ENTITY
from .entries import EntryNotFoundError
from .restored_files import RestoredFile
from .saving import _split_name
from .vault import TokenRecord, TokenVault
from .workflow import Upload, WorkflowError

if TYPE_CHECKING:
    from .services import PhaseOneServices

SpanStatus = Literal["restored", "unknown"]
SpanKind = Literal["code", "surrogate"]
Origin = Literal["here", "entry", "earlier"]

#: Entity types that count as code even without brackets.
KNOWN_ENTITY_TYPES: Final = frozenset(CATEGORY_BY_ENTITY)
#: Length of the digest part in the code (``TOKEN_HEX_LENGTH`` in the vault).
DIGEST_LENGTH: Final = 8

_OPEN = r"\\<\\<|<<|\\<|<|«|‹|\[\[|\["
_CLOSE = r"\\>\\>|>>|\\>|>|»|›|\]\]|\]"
# The type ends as early as needed: ``CUSTOMER_NUMBER_9d03b7aa`` has type
# ``CUSTOMER_NUMBER``. The digest may be longer or shorter than eight characters –
# then the code is damaged and will be reported.
_CODE: Final = re.compile(
    rf"""
    (?P<open>{_OPEN})?
    (?P<lead>[ \t]*)
    (?<!\w)
    (?P<entity>[A-Za-z][A-Za-z0-9_]*?)_(?P<digest>[0-9A-Fa-f]{{4,16}})
    (?!\w)
    (?P<trail>[ \t]*)
    (?P<close>{_CLOSE})?
    """,
    re.VERBOSE,
)

#: File formats whose codes can be replaced in-place (spec §6).
FILE_FORMATS: Final = {
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".csv": "csv",
}
#: Suffix added to the new file name, localized.
NAME_SUFFIX: Final = {"de": "_mit_Namen", "en": "_with_names"}
#: Maximum preview length for a file (characters).
PREVIEW_LIMIT: Final = 200_000

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class RestoreError(WorkflowError):
    """A response or file could not be safely restored."""


# -- Matching ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """A span that looks like a code (Python offsets in the text)."""

    start: int
    end: int
    token: str
    entity_type: str
    #: The representation differs from the canonical code (brackets, case, spaces).
    tolerant: bool
    #: Digest has eight characters – only then can the code be identified.
    complete: bool


def candidates(text: str) -> list[Candidate]:
    """All spans that qualify as codes, in reading order."""

    found: list[Candidate] = []
    for match in _CODE.finditer(text):
        opened, closed = match.group("open"), match.group("close")
        lead, trail = match.group("lead"), match.group("trail")
        # Spaces after a bracket count only if the opposite side also has one.
        # “a < PERSON_…” remains a comparison.
        has_open = bool(opened) and (bool(closed) or not lead)
        has_close = bool(closed) and (bool(opened) or not trail)
        entity = match.group("entity").upper()
        digest = match.group("digest").lower()
        complete = len(digest) == DIGEST_LENGTH
        bracketed = has_open and has_close
        if not (bracketed or (complete and entity in KNOWN_ENTITY_TYPES)):
            continue
        start = match.start("open") if has_open else match.start("entity")
        end = match.end("close") if has_close else match.end("digest")
        token = f"<<{entity}_{digest}>>"
        found.append(
            Candidate(
                start=start,
                end=end,
                token=token,
                entity_type=entity,
                tolerant=text[start:end] != token,
                complete=complete,
            )
        )
    return found


@dataclass(frozen=True, slots=True)
class Span:
    """A placed or unknown location.

    ``start``/``end`` count in the output, ``source_start``/``source_end``
    refer to the original text (Python offsets). An unknown location leaves
    both pairs unchanged.
    """

    start: int
    end: int
    source_start: int
    source_end: int
    status: SpanStatus
    kind: SpanKind
    entity_type: str
    tolerant: bool
    token: str = field(repr=False)
    origin: Origin | None = None
    entry_id: str | None = None


@dataclass(frozen=True, slots=True)
class Restoration:
    text: str = field(repr=False)
    spans: tuple[Span, ...]

    @property
    def restored_tokens(self) -> frozenset[str]:
        return frozenset(span.token for span in self.spans if span.status == "restored")


def _hit(
    status: SpanStatus,
    kind: SpanKind,
    entity_type: str,
    tolerant: bool,
    token: str,
    origin: Origin | None = None,
    entry_id: str | None = None,
) -> Span:
    return Span(
        start=0,
        end=0,
        source_start=0,
        source_end=0,
        status=status,
        kind=kind,
        entity_type=entity_type,
        tolerant=tolerant,
        token=token,
        origin=origin,
        entry_id=entry_id,
    )


class Restorer:
    """Replaces codes (and optionally pseudonyms) with a vault state.

    Two-stage so a large document only reads the vault once: ``prepare`` gathers
    all shortcuts from the texts in one pass; ``restore`` then processes each
    text.
    """

    def __init__(
        self,
        vault: TokenVault,
        *,
        entry_id: str | None = None,
        entry_titles: Mapping[str, str] | None = None,
        surrogates: bool = False,
    ) -> None:
        self._vault = vault
        self._titles = dict(entry_titles or {})
        self._entry_id = entry_id if entry_id in self._titles else None
        self._records: dict[str, TokenRecord] = {}
        self._surrogate_tokens: dict[str, str] = {}
        self._surrogate_pattern: re.Pattern[str] | None = None
        if surrogates:
            index = vault.surrogate_index()
            self._surrogate_tokens = dict(index)
            if index:
                self._surrogate_pattern = re.compile(
                    r"(?<!\w)(?:"
                    + "|".join(re.escape(surrogate) for surrogate, _ in index)
                    + r")(?!\w)"
                )

    @property
    def entry_id(self) -> str | None:
        return self._entry_id

    @property
    def titles(self) -> Mapping[str, str]:
        return self._titles

    def prepare(self, texts: Iterable[str]) -> None:
        tokens: set[str] = set()
        for text in texts:
            tokens.update(item.token for item in candidates(text) if item.complete)
            if self._surrogate_pattern is not None:
                tokens.update(
                    self._surrogate_tokens[match.group(0)]
                    for match in self._surrogate_pattern.finditer(text)
                )
        missing = tokens - set(self._records)
        if missing:
            self._records.update(self._vault.lookup(missing))

    def known(self, text: str) -> int:
        """Number of known codes still present after ``prepare``."""

        return sum(1 for item in candidates(text) if item.token in self._records)

    def restore(self, text: str) -> Restoration:
        self.prepare((text,))
        hits: list[tuple[int, int, str, Span]] = []
        cursor = 0
        for item in candidates(text):
            hits.extend(self._surrogates(text, cursor, item.start))
            record = self._records.get(item.token) if item.complete else None
            if record is None:
                hits.append(
                    (
                        item.start,
                        item.end,
                        text[item.start : item.end],
                        _hit("unknown", "code", item.entity_type, item.tolerant, item.token),
                    )
                )
            else:
                origin, entry_id = self._origin(record)
                hits.append(
                    (
                        item.start,
                        item.end,
                        record.canonical,
                        _hit(
                            "restored",
                            "code",
                            record.entity_type,
                            item.tolerant,
                            item.token,
                            origin,
                            entry_id,
                        ),
                    )
                )
            cursor = item.end
        hits.extend(self._surrogates(text, cursor, len(text)))

        parts: list[str] = []
        spans: list[Span] = []
        position = 0
        length = 0
        for source_start, source_end, replacement, hit in hits:
            parts.append(text[position:source_start])
            length += source_start - position
            parts.append(replacement)
            spans.append(
                replace(
                    hit,
                    start=length,
                    end=length + len(replacement),
                    source_start=source_start,
                    source_end=source_end,
                )
            )
            length += len(replacement)
            position = source_end
        parts.append(text[position:])
        return Restoration(text="".join(parts), spans=tuple(spans))

    def _surrogates(self, text: str, start: int, end: int) -> list[tuple[int, int, str, Span]]:
        pattern = self._surrogate_pattern
        if pattern is None or end <= start:
            return []
        hits: list[tuple[int, int, str, Span]] = []
        for match in pattern.finditer(text, start, end):
            token = self._surrogate_tokens[match.group(0)]
            record = self._records.get(token)
            if record is None:
                continue
            origin, entry_id = self._origin(record)
            hits.append(
                (
                    match.start(),
                    match.end(),
                    record.canonical,
                    _hit(
                        "restored", "surrogate", record.entity_type, False, token, origin, entry_id
                    ),
                )
            )
        return hits

    def _origin(self, record: TokenRecord) -> tuple[Origin, str | None]:
        entries = [entry_id for entry_id, _ in record.origins]
        if self._entry_id is not None and self._entry_id in entries:
            return "here", self._entry_id
        # Return the most recent entry that is still present in the history.
        for entry_id in reversed(entries):
            if entry_id in self._titles:
                return "entry", entry_id
        return "earlier", None


def summarize(spans: Iterable[Span], titles: Mapping[str, str]) -> dict[str, Any]:
    """Count format like “5 here · 1 from »Mietvertrag (Scan)« · 1 unknown”."""

    spans = tuple(spans)
    restored = [span for span in spans if span.status == "restored"]
    unknown = [span for span in spans if span.status == "unknown"]
    by_entry = Counter(span.entry_id for span in restored if span.origin == "entry")
    return {
        "counts": {
            "restored": len(restored),
            "here": sum(1 for span in restored if span.origin == "here"),
            "other": sum(by_entry.values()),
            "earlier": sum(1 for span in restored if span.origin == "earlier"),
            "unknown": len(unknown),
            "unknown_unique": len({span.token for span in unknown}),
            "tolerant": sum(1 for span in restored if span.tolerant),
        },
        "origins": [
            {"entry_id": entry_id, "title": titles[entry_id], "count": count}
            for entry_id, count in sorted(
                by_entry.items(), key=lambda item: (-item[1], titles[str(item[0])])
            )
            if entry_id is not None
        ],
    }


# -- Offsets at the interface ----------------------------------------------


def utf16_offsets(text: str) -> Callable[[int], int]:
    """Convert a Python string offset to a UTF-16 offset, prepared in a single pass.

    If the text contains only BMP characters the offsets are identical; otherwise
    a table is built that counts surrogate pairs instead of re-encoding the text
    for each position.
    """

    if all(ord(character) <= 0xFFFF for character in text):
        return lambda offset: offset
    table = [0]
    for character in text:
        table.append(table[-1] + (2 if ord(character) > 0xFFFF else 1))
    return table.__getitem__


def spans_out(text: str, spans: Iterable[Span]) -> list[dict[str, Any]]:
    convert = utf16_offsets(text)
    return [
        {
            "start": convert(span.start),
            "end": convert(span.end),
            "status": span.status,
            "kind": span.kind,
            "entity_type": span.entity_type,
            "tolerant": span.tolerant,
            "origin": span.origin,
            "entry_id": span.entry_id,
        }
        for span in spans
    ]


# -- Text ----------------------------------------------------------------------


def restore_text(services: PhaseOneServices, request: Any) -> dict[str, Any]:
    """``POST /restore/text``: translate answer back, do not store.

    With ``record`` (“Copy”, “Save as file”) the entry counts a restore and the table
    records the code usage.
    """

    text: str = request.text
    if len(text.encode("utf-8")) > services.settings.max_text_bytes:
        raise RestoreError("too_large", "Die Antwort ist zu gross.")
    restorer = _restorer(services, request.entry_id, surrogates=request.surrogates)
    result = restorer.restore(text)
    recorded = False
    if request.record:
        recorded = _record(services, restorer.entry_id, result.restored_tokens)
    return {
        "text": result.text,
        "spans": spans_out(result.text, result.spans),
        **summarize(result.spans, restorer.titles),
        "recorded": recorded,
    }


def _restorer(services: PhaseOneServices, entry_id: str | None, *, surrogates: bool) -> Restorer:
    # Missing entries are not errors: restore works for all entries, only “from here” is omitted.
    titles = {entry.id: entry.title for entry in services.entries().list()}
    return Restorer(
        services.vault(),
        entry_id=entry_id,
        entry_titles=titles,
        surrogates=surrogates,
    )


def _record(services: PhaseOneServices, entry_id: str | None, tokens: frozenset[str]) -> bool:
    now = datetime.now(UTC)
    services.vault().touch(tokens, used_at=now)
    if entry_id is None:
        return False
    try:
        services.entries().update(
            entry_id,
            lambda entry: entry.model_copy(
                update={"restores": entry.restores + 1, "restored_at": now}
            ),
        )
    except EntryNotFoundError:
        return False
    return True


# -- Files -------------------------------------------------------------------


@dataclass(slots=True)
class _Unit:
    """A text fragment from the file: a paragraph (DOCX) or a cell (XLSX)."""

    text: str
    #: Write the result back into the file; ``False`` if this cannot be done safely.
    apply: Callable[[Restoration], bool]
    preview: bool = True
    result: Restoration | None = None


def restore_file(
    services: PhaseOneServices,
    upload: Upload,
    *,
    entry_id: str | None,
    surrogates: bool,
    max_bytes: int,
) -> dict[str, Any]:
    """``POST /restore/file``: replace AI codes in a file.

    Formatting is preserved: replacements occur in text runs (DOCX) or cells (XLSX);
    Markdown and plain text stay byte-identical except for the replaced spots.
    A new file ``<name>_mit_Namen.<ext>`` is created; the original AI file remains unchanged.
    """

    stem, extension = _split_name(upload.name)
    file_format = FILE_FORMATS.get(extension.casefold())
    if file_format is None:
        raise RestoreError(
            "unsupported_format",
            "Zurueckuebersetzen geht fuer Word, Excel, Markdown und Text.",
        )
    if len(upload.content) > max_bytes:
        raise RestoreError("too_large", "Die Datei ist zu gross.")
    if not upload.content:
        raise RestoreError("empty", "Die Datei ist leer.")

    restorer = _restorer(services, entry_id, surrogates=surrogates)
    if file_format == "docx":
        content, units, unreplaced = _restore_docx(upload.content, restorer)
    elif file_format == "xlsx":
        content, units, unreplaced = _restore_xlsx(upload.content, restorer)
    else:
        content, units, unreplaced = _restore_plain(upload.content, restorer)

    spans = [span for unit in units if unit.result for span in unit.result.spans]
    preview_text, preview_spans, truncated = _preview(units)
    summary = summarize(spans, restorer.titles)
    output = None
    if summary["counts"]["restored"] > 0:
        language = services.settings_store().get().general.language
        suffix = NAME_SUFFIX.get(language, NAME_SUFFIX["de"])
        stored = services.restored_files().put(
            name=f"{stem}{suffix}{extension}",
            format=file_format,
            content=content,
            entry_id=restorer.entry_id,
            tokens=frozenset(
                token for unit in units if unit.result for token in unit.result.restored_tokens
            ),
        )
        output = {
            "id": stored.id,
            "name": stored.name,
            "format": stored.format,
            "sha256": stored.sha256,
            "size_bytes": len(stored.content),
        }
    return {
        "source": {
            "name": f"{stem}{extension}",
            "format": file_format,
            "size_bytes": len(upload.content),
        },
        "output": output,
        "preview": {
            "text": preview_text,
            "spans": spans_out(preview_text, preview_spans),
            "truncated": truncated,
        },
        **summary,
        "unreplaced": unreplaced,
    }


def fetch_restored_file(services: PhaseOneServices, file_id: str) -> RestoredFile:
    """The shell fetches the file for saving – this counts as usage."""

    item = services.restored_files().get(file_id)
    if item is None:
        raise RestoreError("not_found", "Die zurueckuebersetzte Datei ist nicht mehr da.")
    if services.restored_files().mark_recorded(file_id):
        _record(services, item.entry_id, item.tokens)
    return item


def discard_restored_file(services: PhaseOneServices, file_id: str) -> None:
    services.restored_files().discard(file_id)


def _apply_units(units: list[_Unit], restorer: Restorer) -> None:
    restorer.prepare(unit.text for unit in units)
    for unit in units:
        result = restorer.restore(unit.text)
        unit.result = result
        if any(span.status == "restored" for span in result.spans) and not unit.apply(result):
            # Keep as-is; its codes are considered non-replaceable.
            unit.result = Restoration(text=unit.text, spans=())


def _preview(units: list[_Unit]) -> tuple[str, list[Span], bool]:
    parts: list[str] = []
    spans: list[Span] = []
    length = 0
    truncated = False
    for unit in units:
        if not unit.preview:
            continue
        text = unit.result.text if unit.result else unit.text
        if length + len(text) > PREVIEW_LIMIT:
            truncated = True
            break
        if parts:
            parts.append("\n")
            length += 1
        for span in unit.result.spans if unit.result else ():
            spans.append(replace(span, start=length + span.start, end=length + span.end))
        parts.append(text)
        length += len(text)
    return "".join(parts), spans, truncated


# -- Markdown and text ---------------------------------------------------------

_BOMS: Final = (
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)


def _restore_plain(data: bytes, restorer: Restorer) -> tuple[bytes, list[_Unit], int]:
    bom, encoding = next(
        ((mark, name) for mark, name in _BOMS if data.startswith(mark)), (b"", "utf-8")
    )
    try:
        text = data[len(bom) :].decode(encoding)
    except UnicodeDecodeError as error:
        raise RestoreError("encoding", "Die Datei ist kein Text in UTF-8.") from error
    holder: dict[str, str] = {}

    def apply(result: Restoration) -> bool:
        holder["text"] = result.text
        return True

    unit = _Unit(text=text, apply=apply)
    _apply_units([unit], restorer)
    content = bom + holder["text"].encode(encoding) if "text" in holder else data
    return content, [unit], 0


# -- Word ----------------------------------------------------------------------


#: What may appear alongside text in a text run. Anything else (images,
#: fields, objects) would be lost when the run text is replaced.
_PLAIN_RUN_CHILDREN: Final = frozenset(
    {"rPr", "t", "tab", "br", "cr", "noBreakHyphen", "softHyphen", "lastRenderedPageBreak"}
)


def _docx_parts(document: Any) -> list[Any]:
    """First the main text in reading order (including tables and text boxes),
    then headers/footers, footnotes, comments."""

    return [document.part] + sorted(
        (
            part
            for part in document.part.package.iter_parts()
            if part is not document.part and getattr(part, "element", None) is not None
        ),
        key=lambda part: str(part.partname),
    )


def _restore_docx(data: bytes, restorer: Restorer) -> tuple[bytes, list[_Unit], int]:
    # Loaded here: python-docx is only needed when a Word file is restored.
    from docx import Document
    from docx.text.paragraph import Paragraph

    from .exporting import _paragraph_runs, _paragraph_text, _replace_docx_runs

    try:
        document = Document(io.BytesIO(data))
    except Exception as error:  # python-docx reports defects inconsistently
        raise RestoreError("invalid_file", "Die Word-Datei ist ungueltig.") from error

    def runs_are_plain(paragraph: Any, start: int, end: int) -> bool:
        cursor = 0
        for run in _paragraph_runs(paragraph):
            run_end = cursor + len(run.text)
            if start < run_end and end > cursor:
                children = {child.tag.rpartition("}")[2] for child in run._r}  # noqa: SLF001
                if not children <= _PLAIN_RUN_CHILDREN:
                    return False
            cursor = run_end
        return True

    units: list[_Unit] = []
    paragraphs: list[Any] = []
    parts = _docx_parts(document)
    for part in parts:
        for element in part.element.iter(f"{_W}p"):
            paragraph = Paragraph(element, None)

            def apply(result: Restoration, paragraph: Any = paragraph) -> bool:
                restored = [span for span in result.spans if span.status == "restored"]
                if not all(
                    runs_are_plain(paragraph, span.source_start, span.source_end)
                    for span in restored
                ):
                    return False
                # Process from back to front so earlier offsets stay correct.
                for span in reversed(restored):
                    _replace_docx_runs(
                        paragraph,
                        span.source_start,
                        span.source_end,
                        result.text[span.start : span.end],
                    )
                return True

            units.append(
                _Unit(
                    text=_paragraph_text(paragraph),
                    apply=apply,
                    preview=part is document.part,
                )
            )
            paragraphs.append(paragraph)

    _apply_units(units, restorer)
    expected = [unit.result.text if unit.result else unit.text for unit in units]
    if [_paragraph_text(paragraph) for paragraph in paragraphs] != expected:
        raise RestoreError("invalid_file", "Ein Absatz liess sich nicht verlustfrei ersetzen.")
    unreplaced = _remaining_codes([part.element for part in parts], restorer)
    if expected == [unit.text for unit in units]:
        return data, units, unreplaced

    buffer = io.BytesIO()
    document.save(buffer)
    content = buffer.getvalue()
    # Verify: saved file contains exactly the expected paragraphs.
    reopened = Document(io.BytesIO(content))
    actual = [
        _paragraph_text(Paragraph(element, None))
        for part in _docx_parts(reopened)
        for element in part.element.iter(f"{_W}p")
    ]
    if actual != expected:
        raise RestoreError("invalid_file", "Die Word-Datei liess sich nicht nachpruefen.")
    return content, units, unreplaced


def _remaining_codes(roots: list[Any], restorer: Restorer) -> int:
    """Codes that remain in the file after replacement.

    Content controls, change tracking, fields and runs with images cannot be
    safely replaced; the UI reports their count instead of hiding them.
    """

    texts = [
        "".join(
            node.text or ""
            for node in paragraph.iter(f"{_W}t")
            if next(node.iterancestors(f"{_W}p"), None) is paragraph
        )
        for root in roots
        for paragraph in root.iter(f"{_W}p")
    ]
    restorer.prepare(texts)
    return sum(restorer.known(text) for text in texts)


# -- Excel ---------------------------------------------------------------------


def _restore_xlsx(data: bytes, restorer: Restorer) -> tuple[bytes, list[_Unit], int]:
    from openpyxl import load_workbook

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [member.filename.casefold() for member in archive.infolist()]
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise RestoreError("invalid_file", "Die Excel-Datei ist ungueltig.") from error
    # openpyxl does not write back charts or images. A file that loses them cannot be restored.
    if any(name.startswith(("xl/charts/", "xl/media/", "xl/drawings/")) for name in names):
        raise RestoreError(
            "unsupported_content",
            "Excel-Dateien mit Diagrammen oder Bildern lassen sich nicht unveraendert schreiben.",
        )
    try:
        workbook = load_workbook(io.BytesIO(data), data_only=False, keep_links=False)
    except Exception as error:  # openpyxl reports defects inconsistently
        raise RestoreError("invalid_file", "Die Excel-Datei ist ungueltig.") from error

    units: list[_Unit] = []
    cells: list[tuple[str, str]] = []
    try:
        titles = [sheet.title for sheet in workbook.worksheets]
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if not isinstance(cell.value, str) or cell.data_type == "f":
                        continue

                    def apply(result: Restoration, cell: Any = cell) -> bool:
                        cell.value = result.text
                        # Text starting with "=" stays as text.
                        cell.data_type = "s"
                        return True

                    units.append(_Unit(text=cell.value, apply=apply))
                    cells.append((sheet.title, cell.coordinate))
        _apply_units(units, restorer)
        # Sheet names stay unchanged (max 31 chars); any code in them counts as non-replaceable.
        restorer.prepare(titles)
        unreplaced = sum(restorer.known(title) for title in titles)
        changed = [
            (unit, cell)
            for unit, cell in zip(units, cells, strict=True)
            if unit.result and unit.result.text != unit.text
        ]
        if not changed:
            return data, units, unreplaced
        buffer = io.BytesIO()
        workbook.save(buffer)
    finally:
        workbook.close()
    content = buffer.getvalue()
    reopened = load_workbook(io.BytesIO(content), data_only=False, keep_links=False)
    try:
        for unit, (title, coordinate) in changed:
            if reopened[title][coordinate].value != unit.result.text:  # type: ignore[union-attr]
                raise RestoreError("invalid_file", "Die Excel-Datei liess sich nicht nachpruefen.")
    finally:
        reopened.close()
    return content, units, unreplaced


__all__ = [
    "FILE_FORMATS",
    "KNOWN_ENTITY_TYPES",
    "Candidate",
    "RestoreError",
    "Restoration",
    "Restorer",
    "Span",
    "candidates",
    "discard_restored_file",
    "fetch_restored_file",
    "restore_file",
    "restore_text",
    "summarize",
    "utf16_offsets",
]
