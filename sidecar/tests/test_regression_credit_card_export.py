"""Bank statements: duplicate entries must be reviewed before PDF check.

Synthetic records replace private originals: varied spelling, column headers, and partially detected
duplicates across the four statements.
"""

from pathlib import Path

import fitz
import pytest

from presidio_sidecar.catalog import CATEGORY_IDS
from presidio_sidecar.detection import (
    DetectionPipeline,
    Finding,
    FindingSource,
    LanguageSegment,
    load_language_wordlists,
)
from presidio_sidecar.document_workflow import export_processed_document, pdf_retained_spans
from presidio_sidecar.exporting import sha256_file
from presidio_sidecar.extraction import DocumentExtractor
from presidio_sidecar.masking import MaskingSpec, processing_request
from presidio_sidecar.processing import TextProcessor
from presidio_sidecar.vault import TokenVault


class StatementNer:
    def __init__(self, seeds: tuple[tuple[str, str], ...]) -> None:
        self.seeds = seeds

    def detect(self, text: str) -> tuple[Finding, ...]:
        findings: list[Finding] = []
        cursor = 0
        for entity, value in self.seeds:
            start = text.index(value, cursor)
            cursor = start + len(value)
            findings.append(Finding(entity, start, cursor, 0.95, FindingSource.NER, text=value))
        return tuple(findings)


@pytest.mark.parametrize("style", ["codes", "surrogates", "placeholder", "mask", "remove"])
@pytest.mark.parametrize(
    ("lines", "seeds"),
    [
        (
            ("Zuletzt gueltiger Kontowortlaut", "Ort", "Place", "Wien"),
            (("LOCATION", "Ort"), ("LOCATION", "Place"), ("LOCATION", "Wien")),
        ),
        (("Google", "GOOGLE *VIDEO"), (("ORGANIZATION", "Google"),)),
        (("Budapest", "SHOP BUDAPEST"), (("LOCATION", "Budapest"),)),
        (
            ("Firmensitz Wien", "Firmensitz Wien"),
            (("LOCATION", "Wien"), ("PERSON", "Firmensitz Wien")),
        ),
    ],
    ids=["column-headings", "merchant-case", "location-case", "partial-duplicate"],
)
def test_statement_exports_after_duplicate_review(
    tmp_path: Path,
    lines: tuple[str, ...],
    seeds: tuple[tuple[str, str], ...],
    style: str,
) -> None:
    source = tmp_path / "statement.pdf"
    destination = tmp_path / "protected.pdf"
    with fitz.open() as pdf:
        for line in lines:
            page = pdf.new_page()
            page.insert_text((72, 72), line, fontsize=12)
        pdf.save(source)
    before = sha256_file(source)
    document = DocumentExtractor().extract_path(source, ocr="never")
    segments: list[LanguageSegment] = []
    cursor = 0
    for index, line in enumerate(lines):
        end = document.text.index(line, cursor) + len(line)
        if index == len(lines) - 1:
            end = len(document.text)
        segments.append(LanguageSegment(cursor, end, "en" if line == "Place" else "de"))
        cursor = end
    wordlists = {code: load_language_wordlists(code) for code in ("de", "en")}
    detection = DetectionPipeline(StatementNer(seeds)).analyze(
        document.text,
        languages=tuple(segments),
        business_terms_by_language={
            code: words.business_terms for code, words in wordlists.items()
        },
        suspicion_enabled=False,
    )
    method = "reversible" if style in {"codes", "surrogates"} else "irreversible"
    spec = MaskingSpec(
        method=method, style=style, categories=CATEGORY_IDS, anonymize=method == "irreversible"
    )
    state = tmp_path / "vault"
    state.mkdir(mode=0o700)
    secret = state / "token-vault.secret"
    secret.write_bytes(bytes(range(32)))
    secret.chmod(0o600)
    vault = TokenVault(state)
    try:
        processing = TextProcessor(vault).apply(
            document.text, detection, processing_request(spec), commit=False
        )
        result = export_processed_document(
            source,
            destination,
            document=document,
            source_text=document.text,
            processing=processing,
            pdf_retained_spans=pdf_retained_spans(document.text, detection, processing),
        )
    finally:
        vault.close()
    assert sha256_file(source) == before
    assert result.verification.rendered_pages == len(lines)
    assert result.verification.text_extraction_checked
    with fitz.open(destination) as exported:
        text = "\n".join(page.get_text() for page in exported)
    if lines[0].startswith("Zuletzt"):
        assert "Kontowortlaut" in text
        assert "Ort" in text
        assert "Place" in text
        assert "Wien" not in text
    else:
        assert not any(value.casefold() in text.casefold() for _, value in seeds)
