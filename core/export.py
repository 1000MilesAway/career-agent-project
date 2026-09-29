"""Rendering a resume into a file the user can send to an employer.

Three formats out of one document model. The model (`Document`) is a flat list
of blocks, not HTML and not a .docx tree: an HTML template that DOCX has to
re-parse, or a .docx built by walking the same dict a second time, is how the
two formats drift until only one of them is correct.

The layout is deliberately plain — one column, no tables, no text boxes, no
columns, no header/footer. ATS parsers read the text stream, and a two-column
CV reaches them as interleaved gibberish. This is the same conclusion
docs/03-architecture.md records for the PDF template.

Nothing here touches the database or the model: input is the JSON already
stored on `resumes.profile` or `artifacts.content`, so a rendered file costs
nothing and can be re-downloaded any number of times.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from io import BytesIO
from typing import Any, Literal

from core.contacts import Contact
from core.resume_styles import DEFAULT_STYLE, DocxTheme, get_style

Format = Literal["pdf", "docx", "md", "html"]

MEDIA_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    # The charset is stated: without it a browser opening the file may guess
    # a legacy encoding and show a Cyrillic CV as mojibake.
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
}


@dataclass
class Block:
    """One piece of the document. `kind` decides how each backend draws it.

    `href` and `icon` are used by "contact" only: the address a contact links
    to and which glyph a style may draw next to it.
    """

    kind: Literal["h1", "subtitle", "contact", "h2", "h3", "meta", "para", "bullet"]
    text: str
    href: str | None = None
    icon: str | None = None


@dataclass
class Document:
    """A rendered-ready resume: a title and a flat block list."""

    title: str
    blocks: list[Block] = field(default_factory=list)

    def add_contacts(self, contacts: list[Contact]) -> None:
        for c in contacts:
            self.blocks.append(Block(kind="contact", text=c.label, href=c.href, icon=c.kind))

    def add(self, kind: str, text: Any) -> None:
        """Append a block, skipping anything empty.

        Empty blocks are dropped here rather than at each call site: a CV with
        no `field` must not print a bare "Сфера" with nothing after it, and
        checking that in twenty places is how one of them gets forgotten.
        """
        value = "" if text is None else str(text).strip()
        if not value:
            return
        self.blocks.append(Block(kind=kind, text=value))  # type: ignore[arg-type]


def _join(items: Any) -> str:
    """A list of strings as one comma-separated line, ignoring junk entries."""
    if not isinstance(items, list):
        return ""
    return ", ".join(str(i).strip() for i in items if str(i or "").strip())


def _items(value: Any) -> list[dict[str, Any]]:
    """Only the dict entries of a list.

    The model returns these structures, and a malformed element must not take
    the whole download down with an AttributeError.
    """
    if not isinstance(value, list):
        return []
    return [i for i in value if isinstance(i, dict)]


# --- документы ------------------------------------------------------------


def _str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _add_experience(doc: Document, items: list[dict[str, Any]], bullets_key: str) -> None:
    """One job: title, "period · place", what the company does, then bullets."""
    for item in items:
        role = _str(item.get("role"))
        company = _str(item.get("company"))
        doc.add("h3", ", ".join(p for p in (role, company) if p))
        doc.add("meta", " · ".join(p for p in (_str(item.get("period")),
                                                _str(item.get("location"))) if p))
        doc.add("para", item.get("description"))
        for bullet in item.get(bullets_key) or []:
            doc.add("bullet", bullet)


def _add_list_section(doc: Document, title: str, value: Any) -> None:
    """A heading over one paragraph per entry, skipped when there are none."""
    entries = [_str(v) for v in value] if isinstance(value, list) else []
    entries = [e for e in entries if e]
    if entries:
        doc.add("h2", title)
        for entry in entries:
            doc.add("para", entry)


def profile_document(
    profile: dict[str, Any], *, contacts: list[Contact] | None = None
) -> Document:
    """The parsed CV as a resume: what `resumes.profile` holds.

    This is the CV the user uploaded, read back through the model. It is not
    the original file — the original is what they already have on disk, and
    handing it back would be a download button that returns its own input.

    `contacts` come from the raw text (`core.contacts`), not from the profile:
    the model is never asked for them.
    """
    profile = profile or {}
    name = _str(profile.get("name")) or "Резюме"
    doc = Document(title=name)

    doc.add("h1", name)
    doc.add("subtitle", profile.get("target_role"))
    doc.add_contacts(contacts or [])

    meta: list[str] = []
    seniority = _str(profile.get("seniority"))
    if seniority:
        meta.append(f"Уровень: {seniority}")
    years = profile.get("years_experience")
    if isinstance(years, int | float) and years > 0:
        meta.append(f"Опыт: {years:g} лет")
    field_name = _str(profile.get("field"))
    if field_name:
        meta.append(f"Сфера: {field_name}")
    doc.add("meta", " · ".join(meta))

    summary = _str(profile.get("summary"))
    if summary:
        doc.add("h2", "О себе")
        doc.add("para", summary)

    directions = _join(profile.get("directions"))
    if directions:
        doc.add("h2", "Направления")
        doc.add("para", directions)

    skills = _join(profile.get("skills"))
    if skills:
        doc.add("h2", "Навыки")
        doc.add("para", skills)

    experience = _items(profile.get("experience"))
    if experience:
        doc.add("h2", "Опыт работы")
        _add_experience(doc, experience, "achievements")

    _add_list_section(doc, "Образование", profile.get("education"))

    languages = _join(profile.get("languages"))
    if languages:
        doc.add("h2", "Языки")
        doc.add("para", languages)

    return doc


def tailored_resume_document(
    content: dict[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    vacancy_title: str | None = None,
    contacts: list[Contact] | None = None,
) -> Document:
    """The tailored CV: `artifacts.content` for kind `tailor`.

    The name, contacts and education come from the stored profile, because the
    tailoring prompt returns the repackaged content and not the identity — a
    CV that arrives at an employer with no name on it is not a CV.

    `changes` is deliberately NOT rendered. It is the model explaining what it
    rewrote, which is how the user checks it for invention; putting it into
    the file that goes to the employer would send them the audit trail.
    """
    content = content or {}
    profile = profile or {}

    name = _str(profile.get("name")) or "Резюме"
    doc = Document(title=name)

    doc.add("h1", name)
    doc.add("subtitle", profile.get("target_role"))
    doc.add_contacts(contacts or [])
    if vacancy_title:
        doc.add("meta", f"Под вакансию: {vacancy_title}")

    summary = _str(content.get("summary"))
    if summary:
        doc.add("h2", "О себе")
        doc.add("para", summary)

    experience = _items(content.get("experience"))
    if experience:
        doc.add("h2", "Опыт работы")
        _add_experience(doc, experience, "bullets")

    skills = _join(content.get("skills"))
    if skills:
        doc.add("h2", "Навыки")
        doc.add("para", skills)

    keywords = _join(content.get("ats_keywords"))
    if keywords:
        # Kept as a normal line rather than hidden white-on-white text: an ATS
        # that detects keyword stuffing rejects the candidate outright.
        doc.add("h2", "Ключевые слова")
        doc.add("para", keywords)

    _add_list_section(doc, "Образование", profile.get("education"))

    return doc


# --- имя файла ------------------------------------------------------------

_UNSAFE = re.compile(r"[^\w\-.]+", re.UNICODE)


def filename(stem: str, fmt: Format) -> str:
    """An ASCII-safe-ish file name.

    Cyrillic survives — the header sends it as RFC 5987 `filename*` — but
    slashes, quotes and control characters do not, because those are what turn
    a Content-Disposition into a header-injection.
    """
    clean = _UNSAFE.sub("_", (stem or "resume").strip()).strip("._")
    return f"{clean or 'resume'}.{fmt}"


# Only what a Russian CV actually contains. A full transliteration table would
# be a library; this is the fallback name a modern browser never shows.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def ascii_filename(name: str) -> str:
    """The plain `filename=` fallback, for a client that ignores `filename*`.

    Replacing each non-ASCII character with "_" is what the obvious
    implementation does, and it turns "Иванов Иван.docx" into a row of
    underscores — a downloads folder full of files the user cannot tell apart.
    Transliterating keeps the name readable.
    """
    out: list[str] = []
    for char in name:
        if char.isascii():
            out.append(char)
            continue
        lower = char.lower()
        mapped = _TRANSLIT.get(lower)
        if mapped is None:
            out.append("_")
        elif char.isupper():
            out.append(mapped.capitalize())
        else:
            out.append(mapped)
    clean = _UNSAFE.sub("_", "".join(out)).strip("._")
    # The extension survives _UNSAFE (a dot is allowed), so only a name that
    # was entirely non-Latin can come back empty.
    return clean or "resume"


# --- HTML / PDF ---------------------------------------------------------

# The look lives in `core/resume_styles.py`; this function only decides the
# markup every style shares. Two wrappers are added over the flat block list,
# and they are the reason a style can be more than a font change:
#
# - `.resume-header` holds the leading name / role / meta, so a style can draw
#   a band behind them. The template this came from built that band with JS
#   after load; WeasyPrint runs no JS, so it has to exist in the markup.
# - `.job` holds a job title with the date line under it, so a page break
#   cannot separate the two.
#
# One column, no tables in any case: see the module docstring.

_HEADER_KINDS = {"h1", "subtitle", "contact", "meta"}

# Only these schemes become links. The href comes from `core.contacts`, which
# builds it from a fixed prefix, but a `javascript:` URL in a file that goes
# to an employer is not a risk worth one missed check.
_SAFE_HREF = ("mailto:", "tel:", "https://", "http://")


def _contact_html(block: Block) -> str:
    """One contact. Its icon, if any, is the style's business: see
    `contact_icons` in core/resume_styles.py for why it is not an <svg> here."""
    cls = f"contact contact--{escape(block.icon or 'link')}"
    label = escape(block.text)
    href = block.href or ""
    if href.startswith(_SAFE_HREF):
        return f'<a class="{cls}" href="{escape(href)}">{label}</a>'
    return f'<span class="{cls}">{label}</span>'


def to_html(doc: Document, style: str = DEFAULT_STYLE) -> str:
    """The document as a standalone HTML page: the PDF source and the preview."""
    theme = get_style(style)
    parts: list[str] = []
    in_list = False
    blocks = doc.blocks

    header_end = 0
    if blocks and blocks[0].kind == "h1":
        header_end = 1
        while header_end < len(blocks) and blocks[header_end].kind in _HEADER_KINDS - {"h1"}:
            header_end += 1

    def draw(block: Block) -> str:
        text = escape(block.text)
        if block.kind == "h1":
            return f"<h1>{text}</h1>"
        if block.kind == "subtitle":
            return f'<p class="subtitle">{text}</p>'
        if block.kind == "h2":
            return f"<h2>{text}</h2>"
        if block.kind == "h3":
            return f"<h3>{text}</h3>"
        if block.kind == "meta":
            return f'<p class="meta">{text}</p>'
        return f"<p>{text}</p>"

    if header_end:
        parts.append('<header class="resume-header">')
        header = blocks[:header_end]
        contacts = [b for b in header if b.kind == "contact"]
        placed = False
        for b in header:
            if b.kind != "contact":
                parts.append(draw(b))
            elif not placed:
                # One line, in the order found. The separator is real text,
                # so the extracted stream reads "a · b", not "ab"; a style
                # that draws icons hides it.
                sep = '<span class="sep"> · </span>'
                parts.append(
                    '<p class="contacts">' + sep.join(_contact_html(c) for c in contacts) + "</p>"
                )
                placed = True
        parts.append("</header>")

    i = header_end
    while i < len(blocks):
        block = blocks[i]
        if block.kind == "bullet":
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{escape(block.text)}</li>")
            i += 1
            continue
        if in_list:
            parts.append("</ul>")
            in_list = False

        if block.kind == "h3":
            parts.append('<div class="job">')
            parts.append(draw(block))
            if i + 1 < len(blocks) and blocks[i + 1].kind == "meta":
                i += 1
                parts.append(draw(blocks[i]))
            parts.append("</div>")
        else:
            parts.append(draw(block))
        i += 1

    if in_list:
        parts.append("</ul>")

    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f"<title>{escape(doc.title)}</title><style>{theme.stylesheet()}</style></head>"
        f"<body>{''.join(parts)}</body></html>"
    )


def to_pdf(doc: Document, style: str = DEFAULT_STYLE) -> bytes:
    """Render to PDF.

    WeasyPrint is imported here, not at module scope, for two reasons: it pulls
    in Pango and Cairo through cffi at import time, which costs ~1s of startup
    the API pays on every boot for a feature used rarely; and its absence then
    breaks only the download instead of the whole application.
    """
    from weasyprint import HTML  # noqa: PLC0415

    return HTML(string=to_html(doc, style)).write_pdf()


# --- DOCX -----------------------------------------------------------------


def _apply_docx_theme(document: Any, theme: DocxTheme) -> None:
    """Carry a style into Word through the built-in styles, not per-run formatting.

    Changing "Normal" and "Heading N" keeps the structure an ATS reads and
    lets the user restyle the file in Word afterwards; formatting each run by
    hand would freeze the look into hundreds of overrides.
    """
    from docx.oxml.ns import qn  # noqa: PLC0415
    from docx.shared import Pt, RGBColor  # noqa: PLC0415

    def font(style_name: str, size: float, color: str, bold: bool | None = None) -> None:
        style = document.styles[style_name]
        style.font.name = theme.font
        # Word picks the Cyrillic face from the east-Asian/complex slots of
        # rFonts, not from `name`; without these a Russian CV stays in the
        # theme font whatever the style says.
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = rpr.makeelement(qn("w:rFonts"), {})
            rpr.append(rfonts)
        for slot in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(slot), theme.font)
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor.from_string(color)
        if bold is not None:
            style.font.bold = bold

    font("Normal", theme.body_pt, "222222")
    font("Title", theme.name_pt, theme.heading_color, bold=True)
    font("Heading 1", theme.h2_pt, theme.heading_color, bold=True)
    font("Heading 2", theme.h3_pt, theme.heading_color, bold=True)
    font("List Bullet", theme.body_pt, "222222")


def to_docx(doc: Document, style: str = DEFAULT_STYLE) -> bytes:
    """Render to DOCX using the built-in styles, tinted by the chosen style.

    Built-in style names ("Heading 1", "List Bullet") rather than hand-rolled
    formatting: Word maps them to its own outline, and an ATS reading the
    document sees a heading where a heading is meant, not 14pt bold text.
    """
    import docx  # noqa: PLC0415
    from docx.shared import Pt, RGBColor  # noqa: PLC0415

    theme = get_style(style).docx
    document = docx.Document()
    document.core_properties.title = doc.title
    _apply_docx_theme(document, theme)

    contacts = " · ".join(b.text for b in doc.blocks if b.kind == "contact")
    for block in doc.blocks:
        if block.kind == "contact":
            # All contacts on one line, where the first one stood. The labels
            # are the addresses themselves, so no hyperlink is needed for the
            # reader — python-docx has no API for one short of raw XML.
            if contacts:
                run = document.add_paragraph().add_run(contacts)
                run.font.size = Pt(theme.body_pt - 0.5)
                run.font.color.rgb = RGBColor.from_string(theme.muted_color)
                contacts = ""
        elif block.kind == "h1":
            document.add_heading(block.text, level=0)
        elif block.kind == "h2":
            document.add_heading(block.text, level=1)
        elif block.kind == "h3":
            document.add_heading(block.text, level=2)
        elif block.kind == "bullet":
            document.add_paragraph(block.text, style="List Bullet")
        elif block.kind == "subtitle":
            run = document.add_paragraph().add_run(block.text)
            run.bold = True
            run.font.size = Pt(theme.body_pt + 1.5)
            run.font.color.rgb = RGBColor.from_string(theme.accent_color)
        elif block.kind == "meta":
            run = document.add_paragraph().add_run(block.text)
            run.italic = True
            run.font.size = Pt(theme.body_pt - 0.5)
            run.font.color.rgb = RGBColor.from_string(theme.muted_color)
        else:
            document.add_paragraph(block.text)

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# --- Markdown -------------------------------------------------------------

# A line that starts like a block marker — "# ", "- ", "1. ", "> " — would be
# re-read as a heading or a list item. Only the start of the line and the few
# inline markers are escaped: backslashes before every dot and dash make the
# raw file, which is what most people will actually read, look broken.
_MD_BLOCK_START = re.compile(r"^([#>+\-*=|]|\d+[.)])")
_MD_INLINE = re.compile(r"([\\`*_\[\]<])")


def _md_line(text: str) -> str:
    """One block's text as a single Markdown line, structure-safe."""
    line = " ".join(text.split())  # a stray newline would split the block
    line = _MD_INLINE.sub(r"\\\1", line)
    return _MD_BLOCK_START.sub(r"\\\1", line)


def to_markdown(doc: Document) -> bytes:
    """Render to Markdown: plain text that pastes into a job-board form.

    A blank line between blocks, except inside a run of bullets, so a list
    stays one list instead of a sequence of one-item lists.
    """
    lines: list[str] = []
    previous: str | None = None

    for block in doc.blocks:
        text = _md_line(block.text)
        if block.kind == "contact" and previous == "contact":
            # Contacts join the line the first one opened.
            lines[-1] += f" · {text}"
            continue
        if lines and not (block.kind == "bullet" and previous == "bullet"):
            lines.append("")
        if block.kind == "h1":
            lines.append(f"# {text}")
        elif block.kind == "h2":
            lines.append(f"## {text}")
        elif block.kind == "h3":
            lines.append(f"### {text}")
        elif block.kind == "bullet":
            lines.append(f"- {text}")
        elif block.kind == "subtitle":
            lines.append(f"**{text}**")
        elif block.kind == "meta":
            lines.append(f"*{text}*")
        else:
            lines.append(text)
        previous = block.kind

    return ("\n".join(lines) + "\n").encode("utf-8")


def render(doc: Document, fmt: Format, style: str = DEFAULT_STYLE) -> bytes:
    """The file in `fmt`. Markdown ignores `style`: it is plain text by design."""
    if fmt == "md":
        return to_markdown(doc)
    if fmt == "pdf":
        return to_pdf(doc, style)
    if fmt == "docx":
        return to_docx(doc, style)
    if fmt == "html":
        return to_html(doc, style).encode("utf-8")
    raise ValueError(f"unknown format: {fmt}")
