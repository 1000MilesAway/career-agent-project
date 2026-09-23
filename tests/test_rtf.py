"""Reading RTF resumes — as .rtf, and under a .doc name.

The files in tests/fixtures/rtf are LibreOffice's import test documents
(sw/qa/extras/rtfimport/data, MPL-2.0), written by other programs, so a
misreading of RTF can't agree with itself. The hand-written sample below
pins the one combination they don't cover in a single file.
"""

from __future__ import annotations

from pathlib import Path

from core.msdoc import read_doc
from core.pipeline import read_document
from core.rtf import rtf_to_text

FIXTURES = Path(__file__).parent / "fixtures" / "rtf"

# The shape of a 1C / HR-system export: \ansicpg1251 with Cyrillic in a
# charset-0 font, a hyperlink field, a table, and a text box whose fallback
# copy must not be read twice.
RESUME = rb"""{\rtf1\ansi\ansicpg1251\deff0\uc1
{\fonttbl{\f0\fnil\fcharset0 Arial;}{\f1\fnil\fcharset204 Times New Roman;}}
{\colortbl;\red0\green0\blue0;}
{\stylesheet{\s0 Normal;}}
{\info{\author HR System}}
\pard\f0\'c8\'e2\'e0\'ed \'cf\'e5\'f2\'f0\'ee\'e2\par
\f1\u1041?\u1091?\u1093?\u1075?\u1072?\u1083?\u1090?\u1077?\u1088?\par
{\field{\*\fldinst{HYPERLINK "mailto:ivan@example.ru"}}{\fldrslt{ivan@example.ru}}}\par
\trowd\cellx3000\cellx6000
\intbl 2019\endash 2024\cell \'ce\'ce\'ce \'c0\'eb\'fc\'f4\'e0\cell\row
\pard{\shp{\*\shpinst{\sp{\sn shapeType}{\sv 7777}}{\shptxt \f1\'d2\'e5\'eb\'e5\'f4\'ee\'ed\par}}
{\shprslt{\*\do\dptxbx{\dptxbx \'d2\'e5\'eb\'e5\'f4\'ee\'ed}}}}
}"""


class TestCyrillic:
    def test_code_page_from_ansicpg_for_a_charset_0_font(self):
        assert rtf_to_text(RESUME).splitlines()[0] == "Иван Петров"

    def test_unicode_escapes_drop_their_fallback(self):
        assert rtf_to_text(RESUME).splitlines()[1] == "Бухгалтер"

    def test_code_page_from_the_font_charset(self):
        text = read_document(FIXTURES / "fns-certificate-cp1251.rtf")
        assert text.startswith("СПРАВКА № 6785")
        assert "О СОСТОЯНИИ РАСЧЕТОВ ПО НАЛОГАМ" in text

    def test_raw_8bit_bytes_not_only_escapes(self):
        text = read_document(FIXTURES / "raw-8bit-cp1251.rtf")
        assert text.startswith("БАНК ИТБ (ОАО), г.МОСКВА")


class TestStructure:
    def test_hyperlink_shows_its_text_not_the_field_code(self):
        text = rtf_to_text(RESUME)
        assert "ivan@example.ru" in text
        assert "HYPERLINK" not in text

    def test_table_row_is_one_line(self):
        assert "2019–2024\tООО Альфа" in rtf_to_text(RESUME).splitlines()

    def test_text_box_is_read_once(self):
        assert rtf_to_text(RESUME).count("Телефон") == 1

    def test_text_box_document(self):
        # All of this file's text is inside a shape; skipping \*\shpinst
        # wholesale read it as empty.
        text = read_document(FIXTURES / "text-box.rtf")
        assert text.startswith("AAA")
        assert "Lorem ipsum dolor sit amet" in text

    def test_tables_of_fonts_styles_and_metadata_stay_out(self):
        text = rtf_to_text(RESUME)
        for leaked in ("Arial", "Normal", "HR System", "shapeType", "7777"):
            assert leaked not in text


class TestUnderADocName:
    def test_rtf_named_doc_is_read(self, tmp_path):
        path = tmp_path / "Резюме.doc"
        path.write_bytes(RESUME)
        assert read_doc(path).startswith("Иван Петров")

    def test_read_document_routes_it_by_content_not_suffix(self, tmp_path):
        path = tmp_path / "cv.doc"
        path.write_bytes((FIXTURES / "fns-certificate-cp1251.rtf").read_bytes())
        assert read_document(path).startswith("СПРАВКА")


def test_garbage_does_not_raise():
    for data in (b"{\\rtf1", b"{\\rtf1 }}}}{{{", b"{\\rtf1\\ansicpg99999 \\'e0}", b"\\"):
        rtf_to_text(data)
