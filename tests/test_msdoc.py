"""Reading legacy Word (.doc) resumes.

The fixtures in tests/fixtures/doc are Apache POI's test documents
(test-data/document, Apache License 2.0): real files written by Word, not
ones produced by the parser under test, so a misread of the format can't
cancel itself out.
"""

from __future__ import annotations

import io
from pathlib import Path

import docx
import pytest

from core.msdoc import DocError, extract_text, read_doc
from core.pipeline import read_document
from tests.dbmark import requires_db

FIXTURES = Path(__file__).parent / "fixtures" / "doc"


class TestReadsWordText:
    def test_cyrillic_comes_out_as_text(self):
        text = read_doc(FIXTURES / "rasp.doc")
        assert text.startswith("Распоряжение на выдачу информации из реестра")
        assert "Открытое акционерное общество" in text

    def test_hyperlink_keeps_the_shown_text_not_the_field_code(self):
        assert read_doc(FIXTURES / "hyperlink.doc") == "Before text; Hyperlink text; after text"

    def test_table_rows_stay_on_separate_lines(self):
        lines = read_doc(FIXTURES / "simple-table.doc").splitlines()
        assert "Cell 1,1\tCell 1,2\tCell 1,3" in lines
        assert "Cell 2,1\tCell 2,2\tCell 2,3" in lines

    def test_embedded_objects_do_not_shadow_the_main_document(self):
        # The file carries embedded Office documents with streams of the same
        # names; a flat lookup by name read one of those instead.
        assert read_doc(FIXTURES / "word_with_embeded.doc") == "I have lots of embedded files in me"

    def test_read_document_dispatches_on_the_suffix(self):
        assert read_document(FIXTURES / "hyperlink.doc").startswith("Before text")


class TestRefusesWithAReason:
    """Each refusal is something the user fixes by resaving, so it says which."""

    def test_password_protected(self):
        with pytest.raises(DocError, match="паролем"):
            read_doc(FIXTURES / "PasswordProtected.doc")

    def test_word_95(self):
        with pytest.raises(DocError, match="Word 95"):
            read_doc(FIXTURES / "Word6.doc")

    def test_rtf_under_a_doc_name(self, tmp_path):
        path = tmp_path / "cv.doc"
        path.write_bytes(b"{\\rtf1\\ansi\\ansicpg1251 \\'c8\\'e2\\'e0\\'ed}")
        with pytest.raises(DocError, match="RTF"):
            read_doc(path)

    def test_not_a_word_file(self):
        with pytest.raises(DocError):
            extract_text(b"plain text, not a compound file" * 40)

    @pytest.mark.parametrize("cut", [512, 4096, 9000])
    def test_truncated_file_is_an_error_not_a_crash(self, cut):
        data = (FIXTURES / "rasp.doc").read_bytes()[:cut]
        with pytest.raises(DocError):
            extract_text(data)


def test_docx_renamed_to_doc_is_read_as_docx(tmp_path):
    document = docx.Document()
    document.add_paragraph("Иван Петров, бухгалтер")
    buf = io.BytesIO()
    document.save(buf)
    path = tmp_path / "cv.doc"
    path.write_bytes(buf.getvalue())
    assert read_doc(path) == "Иван Петров, бухгалтер"


@requires_db
async def test_upload_explains_an_unreadable_doc(client, auth_headers):
    """A .doc is accepted by the upload, and a bad one is a 400 with the reason.

    Before this the suffix was refused outright; an unhandled parser error
    would have been a bare 500 with nothing for the user to act on.
    """
    r = await client.post(
        "/api/resumes",
        headers=auth_headers,
        files={
            "file": (
                "cv.doc",
                (FIXTURES / "PasswordProtected.doc").read_bytes(),
                "application/msword",
            )
        },
    )
    assert r.status_code == 400
    assert "паролем" in r.json()["detail"]
