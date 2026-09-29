"""The resume download: what ends up in the file, and what must not.

PDF rendering itself is not exercised here — WeasyPrint needs Pango and Cairo
from the OS, which a developer laptop need not have, and a suite that fails
"because the machine lacks a system library" teaches people to ignore red.
What IS pinned here is everything that decides whether the file is correct:
the block structure, the ATS-hostile shapes, and the leak that would send an
employer the model's own notes. The HTML that WeasyPrint renders is asserted
directly, so a broken template fails here rather than in production.

DOCX is rendered for real: python-docx is a pure-Python dependency that the
parser step already requires, so there is nothing to skip.
"""

from __future__ import annotations

from io import BytesIO

import pytest

from core.export import (
    Document,
    ascii_filename,
    filename,
    profile_document,
    render,
    tailored_resume_document,
    to_docx,
    to_html,
    to_markdown,
)

PROFILE = {
    "name": "Иванов Иван",
    "target_role": "Ведущий backend-инженер",
    "seniority": "senior",
    "years_experience": 8,
    "field": "IT",
    "directions": ["Backend"],
    "skills": ["Python", "PostgreSQL"],
    "experience": [
        {
            "role": "Тимлид",
            "company": "ООО «Ромашка»",
            "period": "2021–2025",
            "achievements": ["Сократил время сборки на 40%"],
        }
    ],
}

TAILORED = {
    "summary": "Инженер с 8 годами опыта.",
    "experience": [
        {
            "role": "Тимлид",
            "company": "Ромашка",
            "period": "2021–2025",
            "bullets": ["Внедрил CI/CD"],
        }
    ],
    "skills": ["Python", "Go"],
    "ats_keywords": ["микросервисы"],
    "changes": ["Переставил акценты на платформу"],
}


def texts(doc: Document) -> list[str]:
    return [b.text for b in doc.blocks]


class TestProfileDocument:
    def test_carries_the_cv_content(self):
        doc = profile_document(PROFILE)
        body = "\n".join(texts(doc))
        assert "Иванов Иван" in body
        assert "Ведущий backend-инженер" in body
        assert "Сократил время сборки на 40%" in body
        assert "PostgreSQL" in body

    def test_title_is_the_name_so_the_file_is_findable(self):
        assert profile_document(PROFILE).title == "Иванов Иван"

    def test_empty_profile_does_not_raise(self):
        """A CV the model failed to parse must still download, not 500."""
        doc = profile_document({})
        assert doc.title == "Резюме"
        assert to_html(doc)

    def test_missing_sections_leave_no_empty_headings(self):
        """A heading with nothing under it reads as lost data."""
        doc = profile_document({"name": "Пётр"})
        assert "Навыки" not in texts(doc)
        assert "Опыт работы" not in texts(doc)

    def test_junk_experience_entry_is_skipped(self):
        doc = profile_document({"name": "Пётр", "experience": ["строка", None, {"role": "Инженер"}]})
        assert "Инженер" in texts(doc)


class TestTailoredDocument:
    def test_identity_comes_from_the_profile(self):
        """The tailoring prompt returns content, not identity.

        Without the name from the profile the employer receives an anonymous
        document — which is not a CV.
        """
        doc = tailored_resume_document(TAILORED, profile=PROFILE)
        assert doc.blocks[0].text == "Иванов Иван"

    def test_changes_never_reach_the_file(self):
        """`changes` is the model's audit trail for the user, not for the employer."""
        doc = tailored_resume_document(TAILORED, profile=PROFILE, vacancy_title="Backend")
        body = "\n".join(texts(doc))
        assert "акценты" not in body
        assert "Внедрил CI/CD" in body

    def test_vacancy_title_is_noted(self):
        doc = tailored_resume_document(TAILORED, profile=PROFILE, vacancy_title="Backend-инженер")
        assert any("Backend-инженер" in t for t in texts(doc))

    def test_empty_summary_drops_its_heading(self):
        doc = tailored_resume_document({"skills": ["Go"]}, profile=PROFILE)
        assert "О себе" not in texts(doc)


class TestHtml:
    def test_bullets_are_grouped_into_one_list(self):
        html = to_html(profile_document(PROFILE))
        assert html.count("<ul>") == 1
        assert html.count("</ul>") == 1

    def test_bullet_marker_is_inside_the_text_run(self):
        """`outside` markers extract AFTER the text and read as stray dots.

        Verified by extracting both variants from a rendered PDF; see the
        comment on PDF_CSS.
        """
        assert "list-style-position: inside" in to_html(profile_document(PROFILE))

    def test_content_is_escaped(self):
        """A CV is user data; an unescaped one would rewrite the template."""
        html = to_html(profile_document({"name": "<script>alert(1)</script>"}))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_single_column_no_tables(self):
        """A multi-column CV reaches an ATS as interleaved gibberish."""
        html = to_html(profile_document(PROFILE))
        assert "<table" not in html
        assert "column-count" not in html


class TestDocx:
    def test_renders_and_keeps_the_content(self):
        import docx

        data = to_docx(profile_document(PROFILE))
        assert data[:2] == b"PK"  # a .docx is a zip

        parsed = docx.Document(BytesIO(data))
        body = "\n".join(p.text for p in parsed.paragraphs)
        assert "Иванов Иван" in body
        assert "Сократил время сборки на 40%" in body

    def test_uses_builtin_styles_so_structure_survives(self):
        """Built-in styles are what an ATS and Word's outline both read."""
        import docx

        parsed = docx.Document(BytesIO(to_docx(profile_document(PROFILE))))
        styles = {p.style.name for p in parsed.paragraphs if p.text.strip()}
        assert "List Bullet" in styles
        assert any(s.startswith("Heading") or s == "Title" for s in styles)

    def test_render_dispatches(self):
        assert render(profile_document(PROFILE), "docx")[:2] == b"PK"

    def test_unknown_format_is_refused(self):
        with pytest.raises(ValueError):
            render(profile_document(PROFILE), "rtf")  # type: ignore[arg-type]


class TestMarkdown:
    def test_structure_is_markdown(self):
        md = to_markdown(profile_document(PROFILE)).decode("utf-8")
        assert md.startswith("# Иванов Иван\n")
        assert "\n## Опыт работы\n" in md
        assert "\n### Тимлид — ООО «Ромашка»\n" in md
        assert "\n- Сократил время сборки на 40%\n" in md

    def test_bullets_stay_one_list(self):
        doc = Document(title="x")
        doc.add("bullet", "раз")
        doc.add("bullet", "два")
        assert to_markdown(doc) == "- раз\n- два\n".encode()

    def test_text_cannot_forge_structure(self):
        """A bullet from the model that starts with "#" must not become a heading."""
        doc = Document(title="x")
        doc.add("para", "# не заголовок")
        doc.add("para", "1. не список")
        doc.add("para", "C# и *звёздочки*\nс переносом")
        md = to_markdown(doc).decode()
        assert md.splitlines() == [
            r"\# не заголовок",
            "",
            r"\1. не список",
            "",
            r"C# и \*звёздочки\* с переносом",
        ]

    def test_changes_never_reach_the_file(self):
        md = to_markdown(tailored_resume_document(TAILORED, profile=PROFILE)).decode()
        assert "Переставил акценты" not in md

    def test_render_dispatches(self):
        assert render(profile_document(PROFILE), "md").startswith(b"# ")


class TestFilename:
    def test_keeps_cyrillic(self):
        assert filename("Иванов Иван", "pdf") == "Иванов_Иван.pdf"

    @pytest.mark.parametrize("stem", ['a"b', "a/b", "a\r\nb", "../etc/passwd"])
    def test_strips_what_would_inject_a_header(self, stem):
        """Content-Disposition is a header; a quote or newline in it is an injection."""
        name = filename(stem, "pdf")
        assert not any(c in name for c in '"\r\n/')

    def test_empty_stem_still_produces_a_name(self):
        assert filename("", "docx") == "resume.docx"
        assert filename("...", "pdf") == "resume.pdf"


class TestAsciiFallback:
    """The `filename=` a client that ignores RFC 5987 falls back to."""

    def test_cyrillic_is_transliterated_not_blanked(self):
        """"_____.docx" in a downloads folder is indistinguishable from the next one."""
        assert ascii_filename("Иванов_Иван.docx") == "Ivanov_Ivan.docx"

    def test_result_is_pure_ascii(self):
        name = ascii_filename("Щукин Ъ Ярослав — ООО «Ромашка».pdf")
        assert name.isascii()
        assert "Shchukin" in name

    def test_extension_survives(self):
        assert ascii_filename("Пётр.docx").endswith(".docx")

    def test_latin_is_left_alone(self):
        assert ascii_filename("John_Smith.pdf") == "John_Smith.pdf"

    def test_unmappable_name_still_yields_a_filename(self):
        """A CV in a script with no table here must not produce an empty header."""
        assert ascii_filename("日本語") == "resume"
