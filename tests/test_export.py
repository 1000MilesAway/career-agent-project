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

from core.contacts import extract_contacts
from core.export import (
    Block,
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
from core.resume_styles import DEFAULT_STYLE, STYLES, get_style

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
        comment on _BASE_CSS in core/resume_styles.py.
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
        assert "\n### Тимлид, ООО «Ромашка»\n" in md
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


class TestStyles:
    """A style changes the look, never the text an ATS extracts."""

    @pytest.mark.parametrize("style", sorted(STYLES))
    def test_every_style_keeps_the_ats_rules(self, style):
        html = to_html(profile_document(PROFILE), style)
        assert "list-style-position: inside" in html
        assert "<table" not in html
        assert "column-count" not in html
        assert "<script" not in html

    def test_the_markup_is_the_same_whatever_the_style(self):
        """Only <style> may differ: the body is what gets parsed."""

        def body(style: str) -> str:
            html = to_html(profile_document(PROFILE), style)
            return html[html.index("<body>") :]

        bodies = {body(s) for s in STYLES}
        assert len(bodies) == 1

    def test_the_header_is_in_the_markup_not_built_by_script(self):
        """WeasyPrint runs no JS: the band behind the name must exist in HTML."""
        html = to_html(profile_document(PROFILE), "accent")
        header = html[html.index('<header class="resume-header">') : html.index("</header>")]
        assert "Иванов Иван" in header
        assert "Ведущий backend-инженер" in header
        assert "Опыт работы" not in header

    def test_a_job_title_is_kept_with_its_dates(self):
        html = to_html(profile_document(PROFILE))
        assert '<div class="job"><h3>Тимлид, ООО «Ромашка»</h3><p class="meta">2021–2025</p></div>' in html

    def test_a_job_without_dates_still_renders(self):
        doc = Document(title="x")
        doc.add("h3", "Тимлид")
        doc.add("bullet", "Сделал")
        assert '<div class="job"><h3>Тимлид</h3></div><ul><li>Сделал</li></ul>' in to_html(doc)

    def test_styles_look_different(self):
        sheets = {get_style(s).stylesheet() for s in STYLES}
        assert len(sheets) == len(STYLES)

    def test_unknown_style_is_refused_not_silently_defaulted(self):
        with pytest.raises(KeyError):
            to_html(profile_document(PROFILE), "neon")

    def test_default_is_classic(self):
        assert DEFAULT_STYLE == "classic"
        assert get_style(None).id == "classic"

    def test_docx_carries_the_font_and_the_accent(self):
        import docx

        parsed = docx.Document(BytesIO(to_docx(profile_document(PROFILE), "accent")))
        assert parsed.styles["Normal"].font.name == "Inter"
        subtitle = next(p for p in parsed.paragraphs if p.text == "Ведущий backend-инженер")
        assert str(subtitle.runs[0].font.color.rgb) == "E2611E"

    @pytest.mark.parametrize("style", sorted(STYLES))
    def test_docx_keeps_builtin_styles_in_every_style(self, style):
        import docx

        parsed = docx.Document(BytesIO(to_docx(profile_document(PROFILE), style)))
        names = {p.style.name for p in parsed.paragraphs if p.text.strip()}
        assert "List Bullet" in names
        assert "Heading 1" in names

    def test_markdown_ignores_the_style(self):
        doc = profile_document(PROFILE)
        assert render(doc, "md", "accent") == render(doc, "md", "classic")

    def test_html_is_rendered_as_the_preview(self):
        page = render(profile_document(PROFILE), "html", "compact").decode()
        assert page == to_html(profile_document(PROFILE), "compact")


class TestContactsAndSections:
    CONTACTS = extract_contacts("ivan@mail.ru t.me/ivan_dev github.com/ivandev")
    FULL = {
        **PROFILE,
        "summary": "Инженер с 8 годами опыта.",
        "education": ["НГТУ, прикладная математика, 2016 — 2020"],
        "experience": [
            {
                "role": "Тимлид",
                "company": "ООО «Ромашка»",
                "period": "2021–2025",
                "location": "Новосибирск",
                "description": "Финтех: платежи для малого бизнеса.",
                "achievements": ["Сократил время сборки на 40%"],
            }
        ],
    }

    def test_contacts_sit_in_the_header_on_one_line(self):
        html = to_html(profile_document(PROFILE, contacts=self.CONTACTS))
        header = html[html.index("<header") : html.index("</header>")]
        assert header.count('<p class="contacts">') == 1
        assert 'href="mailto:ivan@mail.ru"' in header
        assert 'class="contact contact--telegram" href="https://t.me/ivan_dev"' in header

    def test_contacts_read_as_separate_words_in_the_text_layer(self):
        """The separator is text: without it an ATS reads "ivan@mail.ru@ivan_dev"."""
        html = to_html(profile_document(PROFILE, contacts=self.CONTACTS))
        assert '</a><span class="sep"> · </span><a' in html

    def test_an_unsafe_href_is_not_a_link(self):
        doc = Document(title="x")
        doc.add("h1", "x")
        doc.blocks.append(
            Block(kind="contact", text="клик", href="javascript:alert(1)", icon="email")
        )
        html = to_html(doc)
        assert "javascript:" not in html
        assert '<span class="contact contact--email">клик</span>' in html

    def test_no_svg_in_the_markup(self):
        """WeasyPrint draws inline SVG black whatever the CSS says; icons are CSS."""
        html = to_html(profile_document(PROFILE, contacts=self.CONTACTS), "graphite")
        assert "<svg" not in html[html.index("<body>") :]

    def test_summary_education_description_and_place(self):
        md = to_markdown(profile_document(self.FULL)).decode()
        assert "\n## О себе\n\nИнженер с 8 годами опыта.\n" in md
        assert "\n*2021–2025 · Новосибирск*\n\nФинтех: платежи для малого бизнеса.\n" in md
        assert "\n## Образование\n\nНГТУ, прикладная математика, 2016 — 2020\n" in md

    def test_old_profiles_without_new_fields_render_as_before(self):
        md = to_markdown(profile_document(PROFILE)).decode()
        assert "О себе" not in md
        assert "*2021–2025*" in md

    def test_markdown_contacts_on_one_line(self):
        md = to_markdown(profile_document(PROFILE, contacts=self.CONTACTS)).decode()
        assert "\nivan@mail.ru · @ivan\\_dev · github.com/ivandev\n" in md

    def test_docx_contacts_on_one_line(self):
        import docx

        data = to_docx(profile_document(PROFILE, contacts=self.CONTACTS), "graphite")
        paragraphs = [p.text for p in docx.Document(BytesIO(data)).paragraphs]
        assert "ivan@mail.ru · @ivan_dev · github.com/ivandev" in paragraphs

    def test_tailored_resume_carries_contacts_and_education(self):
        doc = tailored_resume_document(TAILORED, profile=self.FULL, contacts=self.CONTACTS)
        assert [b.kind for b in doc.blocks].count("contact") == 3
        assert "Образование" in texts(doc)

    def test_graphite_draws_icons_in_its_colours(self):
        css = get_style("graphite").stylesheet()
        assert ".contact--telegram::before" in css
        assert "%2329a8e0" in css  # "#29a8e0", url-encoded inside the data URI
        assert "IBM Plex Sans" in css
