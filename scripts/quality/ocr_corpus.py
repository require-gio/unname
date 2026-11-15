"""Synthetic corpus for the OCR quality gate.

Deterministic: each page is generated from fixed seeds. Rendering is done word-by-word
with PIL, so the glyph box for every word is known. Ground truth consists of
words, boxes, lines and PII words (names, addresses, IBAN, phone, email, UID).

Fonts: the Base-14 fonts from MuPDF (same as in the export) and the OFL fonts
used by the app (``app/node_modules/@fontsource``; install once with
``npm ci --prefix app``).

Coverage: letters, two-column letters and tables in 5–14 pt, 150, 200 and
300 dpi, skew up to 2°, JPEG noise, photos with EXIF orientation,
rotated pages (``/Rotate`` 90/180/270) and footers in 6 pt. Also a code-sheet
corpus for reading codes in 6–12 pt.
"""

from __future__ import annotations

import io
import math
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

VERSION = 1
REPO = Path(__file__).resolve().parents[2]
FONTSOURCE = REPO / "app" / "node_modules" / "@fontsource"

#: Base-14 (MuPDF) and OFL (App); name → source.
BASE14 = ("helv", "hebo", "tiro", "cour")
OFL = ("carlito", "arimo", "tinos", "cousine")
FONTS = BASE14 + OFL
SIZES_PT = (5, 6, 7, 8, 9, 10, 11, 12, 14)
DPIS = (150, 200, 300)
AUGMENTS = ("none", "skew", "jpeg", "rot90", "exif", "rot180", "none", "rot270")
LAYOUTS = ("brief", "zweispaltig", "tabelle")
PAGES = 24
A4_PT = (595, 842)

FIRST_NAMES = ("Martina", "Jonas", "Erika", "Özlem", "Wojciech", "Thi Lan", "Björn", "Agnieszka")
NACHNAMEN = (
    "Brettschneider",
    "Ebersberger",
    "Musterfrau",
    "Yılmaz",
    "Wojciechowski",
    "Nguyen",
    "Lüdenscheidt",
    "Kowalczyk",
)
STREETS = ("Mustergasse 12/1/30", "Bahnhofstraße 7a", "Am Hang 3", "Schloßallee 101")
LOCATIONS = ("1110 Wien", "79098 Freiburg", "8010 Graz", "10115 Berlin")
IBANS = (
    "AT61 1904 3002 3457 3201",
    "DE89 3704 0044 0532 0130 00",
    "AT48 3200 0000 1234 5864",
    "DE02 1203 0000 0000 2020 51",
)
TELEFONE = ("+43 660 1234567", "0761 201-4393", "050123/154-100", "+49 30 12345678")
UIDS = ("ATU13585627", "DE123456789", "ATU62728109", "DE987654321")
FILLER_TEXT = (
    "vielen Dank fuer Ihre Bestellung. Die Lieferung erfolgt in den naechsten Tagen.",
    "Bitte pruefen Sie die Angaben und melden Sie Abweichungen innerhalb von zwei Wochen.",
    "Die Rechnung ist ohne Abzug innerhalb von 14 Tagen nach Erhalt faellig.",
    "Fuer Rueckfragen stehen wir Ihnen gerne zur Verfuegung.",
)


@dataclass(frozen=True, slots=True)
class TruthWord:
    text: str
    #: Glyph box in pixels of the final image (left, top, right, bottom).
    box: tuple[int, int, int, int]
    line: int
    #: ``NAME``, ``ADDRESS``, ``IBAN``, ``PHONE``, ``EMAIL``, ``UID`` or ``None``.
    pii: str | None = None


@dataclass(slots=True)
class Page:
    id: str
    layout: str
    font: str
    size_pt: int
    dpi: int
    augment: str
    image: Image.Image
    words: list[TruthWord] = field(default_factory=list)

    @property
    def upright(self) -> bool:
        """No skew and no rotation: only there the “Nachbarzeile” is unambiguous."""

        return self.augment in {"none", "jpeg", "exif"}


@dataclass(frozen=True, slots=True)
class CodeSheet:
    id: str
    font: str
    size_pt: int
    image: Image.Image
    codes: tuple[tuple[str, tuple[int, int, int, int]], ...]


@lru_cache(maxsize=64)
def font(name: str, size_px: int) -> ImageFont.FreeTypeFont:
    if name in BASE14:
        return ImageFont.truetype(io.BytesIO(fitz.Font(name).buffer), size_px)
    path = FONTSOURCE / name / "files" / f"{name}-latin-400-normal.woff"
    if not path.is_file():
        raise SystemExit(f"OFL-Schrift fehlt: {path}. Einmalig `npm ci --prefix app` ausfuehren.")
    return ImageFont.truetype(str(path), size_px)


class _Canvas:
    """Render text word by word and record each glyph box."""

    def __init__(self, dpi: int, font_name: str) -> None:
        self.dpi = dpi
        self.font_name = font_name
        width, height = (round(value * dpi / 72) for value in A4_PT)
        self.image = Image.new("RGB", (width, height), (255, 255, 255))
        self.draw = ImageDraw.Draw(self.image)
        self.words: list[TruthWord] = []
        self.line = 0

    def px(self, pt: float) -> int:
        return round(pt * self.dpi / 72)

    def text(
        self,
        x_pt: float,
        y_pt: float,
        parts: list[tuple[str, str | None]],
        size_pt: float,
        *,
        font_name: str | None = None,
        color: tuple[int, int, int] = (0, 0, 0),
    ) -> float:
        """A line at (x, y) baseline in pt; returns the end position in pt."""

        face = font(font_name or self.font_name, max(4, self.px(size_pt)))
        x = self.px(x_pt)
        y = self.px(y_pt)
        self.line += 1
        space = face.getlength(" ")
        for chunk, pii in parts:
            for word in chunk.split():
                box = self.draw.textbbox((x, y), word, font=face, anchor="ls")
                self.draw.text((x, y), word, font=face, fill=color, anchor="ls")
                self.words.append(TruthWord(word, tuple(int(v) for v in box), self.line, pii))
                x += face.getlength(word) + space
        return x * 72 / self.dpi

    def wrapped(
        self,
        x_pt: float,
        y_pt: float,
        width_pt: float,
        parts: list[tuple[str, str | None]],
        size_pt: float,
    ) -> float:
        """Flow text with wrapping; returns the baseline of the next line."""

        face = font(self.font_name, max(4, self.px(size_pt)))
        tokens = [(word, pii) for chunk, pii in parts for word in chunk.split()]
        line: list[tuple[str, str | None]] = []
        leading = size_pt * 1.35
        for token in tokens:
            candidate = " ".join(word for word, _ in [*line, token])
            if line and face.getlength(candidate) > self.px(width_pt):
                self.text(x_pt, y_pt, line, size_pt)
                y_pt += leading
                line = []
            line.append(token)
        if line:
            self.text(x_pt, y_pt, line, size_pt)
            y_pt += leading
        return y_pt


def _person(rng: random.Random) -> tuple[str, str, str, str, str, str, str]:
    return (
        rng.choice(FIRST_NAMES),
        rng.choice(NACHNAMEN),
        rng.choice(STREETS),
        rng.choice(LOCATIONS),
        rng.choice(IBANS),
        rng.choice(TELEFONE),
        rng.choice(UIDS),
    )


def _mail(first: str, last: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "ı": "i", "Ö": "Oe"})
    local = f"{first.split()[0]}.{last}".translate(table).casefold()
    return f"{local}@beispiel-mail.de"


def _letter(canvas: _Canvas, rng: random.Random, size: int, *, columns: int) -> None:
    first, last, street, city, iban, phone, uid = _person(rng)
    canvas.text(360, 60, [("Moebelhaus Online Shop", None)], 9)
    canvas.text(360, 72, [("Tel:", None), ("050123/154-100", "PHONE")], 9)
    canvas.text(360, 84, [("E-Mail:", None), ("shop@moebelhaus-beispiel.at", "EMAIL")], 9)
    canvas.text(360, 96, [("UID:", None), (uid, "UID")], 9)
    canvas.text(60, 150, [(f"{first} {last}", "NAME")], 10)
    canvas.text(60, 163, [(street, "ADDRESS")], 10)
    canvas.text(60, 176, [(city, "ADDRESS")], 10)
    canvas.text(60, 230, [("Rechnung Nr. 2026-0815", None)], 12, font_name="hebo")
    body = [
        (f"Sehr geehrte Frau {last},", None),
        (rng.choice(FILLER_TEXT), None),
        ("Bitte ueberweisen Sie den Betrag auf das Konto IBAN", None),
        (iban, "IBAN"),
        ("– Rueckfragen beantwortet", None),
        (f"{rng.choice(FIRST_NAMES)} {rng.choice(NACHNAMEN)}", "NAME"),
        ("unter", None),
        (phone, "PHONE"),
        ("oder", None),
        (_mail(first, last), "EMAIL"),
        (rng.choice(FILLER_TEXT), None),
        (rng.choice(FILLER_TEXT), None),
    ]
    if columns == 1:
        canvas.wrapped(60, 260, 470, body, size)
    else:
        middle = len(body) // 2
        canvas.wrapped(60, 260, 220, body[:middle], size)
        canvas.wrapped(315, 260, 220, body[middle:], size)
    _footer(canvas, rng)


def _table(canvas: _Canvas, rng: random.Random, size: int) -> None:
    canvas.text(60, 80, [("Kundenliste", None)], 14, font_name="hebo")
    rows = [_person(rng) for _ in range(6)]
    cells = [(f"{first} {last}", iban, phone) for first, last, _s, _c, iban, phone, _u in rows]
    face = font(canvas.font_name, max(4, canvas.px(size)))
    # Columns as wide as their longest entry plus 3 em; a row must never overflow into the next
    # column.
    widths = [
        max(face.getlength(row[column]) for row in cells) * 72 / canvas.dpi + 3 * size
        for column in range(3)
    ]
    columns = (60, 60 + widths[0], 60 + widths[0] + widths[1])
    for x, title in zip(columns, ("Name", "IBAN", "Telefon"), strict=True):
        canvas.text(x, 120, [(title, None)], size, font_name="hebo")
    for row, (name, iban, phone) in enumerate(cells):
        y = 120 + (row + 1) * size * 2.2
        canvas.text(columns[0], y, [(name, "NAME")], size)
        canvas.text(columns[1], y, [(iban, "IBAN")], size)
        canvas.text(columns[2], y, [(phone, "PHONE")], size)
    _footer(canvas, rng)


def _footer(canvas: _Canvas, rng: random.Random) -> None:
    first, last, _street, _city, iban, _phone, _uid = _person(rng)
    canvas.text(
        60,
        800,
        [
            ("Musterfirma GmbH · Geschaeftsfuehrung:", None),
            (f"{first} {last}", "NAME"),
            ("· IBAN", None),
            (iban, "IBAN"),
        ],
        6,
        color=(60, 60, 60),
    )
    canvas.text(60, 808, [("info@musterfirma.de", "EMAIL"), ("· Sitz Wien", None)], 6)


def _transform(words: list[TruthWord], matrix: object) -> list[TruthWord]:
    transform = matrix  # (x, y) → (x', y')
    moved = []
    for word in words:
        left, top, right, bottom = word.box
        corners = [
            transform(x, y) for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))
        ]  # type: ignore[operator]
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        box = (math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys)))
        moved.append(TruthWord(word.text, box, word.line, word.pii))
    return moved


def _augment(page: Page, rng: random.Random) -> None:
    image = page.image
    width, height = image.size
    if page.augment == "skew":
        angle = 0.5 + rng.random() * 1.5
        image = image.rotate(angle, resample=Image.BICUBIC, fillcolor="white")
        cx, cy = width / 2, height / 2
        radians = math.radians(angle)
        cos, sin = math.cos(radians), math.sin(radians)

        def skew(x: float, y: float) -> tuple[float, float]:
            # PIL rotates counter-clockwise (y down).
            dx, dy = x - cx, y - cy
            return cx + dx * cos + dy * sin, cy - dx * sin + dy * cos

        page.words = _transform(page.words, skew)
    elif page.augment in {"rot90", "rot180", "rot270"}:
        # Like /Rotate: page appears rotated clockwise.
        degrees = int(page.augment[3:])
        image = image.rotate(-degrees, expand=True)
        mapping = {
            90: lambda x, y: (height - y, x),
            180: lambda x, y: (width - x, height - y),
            270: lambda x, y: (y, width - x),
        }[degrees]
        page.words = _transform(page.words, mapping)
    if page.augment in {"jpeg", "exif", "skew"}:
        # From the seeded source of the page: ``Image.effect_noise`` draws from an unseeded
        # generator,
        # so the baseline would be unstable.
        noise = Image.frombytes(
            "L", image.size, rng.randbytes(image.size[0] * image.size[1])
        ).convert("RGB")
        image = Image.blend(image, noise, 0.06).filter(ImageFilter.GaussianBlur(0.4))
    if page.augment in {"jpeg", "exif"}:
        buffer = io.BytesIO()
        if page.augment == "exif":
            # Saved rotated counter-clockwise, orientation 6; read as in
            # extractor with ``exif_transpose``.
            exif = Image.Exif()
            exif[0x0112] = 6
            image.rotate(90, expand=True).save(buffer, format="JPEG", quality=72, exif=exif)
        else:
            image.save(buffer, format="JPEG", quality=72)
        with Image.open(io.BytesIO(buffer.getvalue())) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
    page.image = image


def pages() -> Iterator[Page]:
    """The corpus pages, always the same."""

    for index in range(PAGES):
        rng = random.Random(f"ocr-korpus-{VERSION}-{index}")
        layout = LAYOUTS[index % len(LAYOUTS)]
        font_name = FONTS[index % len(FONTS)]
        size = SIZES_PT[index % len(SIZES_PT)]
        dpi = DPIS[(index // len(LAYOUTS)) % len(DPIS)]
        augment = AUGMENTS[index % len(AUGMENTS)]
        canvas = _Canvas(dpi, font_name)
        if layout == "tabelle":
            _table(canvas, rng, size)
        else:
            _letter(canvas, rng, size, columns=1 if layout == "brief" else 2)
        page = Page(
            id=f"p{index:02d}-{layout}-{font_name}-{size}pt-{dpi}dpi-{augment}",
            layout=layout,
            font=font_name,
            size_pt=size,
            dpi=dpi,
            augment=augment,
            image=canvas.image,
            words=canvas.words,
        )
        _augment(page, rng)
        yield page


CODE_FONTS = ("helv", "tiro", "cour")
CODE_SIZES = (6, 8, 10, 12)
CODES_PER_SHEET = 12


def code_sheets() -> Iterator[CodeSheet]:
    """For each font and size, 12 codes ``<<TYP_xxxxxxxx>>`` on a 300 dpi page."""

    # The detection types, also with underscore in the type.
    types = ("PERSON", "LOCATION", "IBAN_CODE", "PHONE_NUMBER", "EMAIL_ADDRESS", "UID")
    for font_name in CODE_FONTS:
        for size in CODE_SIZES:
            rng = random.Random(f"ocr-codes-{VERSION}-{font_name}-{size}")
            canvas = _Canvas(300, font_name)
            codes = []
            for index in range(CODES_PER_SHEET):
                value = f"<<{rng.choice(types)}_{rng.getrandbits(32):08x}>>"
                x = 60 + (index % 2) * 270
                y = 100 + (index // 2) * 110 + rng.random() * 20
                canvas.text(x, y, [(value, None)], size)
                codes.append((value, canvas.words[-1].box))
            yield CodeSheet(
                id=f"codes-{font_name}-{size}pt",
                font=font_name,
                size_pt=size,
                image=canvas.image,
                codes=tuple(codes),
            )


def main() -> None:
    """Write the corpus as PNG files for visual inspection (``--output``)."""

    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for page in pages():
        page.image.save(args.output / f"{page.id}.png")
    for sheet in code_sheets():
        sheet.image.save(args.output / f"{sheet.id}.png")
    print(f"geschrieben: {args.output}")


if __name__ == "__main__":
    main()
