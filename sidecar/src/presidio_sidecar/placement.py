"""Placement plan: where and how a replacement sits.

When saving, the PDF export removes the original text and then draws the
area and replacement text. *Where* the replacement text fits is decided by
a placement search in the line. The same calculation is needed by the
review view during Apply (dry-run) to overlay the result on the page image
without changing the PDF. To keep view and file consistent there is a
single function for this: :func:`plan_replacements`. The export calls it
after editing, Apply calls it before; both see the same obstacles
(the page words that are not edited).

Typography: The replacement text is left-aligned at the
start of the span, on the baseline of the first affected text span, using
its size, color and font class (``sans``/``serif``/``mono``, normal or
bold → Base-14 ``helv``/``tiro``/``cour`` or ``hebo``/``tibo``/``cobo``).
The spans are read by :func:`page_styles` from the unmodified page – in
Apply as in saving. If no span (images, OCR words) a size from line height,
Helvetica and a baseline at line middle are used.

All geometry here is unrotated – the export also works with
(``set_rotation(0)``). Only the response to the view is rotated into the
display space (:func:`placement_out`).

The module knows no export types; it works on any object that has
``rect``, ``replacement``, ``font_size``, ``fill`` and ``text_color``
(:class:`~presidio_sidecar.exporting.PdfRedaction`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

import fitz

#: Font for replacement text when no reference exists on the page (Base-14 Helvetica).
PDF_REPLACEMENT_FONT: Final = "helv"
#: Font class and weight → Base-14 font used for export. The view uses
#: metrically equivalent web fonts (Arimo, Tinos, Cousine).
BASE14_FONTS: Final[dict[tuple[str, bool], str]] = {
    ("sans", False): "helv",
    ("sans", True): "hebo",
    ("serif", False): "tiro",
    ("serif", True): "tibo",
    ("mono", False): "cour",
    ("mono", True): "cobo",
}
#: Smallest readable font size for a replacement. Codes must mainly be on the
#: text layer (round-trip via a language model); they are readable enough for
#: the eye as is.
PDF_MIN_DRAWN_FONT_SIZE: Final = 2.5
#: Gap between replacement text and the next remaining word on the line.
PDF_TEXT_GAP: Final = 1.5
#: Baseline position without reference: line middle plus this fraction of
#: the font size.
_BASELINE_SHIFT: Final = 0.35
#: Above this luminance an original color on a white area is no longer
#: readable (white UI text, light text on dark logo); the code is then set
#: to black.
_MAX_TEXT_LUMINANCE: Final = 0.6

_BLACK: Final = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class TextStyle:
    """A text span from the unmodified page (``get_text("dict")``), unrotated."""

    rect: tuple[float, float, float, float]
    baseline: float
    size: float
    color: tuple[float, float, float]
    font: str
    bold: bool

    def as_tuple(self) -> tuple[Any, ...]:
        """Compact representation for per-job cache (JSON)."""

        return (*self.rect, self.baseline, self.size, *self.color, self.font, self.bold)

    @classmethod
    def from_tuple(cls, raw: Sequence[Any]) -> TextStyle:
        return cls(
            rect=(float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])),
            baseline=float(raw[4]),
            size=float(raw[5]),
            color=(float(raw[6]), float(raw[7]), float(raw[8])),
            font=str(raw[9]),
            bold=bool(raw[10]),
        )


def page_styles(page: fitz.Page) -> tuple[TextStyle, ...]:
    """The text spans of a page with baseline, size, color and font.

    Must run on the page *before* editing: after that the text is gone.
    Coordinates are unrotated like ``get_text("words")``.
    """

    styles: list[TextStyle] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                if not str(span.get("text", "")).strip():
                    continue
                font, bold = font_class(str(span.get("font", "")), int(span.get("flags", 0)))
                x0, y0, x1, y1 = (float(value) for value in span["bbox"])
                styles.append(
                    TextStyle(
                        rect=(x0, y0, x1, y1),
                        baseline=float(span["origin"][1]),
                        size=float(span["size"]),
                        color=fitz.sRGB_to_pdf(int(span.get("color", 0))),
                        font=font,
                        bold=bold,
                    )
                )
    return tuple(styles)


def font_class(name: str, flags: int) -> tuple[str, bool]:
    """Determine font class (``sans``/``serif``/``mono``) and bold flag from name and flags.

    MuPDF flags (4 = serif, 8 = mono, 16 = bold) are missing for some embedded fonts;
    the name is used as a fallback.
    """

    lower = name.lower()
    if flags & 8 or any(key in lower for key in ("courier", "mono", "consol", "cousine")):
        font = "mono"
    elif "sans" in lower or any(key in lower for key in ("helvetica", "arial", "arimo")):
        font = "sans"
    elif flags & 4 or any(
        key in lower for key in ("times", "serif", "roman", "georgia", "cambria", "tinos")
    ):
        font = "serif"
    else:
        font = "sans"
    bold = bool(flags & 16) or any(key in lower for key in ("bold", "black", "heavy"))
    return font, bold


class Replaceable(Protocol):
    """Interface required by the placement algorithm."""

    @property
    def rect(self) -> tuple[float, float, float, float]: ...

    @property
    def replacement(self) -> str: ...

    @property
    def font_size(self) -> float: ...


@dataclass(frozen=True, slots=True)
class PlannedReplacement:
    """Result of the placement search for a single entry."""

    #: Index of the entry in the input.
    index: int
    #: Filled area: removal box ∪ text box.
    cover: fitz.Rect
    #: Box of the replacement text; ``None`` if no text or no space.
    text_rect: fitz.Rect | None
    #: Font size of the replacement text (0 if no text).
    size: float
    #: Replacement text could not be placed; the area is blackened instead.
    blackened: bool = False
    #: Baseline of the replacement text (unrotated).
    baseline: float = 0.0
    #: Font class ``sans``/``serif``/``mono`` and weight.
    font: str = "sans"
    bold: bool = False
    #: Text color as RGB 0…1.
    color: tuple[float, float, float] = _BLACK

    @property
    def origin(self) -> fitz.Point | None:
        """Start of the baseline where the export inserts the text."""

        if self.text_rect is None:
            return None
        return fitz.Point(self.text_rect.x0, self.baseline)

    @property
    def fontname(self) -> str:
        """Base-14 font used by the export for the replacement text."""

        return BASE14_FONTS[(self.font, self.bold)]


def same_line(word: fitz.Rect, rect: fitz.Rect, *, share: float = 0.5) -> bool:
    overlap = min(word.y1, rect.y1) - max(word.y0, rect.y0)
    return overlap > 0 and overlap >= min(word.height, rect.height) * share


def untouched_words(words: Sequence[fitz.Rect], targets: Sequence[fitz.Rect]) -> tuple[int, ...]:
    """Indices of words that are not part of any entry.

    The export must see the same set of protected words after processing,
    and these words also act as obstacles for placement.
    """

    return tuple(
        index
        for index, box in enumerate(words)
        if not any(
            same_line(box, target) and box.x0 < target.x1 and target.x0 < box.x1
            for target in targets
        )
    )


def plan_replacements(
    bounds: fitz.Rect,
    obstacles: Sequence[fitz.Rect],
    redactions: Sequence[Replaceable],
    styles: Sequence[TextStyle] = (),
) -> tuple[PlannedReplacement, ...]:
    """Areas and replacement texts for a page, in reading order.

    A code is always placed if any space is found: left-aligned at the start
    of the entry in original size; if it does not fit up to the next word,
    it shifts left into free line space, then shrinks (down to
    :data:`PDF_MIN_DRAWN_FONT_SIZE`). If no space is found, the area is
    blackened (``blackened``). Placed texts become obstacles for subsequent
    entries.

    ``bounds`` is the unrotated page (or the image for image pages),
    ``obstacles`` are the words that remain fixed, ``styles`` are the text
    spans of the unchanged page (:func:`page_styles`; empty for images).
    """

    placed: list[fitz.Rect] = [fitz.Rect(rect) for rect in obstacles]
    order = sorted(
        range(len(redactions)),
        key=lambda index: (redactions[index].rect[1], redactions[index].rect[0]),
    )
    plans: list[PlannedReplacement] = []
    for index in order:
        item = redactions[index]
        rect = fitz.Rect(item.rect)
        text_rect: fitz.Rect | None = None
        size = 0.0
        style = _style_for(rect, styles) if item.replacement else None
        font, bold = (style.font, style.bold) if style is not None else ("sans", False)
        if item.replacement:
            others = [
                fitz.Rect(other.rect)
                for position, other in enumerate(redactions)
                if position != index
            ]
            text_rect, size = _slot(
                bounds, item, [*placed, *others], style, BASE14_FONTS[(font, bold)]
            )
        if text_rect is not None:
            plans.append(
                PlannedReplacement(
                    index,
                    rect | text_rect,
                    text_rect,
                    size,
                    baseline=(style.baseline if style is not None else _baseline(text_rect, size)),
                    font=font,
                    bold=bold,
                    color=_text_color(item, style),
                )
            )
            placed.append(text_rect)
        else:
            plans.append(
                PlannedReplacement(index, rect, None, 0.0, blackened=bool(item.replacement))
            )
    return tuple(plans)


def _style_for(rect: fitz.Rect, styles: Sequence[TextStyle]) -> TextStyle | None:
    """First (leftmost) text span on the line that intersects the position."""

    hits = [
        style
        for style in styles
        if same_line(fitz.Rect(style.rect), rect)
        and style.rect[0] < rect.x1
        and rect.x0 < style.rect[2]
    ]
    if not hits:
        return None
    return min(hits, key=lambda style: max(style.rect[0], rect.x0))


def _text_color(item: Replaceable, style: TextStyle | None) -> tuple[float, float, float]:
    color = style.color if style is not None else getattr(item, "text_color", _BLACK)
    red, green, blue = (float(value) for value in color[:3])
    if 0.2126 * red + 0.7152 * green + 0.0722 * blue > _MAX_TEXT_LUMINANCE:
        return _BLACK
    return (red, green, blue)


def _slot(
    bounds: fitz.Rect,
    item: Replaceable,
    obstacles: Sequence[fitz.Rect],
    style: TextStyle | None,
    fontname: str,
) -> tuple[fitz.Rect | None, float]:
    rect = fitz.Rect(item.rect)
    try:
        unit_width = fitz.get_text_length(item.replacement, fontname, 1.0)
    except (ValueError, RuntimeError):
        return None, 0.0
    if unit_width <= 0:
        return None, 0.0
    # Free space on the line: up to the next remaining word or code token.
    left = bounds.x0 + PDF_TEXT_GAP
    right = bounds.x1 - PDF_TEXT_GAP
    center = (rect.x0 + rect.x1) / 2
    for other in obstacles:
        if not same_line(other, rect, share=0.3):
            continue
        if other.x1 <= center:
            left = max(left, other.x1 + PDF_TEXT_GAP)
        else:
            right = min(right, other.x0 - PDF_TEXT_GAP)
    left = min(left, rect.x0)
    right = max(right, rect.x1)
    wanted = style.size if style is not None else min(item.font_size, rect.height * 0.75)
    size = min(wanted, (right - left) / unit_width)
    if size < PDF_MIN_DRAWN_FONT_SIZE:
        return None, 0.0
    width = unit_width * size
    # Left-aligned at the start of the first word (ignoring the margin of the
    # exclusion box); if there isn’t enough room to the right, shift left.
    start = getattr(item, "text_x", None)
    x0 = min(max(rect.x0 if start is None else start, left), right - width)
    return fitz.Rect(x0, rect.y0, x0 + width, rect.y1), size


def _baseline(rect: fitz.Rect, size: float) -> float:
    return (rect.y0 + rect.y1) / 2 + size * _BASELINE_SHIFT


# -- Response to the UI -------------------------------------------------


def placement_out(
    *,
    finding_id: str,
    start: int,
    end: int,
    page: int,
    plans: Sequence[tuple[Replaceable, PlannedReplacement]],
    matrix: fitz.Matrix | None,
    rotation: int,
) -> dict[str, Any]:
    """An entry in ``placements`` (``PlacementOut``) for the UI.

    ``plans`` are the rows for a finding on a page; ``matrix`` rotates
    unrotated coordinates into UI space (``page.rotation_matrix``),
    ``None`` for images.
    """

    def turned(rect: fitz.Rect) -> fitz.Rect:
        return rect * matrix if matrix is not None else rect

    covers: list[list[float]] = []
    text: dict[str, Any] | None = None
    fill = "white"
    for item, plan in plans:
        cover = turned(plan.cover)
        covers.append([_round(cover.x0), _round(cover.y0), _round(cover.x1), _round(cover.y1)])
        if plan.blackened or _is_black(getattr(item, "fill", (1.0, 1.0, 1.0))):
            fill = "black"
        origin = plan.origin
        if text is None and origin is not None and plan.text_rect is not None:
            anchor = origin * matrix if matrix is not None else origin
            text = {
                "value": item.replacement,
                "x": _round(anchor.x),
                "baseline": _round(anchor.y),
                "size": _round(plan.size),
                "font": plan.font,
                "bold": plan.bold,
                "color": _hex(plan.color),
                "width": _round(plan.text_rect.width),
                "rotate": rotation % 360,
            }
    return {
        "finding_id": finding_id,
        "start": start,
        "end": end,
        "page": page,
        "cover": covers,
        "fill": fill,
        "text": text,
    }


def _is_black(color: Sequence[float]) -> bool:
    return all(value <= 0.0 for value in color)


def _hex(color: Sequence[float]) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, value)) * 255):02x}" for value in color[:3])


def _round(value: float) -> float:
    return round(float(value), 2)


__all__ = [
    "BASE14_FONTS",
    "PDF_MIN_DRAWN_FONT_SIZE",
    "PDF_REPLACEMENT_FONT",
    "PDF_TEXT_GAP",
    "PlannedReplacement",
    "Replaceable",
    "TextStyle",
    "font_class",
    "page_styles",
    "placement_out",
    "plan_replacements",
    "same_line",
    "untouched_words",
]
