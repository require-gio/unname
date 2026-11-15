"""Restore text and files with origin."""

# The ``api`` fixture is provided by ``test_api_v2``; as a parameter it hides the import.
# ruff: noqa: F811

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pytest
from docx import Document
from docx.oxml import parse_xml
from openpyxl import Workbook, load_workbook

from presidio_sidecar.masking import MaskingSpec
from presidio_sidecar.restore import Restorer, candidates
from presidio_sidecar.restored_files import RestoredFileStore
from presidio_sidecar.vault import TokenVault
from test_api_v2 import AUTH, REVERSIBLE, Harness, api  # noqa: F401

# -- Matching ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "written"),
    [
        ("<<PERSON_ba903855>>", "<<PERSON_ba903855>>"),
        ("<PERSON_ba903855>", "<PERSON_ba903855>"),
        ("PERSON_ba903855", "PERSON_ba903855"),
        ("<< person_ba903855 >>", "<< person_ba903855 >>"),
        ("«PERSON_ba903855»", "«PERSON_ba903855»"),
        ("[PERSON_ba903855]", "[PERSON_ba903855]"),
        ("\\<\\<PERSON_ba903855\\>\\>", "\\<\\<PERSON_ba903855\\>\\>"),
        ("<<PERSON_BA903855>>", "<<PERSON_BA903855>>"),
        ("**<<PERSON_ba903855>>**", "<<PERSON_ba903855>>"),
        ("Frau <<PERSON_ba903855>>s Akte", "<<PERSON_ba903855>>"),
    ],
)
def test_tolerant_spellings_lead_to_exactly_one_code(text: str, written: str) -> None:
    found = candidates(text)
    assert len(found) == 1
    item = found[0]
    assert item.token == "<<PERSON_ba903855>>"
    assert text[item.start : item.end] == written
    assert item.tolerant is (written != "<<PERSON_ba903855>>")


def test_lookalikes_are_not_codes() -> None:
    # Without brackets only a known type counts: code examples stay as text.
    assert candidates("build_12345678 und var_deadbeef") == []
    assert candidates("XPERSON_ba903855 PERSON_ba903855_x") == []
    # A comparison with spaces remains a comparison.
    found = candidates("a < PERSON_ba903855 b")
    assert [(item.start, item.end) for item in found] == [(4, 19)]
    # Types with underscore end only before the check value.
    assert candidates("<<CUSTOMER_NUMBER_9d03b7aa>>")[0].entity_type == "CUSTOMER_NUMBER"


def test_corrupted_codes_are_reported_not_guessed(tmp_path: Path) -> None:
    vault = TokenVault(tmp_path / "state")
    try:
        token = vault.prepare_token("PERSON", "Sabine Hofer")
        vault.commit([token], entry_id="e1")
        digest = token.token[len("<<PERSON_") : -2]
        restorer = Restorer(vault, entry_id="e1", entry_titles={"e1": "Angebot"})
        result = restorer.restore(
            f"A {token.token} B <<NAME_{digest}>> C <<PERSON_{digest[:7]}>> D"
        )
        assert result.text.startswith("A Sabine Hofer B <<NAME_")
        assert [span.status for span in result.spans] == ["restored", "unknown", "unknown"]
    finally:
        vault.close()


# -- Text ----------------------------------------------------------------------


def _commit(api: Harness, entry_id: str, *values: tuple[str, str]) -> list[str]:
    vault = api.services.vault()
    pending = [vault.prepare_token(entity, value) for entity, value in values]
    vault.commit(pending, entry_id=entry_id)
    return [item.token for item in pending]


def _entry(api: Harness, title: str) -> str:
    return (
        api.services.entries()
        .create(
            kind="documents",
            masking=MaskingSpec.model_validate(REVERSIBLE),
            title=title,
            status="saved",
        )
        .id
    )


def test_answer_with_codes_from_two_entries_and_one_unknown(api: Harness) -> None:
    invoice_and_quote = _entry(api, "Rechnung & Angebot")
    lease = _entry(api, "Mietvertrag (Scan)")
    hofer, number = _commit(
        api, invoice_and_quote, ("PERSON", "Hofer"), ("CUSTOMER_NUMBER", "K-4711")
    )
    (wimmer,) = _commit(api, lease, ("PERSON", "Renate Wimmer"))
    (alt,) = _commit(api, "geloescht", ("PERSON", "Josef Brandstetter"))
    mappings = api.mappings()
    answer = (
        f"Sehr geehrte Frau {hofer},\n\nzur Kundennummer {number.lower()} …"
        f" Mietvertrag mit {wimmer}. Gruss {alt[1:-1]}\nPS: <<PERSON_8e21d7f0>> in Kopie."
    )

    response = api.client.post(
        "/restore/text", headers=AUTH, json={"text": answer, "entry_id": invoice_and_quote}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == (
        "Sehr geehrte Frau Hofer,\n\nzur Kundennummer K-4711 … Mietvertrag mit Renate Wimmer."
        " Gruss Josef Brandstetter\nPS: <<PERSON_8e21d7f0>> in Kopie."
    )
    assert body["counts"] == {
        "restored": 4,
        "here": 2,
        "other": 1,
        "earlier": 1,
        "unknown": 1,
        "unknown_unique": 1,
        "tolerant": 2,
    }
    assert body["origins"] == [{"entry_id": lease, "title": "Mietvertrag (Scan)", "count": 1}]
    spans = body["spans"]
    assert [body["text"][span["start"] : span["end"]] for span in spans] == [
        "Hofer",
        "K-4711",
        "Renate Wimmer",
        "Josef Brandstetter",
        "<<PERSON_8e21d7f0>>",
    ]
    assert [span["origin"] for span in spans] == ["here", "here", "entry", "earlier", None]
    assert spans[2]["entry_id"] == lease
    # No data saved: no new mapping, no new entry, no counter.
    assert api.mappings() == mappings
    assert api.client.get(f"/entries/{invoice_and_quote}", headers=AUTH).json()["restores"] == 0
    assert body["recorded"] is False


def test_copy_counts_restore_and_usage(api: Harness) -> None:
    invoice_and_quote = _entry(api, "Angebot")
    (token,) = _commit(api, invoice_and_quote, ("PERSON", "Sabine Hofer"))
    vault = api.services.vault()
    before = vault.last_used(token)
    assert before is not None

    body = {"text": f"Liebe {token}", "entry_id": invoice_and_quote, "record": True}
    first = api.client.post("/restore/text", headers=AUTH, json=body).json()
    api.client.post("/restore/text", headers=AUTH, json=body)

    assert first["recorded"] is True
    entry = api.client.get(f"/entries/{invoice_and_quote}", headers=AUTH).json()
    assert entry["restores"] == 2
    assert entry["restored_at"] is not None
    assert vault.last_used(token) > before  # type: ignore[operator]
    # An entry that no longer exists is not an error – only the "here" origin is missing.
    other = api.client.post(
        "/restore/text",
        headers=AUTH,
        json={"text": f"Liebe {token}", "entry_id": "weg", "record": True},
    ).json()
    assert other["text"] == "Liebe Sabine Hofer"
    assert other["spans"][0]["origin"] == "entry"
    assert other["recorded"] is False


def test_fake_names_are_restored_only_on_request(api: Harness) -> None:
    invoice_and_quote = _entry(api, "Brief")
    vault = api.services.vault()
    pending = vault.prepare_surrogate("PERSON", "Petra Novak")
    vault.commit([pending], entry_id=invoice_and_quote)
    assert pending.surrogate is not None
    text = f"Danke, {pending.surrogate}!"

    plain = api.client.post("/restore/text", headers=AUTH, json={"text": text}).json()
    with_surrogates = api.client.post(
        "/restore/text",
        headers=AUTH,
        json={"text": text, "entry_id": invoice_and_quote, "surrogates": True},
    ).json()

    assert plain["text"] == text
    assert with_surrogates["text"] == "Danke, Petra Novak!"
    assert with_surrogates["spans"][0]["kind"] == "surrogate"
    assert with_surrogates["spans"][0]["origin"] == "here"


def test_offsets_count_in_utf16(api: Harness) -> None:
    invoice_and_quote = _entry(api, "Notiz")
    (token,) = _commit(api, invoice_and_quote, ("PERSON", "Hofer"))
    body = api.client.post(
        "/restore/text",
        headers=AUTH,
        json={"text": f"😀 {token} 😀", "entry_id": invoice_and_quote},
    ).json()
    span = body["spans"][0]
    # "😀" occupies two UTF-16 code units.
    assert (span["start"], span["end"]) == (3, 8)


def test_too_large_answer_is_rejected(api: Harness) -> None:
    response = api.client.post(
        "/restore/text", headers=AUTH, json={"text": "x" * (1024 * 1024 + 1)}
    )
    assert response.status_code == 413


# -- Files -------------------------------------------------------------------


def _upload(api: Harness, name: str, content: bytes, **params: Any) -> Any:
    return api.client.post(
        "/restore/file",
        headers=AUTH,
        params=params,
        files=[("file", (name, content, "application/octet-stream"))],
    )


def _docx(*paragraphs: list[tuple[str, bool]], header: str | None = None) -> bytes:
    document = Document()
    for runs in paragraphs:
        paragraph = document.add_paragraph()
        for text, bold in runs:
            paragraph.add_run(text).bold = bold
    if header is not None:
        document.sections[0].header.paragraphs[0].text = header
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_docx_with_codes_from_two_entries_is_restored_losslessly(
    api: Harness,
) -> None:
    invoice_and_quote = _entry(api, "Rechnung")
    lease = _entry(api, "Mietvertrag")
    (hofer,) = _commit(api, invoice_and_quote, ("PERSON", "Sabine Hofer"))
    (wimmer,) = _commit(api, lease, ("PERSON", "Renate Wimmer"))
    # The code spans three runs, partly bold; one run remains in the header.
    content = _docx(
        [
            ("Sehr geehrte Frau ", False),
            (hofer[:6], True),
            (hofer[6:12], False),
            (hofer[12:], True),
        ],
        [("Kaution: ", False), (wimmer, False), (" – und ", False), ("<<PERSON_8e21d7f0>>", False)],
        header=f"Akte {hofer}",
    )

    response = _upload(api, "Zahlungserinnerung_Entwurf.docx", content, entry_id=invoice_and_quote)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == {
        "name": "Zahlungserinnerung_Entwurf.docx",
        "format": "docx",
        "size_bytes": len(content),
    }
    assert body["counts"]["restored"] == 3
    assert body["counts"]["here"] == 2
    assert body["counts"]["other"] == 1
    assert body["counts"]["unknown"] == 1
    assert body["unreplaced"] == 0
    # Preview shows main text; header is still replaced.
    assert body["preview"]["text"] == (
        "Sehr geehrte Frau Sabine Hofer\nKaution: Renate Wimmer – und <<PERSON_8e21d7f0>>"
    )
    output = body["output"]
    assert output["name"] == "Zahlungserinnerung_Entwurf_mit_Namen.docx"

    fetched = api.client.get(f"/restore/file/{output['id']}", headers=AUTH)
    assert fetched.status_code == 200
    assert fetched.headers["x-content-sha256"] == output["sha256"]
    restored = Document(io.BytesIO(fetched.content))
    first = restored.paragraphs[0]
    assert first.text == "Sehr geehrte Frau Sabine Hofer"
    # Formatting stays: the inserted name is in the first run of the code.
    assert [run.bold for run in first.runs] == [False, True, False, True]
    assert restored.paragraphs[1].text.endswith("<<PERSON_8e21d7f0>>")
    assert restored.sections[0].header.paragraphs[0].text == "Akte Sabine Hofer"
    # The AI file remains unchanged; nothing was saved.
    assert Document(io.BytesIO(content)).paragraphs[0].text.endswith(hofer[12:])
    assert not any(path.suffix == ".docx" for path in api.tmp_path.rglob("*"))


def test_fetch_counts_once_remove_clears_up(api: Harness) -> None:
    invoice_and_quote = _entry(api, "Notiz")
    (token,) = _commit(api, invoice_and_quote, ("PERSON", "Hofer"))
    body = _upload(
        api, "antwort.md", f"# Hallo {token}\n".encode(), entry_id=invoice_and_quote
    ).json()
    file_id = body["output"]["id"]

    for _ in range(2):
        fetched = api.client.get(f"/restore/file/{file_id}", headers=AUTH)
        assert fetched.content == b"# Hallo Hofer\n"
    assert api.client.get(f"/entries/{invoice_and_quote}", headers=AUTH).json()["restores"] == 1

    assert api.client.delete(f"/restore/file/{file_id}", headers=AUTH).status_code == 204
    gone = api.client.get(f"/restore/file/{file_id}", headers=AUTH)
    assert gone.status_code == 404
    assert gone.json()["detail"]["code"] == "not_found"


def test_text_and_markdown_keep_bom_and_line_endings(api: Harness) -> None:
    invoice_and_quote = _entry(api, "Notiz")
    (token,) = _commit(api, invoice_and_quote, ("PERSON", "Jürgen Müller"))
    content = b"\xef\xbb\xbf" + f"Hallo {token}\r\nTschüss\r\n".encode()

    body = _upload(api, "brief.txt", content).json()
    fetched = api.client.get(f"/restore/file/{body['output']['id']}", headers=AUTH).content

    assert fetched == b"\xef\xbb\xbf" + "Hallo Jürgen Müller\r\nTschüss\r\n".encode()


def test_without_known_code_no_file_is_created(api: Harness) -> None:
    body = _upload(api, "antwort.txt", b"Nur <<PERSON_8e21d7f0>> und Text.").json()
    assert body["output"] is None
    assert body["counts"]["unknown"] == 1
    assert body["preview"]["spans"][0]["status"] == "unknown"


def test_xlsx_replaces_cells_and_keeps_formulas(api: Harness) -> None:
    invoice_and_quote = _entry(api, "Liste")
    (token,) = _commit(api, invoice_and_quote, ("PERSON", "Hofer"))
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = f"Kunde {token}"
    sheet["A2"] = '=CONCAT(A1, "x")'
    sheet["B1"] = 42
    buffer = io.BytesIO()
    workbook.save(buffer)

    body = _upload(api, "liste.xlsx", buffer.getvalue(), entry_id=invoice_and_quote).json()
    fetched = api.client.get(f"/restore/file/{body['output']['id']}", headers=AUTH).content
    restored = load_workbook(io.BytesIO(fetched)).active

    assert restored["A1"].value == "Kunde Hofer"
    assert restored["A2"].value == '=CONCAT(A1, "x")'
    assert restored["B1"].value == 42
    assert body["output"]["name"] == "liste_mit_Namen.xlsx"


def test_xlsx_with_chart_is_not_written_modified(api: Harness) -> None:
    workbook = Workbook()
    workbook.active["A1"] = "x"
    buffer = io.BytesIO()
    workbook.save(buffer)
    with zipfile.ZipFile(buffer, "a") as archive:
        archive.writestr("xl/charts/chart1.xml", "<c/>")

    response = _upload(api, "diagramm.xlsx", buffer.getvalue())

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_content"


def test_code_in_a_field_counts_as_not_replaceable(api: Harness) -> None:
    invoice_and_quote = _entry(api, "Brief")
    (token,) = _commit(api, invoice_and_quote, ("PERSON", "Hofer"))
    document = Document()
    paragraph = document.add_paragraph("Hallo ")
    # Simple field: the text is in a Run inside w:fldSimple.
    paragraph._p.append(  # noqa: SLF001
        parse_xml(
            '<w:fldSimple xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
            f' w:instr="MERGEFIELD x"><w:r><w:t>{escape(token)}</w:t></w:r></w:fldSimple>'
        )
    )
    document.add_paragraph(f"Gruss {token}")
    buffer = io.BytesIO()
    document.save(buffer)

    body = _upload(api, "feld.docx", buffer.getvalue(), entry_id=invoice_and_quote).json()

    assert body["counts"]["restored"] == 1
    assert body["unreplaced"] == 1


@pytest.mark.parametrize(
    ("name", "content", "status", "code"),
    [
        ("antwort.pdf", b"%PDF-1.7", 415, "unsupported_format"),
        ("leer.txt", b"", 422, "empty"),
        ("kaputt.docx", b"PK\x03\x04kaputt", 422, "invalid_file"),
        ("latin.txt", "Grüße".encode("latin-1"), 422, "encoding"),
    ],
)
def test_unsuitable_files_rejected_with_reason(
    api: Harness, name: str, content: bytes, status: int, code: str
) -> None:
    response = _upload(api, name, content)
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code


def test_entry_id_in_parameter_is_path_free(api: Harness) -> None:
    response = _upload(api, "a.txt", b"x", entry_id="../jobs")
    assert response.status_code == 422


def test_store_keeps_few_files_and_forgets_old() -> None:
    now = [0.0]
    store = RestoredFileStore(ttl_seconds=10, max_items=2, clock=lambda: now[0])
    first = store.put(name="a", format="txt", content=b"1", entry_id=None, tokens=frozenset())
    second = store.put(name="b", format="txt", content=b"2", entry_id=None, tokens=frozenset())
    third = store.put(name="c", format="txt", content=b"Hofer", entry_id=None, tokens=frozenset())
    assert store.get(first.id) is None
    assert store.get(second.id) is not None
    # Content never appears in logs or error messages.
    assert "Hofer" not in repr(third)
    now[0] = 11
    assert store.get(third.id) is None


# -- Table ---------------------------------------------------------------------


def test_vault_schema_two_gets_last_use(tmp_path: Path) -> None:
    vault = TokenVault(tmp_path / "state")
    pending = vault.prepare_token("PERSON", "Hans Meier")
    saved_at = datetime(2026, 1, 2, tzinfo=UTC)
    vault.commit([pending], entry_id="e", used_at=saved_at)
    mapping = vault._mapping_store()
    mapping._connection.executescript(
        "ALTER TABLE mappings DROP COLUMN last_used_at; "
        "UPDATE vault_metadata SET schema_version = 2;"
    )
    mapping._persist()
    vault.close()

    reopened = TokenVault(tmp_path / "state")
    try:
        assert reopened.last_used(pending.token) == saved_at.isoformat()
        later = saved_at + timedelta(days=30)
        assert reopened.touch({pending.token, "<<PERSON_00000000>>"}, used_at=later) == 1
        assert reopened.last_used(pending.token) == later.isoformat()
        record = reopened.lookup({pending.token})[pending.token]
        assert record.canonical == "Hans Meier"
        assert record.origins == (("e", saved_at.isoformat()),)
    finally:
        reopened.close()
