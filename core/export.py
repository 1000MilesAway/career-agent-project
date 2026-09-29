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

Format = Literal["pdf", "docx", "md"]

MEDIA_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    # The charset is stated: without it a browser opening the file may guess
    # a legacy encoding and show a Cyrillic CV as mojibake.
    "md": "text/markdown; charset=utf-8",
}


@dataclass
class Block:
    """One piece of the document. `kind` decides how each backend draws it."""

    kind: Literal["h1", "subtitle", "h2", "h3", "meta", "para", "bullet"]
    text: str


@dataclass
class Document:
    """A rendered-ready resume: a title and a flat block list."""

    title: str
    blocks: list[Block] = field(default_factory=list)

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


def profile_document(profile: dict[str, Any]) -> Document:
    """The parsed CV as a resume: what `resumes.profile` holds.

    This is the CV the user uploaded, read back through the model. It is not
    the original file — the original is what they already have on disk, and
    handing it back would be a download button that returns its own input.
    """
    profile = profile or {}
    name = str(profile.get("name") or "").strip() or "Резюме"
    doc = Document(title=name)

    doc.add("h1", name)
    doc.add("subtitle", profile.get("target_role"))

    meta: list[str] = []
    seniority = str(profile.get("seniority") or "").strip()
    if seniority:
        meta.append(f"Уровень: {seniority}")
    years = profile.get("years_experience")
    if isinstance(years, int | float) and years > 0:
        meta.append(f"Опыт: {years:g} лет")
    field_name = str(profile.get("field") or "").strip()
    if field_name:
        meta.append(f"Сфера: {field_name}")
    doc.add("meta", " · ".join(meta))

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
        for item in experience:
            role = str(item.get("role") or "").strip()
            company = str(item.get("company") or "").strip()
            doc.add("h3", " — ".join(p for p in (role, company) if p))
            doc.add("meta", item.get("period"))
            for achievement in item.get("achievements") or []:
                doc.add("bullet", achievement)

    return doc


def tailored_resume_document(
    content: dict[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    vacancy_title: str | None = None,
) -> Document:
    """The tailored CV: `artifacts.content` for kind `tailor`.

    The name and the contact line come from the stored profile, because the
    tailoring prompt returns the repackaged content and not the identity — a
    CV that arrives at an employer with no name on it is not a CV.

    `changes` is deliberately NOT rendered. It is the model explaining what it
    rewrote, which is how the user checks it for invention; putting it into
    the file that goes to the employer would send them the audit trail.
    """
    content = content or {}
    profile = profile or {}

    name = str(profile.get("name") or "").strip() or "Резюме"
    doc = Document(title=name)

    doc.add("h1", name)
    doc.add("subtitle", profile.get("target_role"))
    if vacancy_title:
        doc.add("meta", f"Под вакансию: {vacancy_title}")

    summary = str(content.get("summary") or "").strip()
    if summary:
        doc.add("h2", "О себе")
        doc.add("para", summary)

    experience = _items(content.get("experience"))
    if experience:
        doc.add("h2", "Опыт работы")
        for item in experience:
            role = str(item.get("role") or "").strip()
            company = str(item.get("company") or "").strip()
            doc.add("h3", " — ".join(p for p in (role, company) if p))
            doc.add("meta", item.get("period"))
            for bullet in item.get("bullets") or []:
                doc.add("bullet", bullet)

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


# --- PDF ------------------------------------------------------------------

# One column, no tables, generous line height. The font stack stays on the
# system serif/sans rather than an embedded family: the container has DejaVu,
# which covers Cyrillic, and an embedded font would add megabytes to the image
# for a layout nobody is judging on typography.
PDF_CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: "DejaVu Sans", sans-serif; font-size: 10.5pt; line-height: 1.45; color: #111; }
h1 { font-size: 20pt; margin: 0 0 2pt; }
.subtitle { font-size: 12pt; color: #333; margin: 0 0 6pt; }
h2 { font-size: 12pt; margin: 14pt 0 4pt; border-bottom: 0.6pt solid #999; padding-bottom: 2pt; }
h3 { font-size: 11pt; margin: 9pt 0 1pt; }
.meta { color: #555; font-size: 9.5pt; margin: 0 0 3pt; }
p { margin: 0 0 4pt; }
ul { margin: 2pt 0 6pt; padding-left: 4pt; }
/* `inside` puts the bullet into the same text run as the line it belongs to.
   With the default `outside` the markers are drawn separately and land at the
   END of the extracted text stream — the PDF looks right and an ATS reads a
   block of bullet-less lines followed by a row of stray dots. Verified by
   extracting both variants. */
li { margin: 0 0 2pt; list-style-position: inside; }
"""


def to_html(doc: Document) -> str:
    """The document as a standalone HTML page. Also what WeasyPrint renders."""
    parts: list[str] = []
    in_list = False

    for block in doc.blocks:
        if block.kind == "bullet":
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{escape(block.text)}</li>")
            continue
        if in_list:
            parts.append("</ul>")
            in_list = False

        text = escape(block.text)
        if block.kind == "h1":
            parts.append(f"<h1>{text}</h1>")
        elif block.kind == "subtitle":
            parts.append(f'<p class="subtitle">{text}</p>')
        elif block.kind == "h2":
            parts.append(f"<h2>{text}</h2>")
        elif block.kind == "h3":
            parts.append(f"<h3>{text}</h3>")
        elif block.kind == "meta":
            parts.append(f'<p class="meta">{text}</p>')
        else:
            parts.append(f"<p>{text}</p>")

    if in_list:
        parts.append("</ul>")

    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f"<title>{escape(doc.title)}</title><style>{PDF_CSS}</style></head>"
        f"<body>{''.join(parts)}</body></html>"
    )


def to_pdf(doc: Document) -> bytes:
    """Render to PDF.

    WeasyPrint is imported here, not at module scope, for two reasons: it pulls
    in Pango and Cairo through cffi at import time, which costs ~1s of startup
    the API pays on every boot for a feature used rarely; and its absence then
    breaks only the download instead of the whole application.
    """
    from weasyprint import HTML  # noqa: PLC0415

    return HTML(string=to_html(doc)).write_pdf()


# --- DOCX -----------------------------------------------------------------


def to_docx(doc: Document) -> bytes:
    """Render to DOCX using the built-in styles.

    Built-in style names ("Heading 1", "List Bullet") rather than hand-rolled
    formatting: Word maps them to its own outline, and an ATS reading the
    document sees a heading where a heading is meant, not 14pt bold text.
    """
    import docx  # noqa: PLC0415
    from docx.shared import Pt  # noqa: PLC0415

    document = docx.Document()
    document.core_properties.title = doc.title

    for block in doc.blocks:
        if block.kind == "h1":
            document.add_heading(block.text, level=0)
        elif block.kind == "h2":
            document.add_heading(block.text, level=1)
        elif block.kind == "h3":
            document.add_heading(block.text, level=2)
        elif block.kind == "bullet":
            document.add_paragraph(block.text, style="List Bullet")
        elif block.kind in ("subtitle", "meta"):
            paragraph = document.add_paragraph()
            run = paragraph.add_run(block.text)
            run.italic = True
            run.font.size = Pt(10) if block.kind == "meta" else Pt(12)
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


def render(doc: Document, fmt: Format) -> bytes:
    if fmt == "md":
        return to_markdown(doc)
    if fmt == "pdf":
        return to_pdf(doc)
    if fmt == "docx":
        return to_docx(doc)
    raise ValueError(f"unknown format: {fmt}")
