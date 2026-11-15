# unname-vision-ocr

Text recognition with Apple Vision for the sidecar on the Mac. It is a program
of its own with a narrow interface: an image on stdin, one JSON line on stdout.
It gets no path, writes no file and does not talk to the network; messages on
stderr never name content.

## Interface (protocol 1)

| Command | Input | Output |
|---|---|---|
| `unname-vision-ocr info` | – | `{"engine":"apple-vision","protocol":1,"helper","os","os_build","text_revisions","document_revisions","languages"}` |
| `unname-vision-ocr recognize --languages de-DE,en-US [--tiles auto\|1x1\|2x2\|3x3] [--mode text\|document] [--correction off\|on] [--qos user-initiated]` | PPM (P6), PGM (P5), PNG, TIFF or JPEG on stdin | `{"protocol":1,"request","revision","os_build","width","height","tiles","elapsed_ms","words":[{"text","left","top","width","height","line":[block,paragraph,line],"confidence"}]}` |

Exit codes: 0 success · 2 bad usage · 3 input too large (> 256 MB or > 100 MP)
· 4 recognition failed · 5 language, revision or macOS not supported. Revision 3
is fixed; if it is missing, the helper exits with 5 instead of silently
switching.

## Recognition

1. The whole page with `VNRecognizeTextRequest` (`.accurate`, language
   correction off).
2. If the text is rotated or upside down (reading direction of the lines), the
   image is recognized upright and every box is mapped back.
3. Tiles with 10 % overlap: at most 1,900 px per tile side, and from 1,000 px
   at least two per direction. Vision sometimes silently skips a whole page and
   misses small print; the tiles catch both.
4. Merging (words at an inner edge are dropped, equal readings become one, the
   tile wins against the page), splitting at whitespace, reading order (columns
   are never interleaved, tables are read row by row, "label: value" stays one
   line) and tightening the boxes only where they reach into the neighboring
   line.
5. Words that mix Latin and Cyrillic letters become Latin (only de-DE and
   en-US are requested).

`--mode document` (stage 2, `RecognizeDocumentsRequest`, macOS 26 and later)
returns paragraphs and tables from the document; it is only switched on once
the OCR gate confirms it.

## Build and test

```sh
swift build -c release --package-path vision-ocr
swift test --package-path vision-ocr
VISION_OCR_INTEGRATION=1 swift test --package-path vision-ocr   # with real recognition
```

The very first call per program, location and macOS build takes ≈ 50 s:
`ANECompilerService` compiles the Vision models for the Neural Engine and
stores them under `/Library/Caches/com.apple.aned/<build>/`. After that, an A4
page at 300 dpi costs about 0.9 s with tiles.

In development, the sidecar finds the helper under `.build/release/` or through
`PRESIDIO_VISION_OCR`; in the frozen sidecar only
`assets/vision-ocr/unname-vision-ocr` from the signed package counts
(`sidecar/src/presidio_sidecar/extraction/ocr_vision.py`).
`scripts/prepare-sidecar-assets.py` puts it into the package. The license is
the same as this repository (MIT). Vision is part of the system and needs no
third-party notice.
