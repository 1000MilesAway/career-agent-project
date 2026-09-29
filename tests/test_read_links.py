"""Link targets survive text extraction.

A CV shows "@handle" or "GitHub" and keeps the address in the link. Plain
text extraction drops the address, and with it the contact: the model never
sees it and `core.contacts` cannot find it. Built on the user's own PDF,
where the Telegram line reads only "@e1337_u53r".
"""

from __future__ import annotations

import docx
import pymupdf
from docx.opc.constants import RELATIONSHIP_TYPE as RT

from core.pipeline import read_document


def test_pdf_link_targets_are_appended(tmp_path):
    path = tmp_path / "cv.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "@e1337_u53r")
    page.insert_link(
        {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 60, 200, 80),
         "uri": "https://t.me/e1337_u53r"}
    )
    page.insert_link(
        {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 90, 200, 110),
         "uri": "https://t.me/e1337_u53r"}
    )
    doc.save(path)

    text = read_document(path)

    assert text.startswith("@e1337_u53r")
    assert text.endswith("Ссылки:\nhttps://t.me/e1337_u53r")  # once, not twice


def test_docx_hyperlink_targets_are_appended(tmp_path):
    path = tmp_path / "cv.docx"
    document = docx.Document()
    document.add_paragraph("GitHub")
    document.part.relate_to("https://github.com/ivandev", RT.HYPERLINK, is_external=True)
    document.save(path)

    assert read_document(path).endswith("Ссылки:\nhttps://github.com/ivandev")


def test_internal_and_odd_schemes_are_left_out(tmp_path):
    path = tmp_path / "cv.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "text")
    page.insert_link(
        {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 60, 200, 80),
         "uri": "javascript:alert(1)"}
    )
    doc.save(path)

    assert read_document(path) == "text"
