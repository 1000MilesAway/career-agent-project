"""Visual presets for the resume download.

A style is two things that must describe the same look: a stylesheet for the
HTML that WeasyPrint turns into a PDF (and that the cabinet shows as the
preview), and a handful of values for the DOCX, because Word does not read
CSS. Keeping both in one record is what stops "Акцент" from being orange in
the PDF and black in the .docx.

Every style styles the same markup from `core.export.to_html`. A preset may
change type, colour and spacing, never the structure: the text stream an ATS
extracts has to be identical whichever style the user picked, so the choice
is purely visual and cannot cost them a parse.

Fonts: the image ships DejaVu Sans only (see Dockerfile). A family named
first in a stack is used when the viewer has it — the browser preview on a
machine with Inter — and DejaVu otherwise, which is what the PDF gets. The
fallback is stated explicitly so the preview and the file land on the same
face where they can.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

# Shared by every preset. `inside` puts the bullet into the same text run as
# the line it belongs to: with `outside` the markers land at the END of the
# extracted text stream — the PDF looks right and an ATS reads bullet-less
# lines followed by a row of stray dots. Verified by extracting both variants.
_BASE_CSS = """
* { box-sizing: border-box; }
li { list-style-position: inside; }
a { color: inherit; text-decoration: none; }
h2 { break-after: avoid; page-break-after: avoid; }
.job { break-inside: avoid; page-break-inside: avoid; }
.job + ul, .job + p { break-before: avoid; page-break-before: avoid; }
li { break-inside: avoid; page-break-inside: avoid; }
p { orphans: 2; widows: 2; }
/* Contacts: plain text with " · " between them unless a style opts into
   icons through `contact_icons`. */
.contacts { margin: 0; }
/* The portrait, when the user asked for one (`core.export._photo_html`).
   Floated or a table cell, never `position`ed: see `_photo_html`. The header
   holds its float through `flow-root`, not `overflow: hidden` — WeasyPrint
   paints a clipping box as a layer of its own, and that too moved the name
   and contacts to the end of the text stream. Verified by extraction. */
.resume-header--photo { display: flow-root; }
.photo { background-repeat: no-repeat; background-size: cover;
         background-position: center top; }
"""

# Screen-only: the cabinet shows this page in an A4-wide iframe and draws the
# sheet's shadow itself. The body gets the @page margins as padding (see
# `screen_padding`) so the preview has the margins the PDF will. WeasyPrint
# renders with the print medium and never applies any of it.
_SCREEN_CSS = """
@media screen {
  html, body { background: #fff; }
  body { width: 210mm; min-height: 297mm; margin: 0; }
}
"""


# Contact glyphs: 24x24 path data, the same set as the user's pandoc template
# the dark-header style was taken from.
_ICONS: dict[str, str] = {
    "email": (
        "M20 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2Zm0 2v.01"
        "L12 13 4 6.01V6h16ZM4 18V8.24l7.38 6.46a1 1 0 0 0 1.24 0L20 8.24V18H4Z"
    ),
    "phone": (
        "M6.62 10.79a15.05 15.05 0 0 0 6.59 6.59l2.2-2.2a1 1 0 0 1 1.01-.24c1.12.37 2.33."
        "57 3.58.57a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1C10.07 21 3 13.93 3 5a1 1 0 0 1 1-1h3.5"
        "a1 1 0 0 1 1 1c0 1.25.2 2.46.57 3.58a1 1 0 0 1-.25 1.01l-2.2 2.2Z"
    ),
    "telegram": (
        "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2Zm4.64 6.8c-.15"
        " 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1"
        ".38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 0 0-"
        ".05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.3"
        "6-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.74-.55 2.92-1.27 4"
        ".86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14"
        ".27-.01.06.01.24 0 .38Z"
    ),
    "github": (
        "M12 2A10 10 0 0 0 2 12c0 4.42 2.87 8.17 6.84 9.5.5.08.66-.23.66-.5v-1.7c-2.78.6-"
        "3.37-1.34-3.37-1.34-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 "
        "1.03 1.53 1.03.9 1.53 2.34 1.09 2.91.84.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.1"
        "1-4.55-4.92 0-1.11.38-2 1.03-2.71-.1-.25-.45-1.29.1-2.64 0 0 .84-.27 2.75 1.02A9"
        ".58 9.58 0 0 1 12 6.8c.85.004 1.71.115 2.51.337 1.91-1.29 2.75-1.02 2.75-1.02.55"
        " 1.35.2 2.39.1 2.64.65.71 1.03 1.6 1.03 2.71 0 3.82-2.34 4.66-4.57 4.91.36.31.68"
        ".92.68 1.85v2.75c0 .27.16.59.67.5A10 10 0 0 0 22 12 10 10 0 0 0 12 2Z"
    ),
    "linkedin": (
        "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2Zm6.92 6h-2.95a"
        "15.6 15.6 0 0 0-1.38-3.56A8.03 8.03 0 0 1 18.92 8ZM12 4.04c.83 1.2 1.48 2.53 1.9"
        "1 3.96h-3.82c.43-1.43 1.08-2.76 1.91-3.96ZM4.26 14A8.09 8.09 0 0 1 4 12c0-.69.1-"
        "1.36.26-2h3.38a16.5 16.5 0 0 0 0 4H4.26Zm.82 2h2.95c.32 1.25.78 2.45 1.38 3.56A7"
        ".99 7.99 0 0 1 5.08 16Zm2.95-8H5.08a7.99 7.99 0 0 1 4.33-3.56A15.6 15.6 0 0 0 8."
        "03 8ZM12 19.96A13.9 13.9 0 0 1 10.09 16h3.82A13.9 13.9 0 0 1 12 19.96ZM14.34 14H"
        "9.66a14.7 14.7 0 0 1 0-4h4.68a14.7 14.7 0 0 1 0 4Zm.25 5.56c.6-1.11 1.06-2.31 1."
        "38-3.56h2.95a7.99 7.99 0 0 1-4.33 3.56ZM16.36 14a16.5 16.5 0 0 0 0-4h3.38c.16.64"
        ".26 1.31.26 2 0 .69-.1 1.36-.26 2h-3.38Z"
    ),
}


def contact_icons(color: str, overrides: dict[str, str] | None = None) -> str:
    """CSS that draws an icon before each contact, in a fixed colour.

    A background image, not an inline <svg> in the markup: WeasyPrint draws
    inline SVG with its own defaults and ignores both a stylesheet `fill` and
    `currentColor` — every icon came out black on the dark header. Verified
    by rendering all three variants. A data-URI image carries its colour
    inside, so the style decides it and the markup stays the same for all.
    """
    overrides = overrides or {}
    rules = [
        ".contact::before { content: ''; display: inline-block; width: 14px; height: 14px;"
        " margin-right: 7px; vertical-align: -3px;"
        " background: no-repeat center / contain; }"
    ]
    for kind, path in _ICONS.items():
        fill = overrides.get(kind, color)
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            f'<path fill="{fill}" d="{path}"/></svg>'
        )
        rules.append(
            f'.contact--{kind}::before {{ background-image: url("data:image/svg+xml,{quote(svg)}"); }}'
        )
    return "\n".join(rules) + "\n"


@dataclass(frozen=True)
class DocxTheme:
    """What a .docx can carry of a style: one family, sizes, one accent."""

    font: str
    body_pt: float
    name_pt: float
    h2_pt: float
    h3_pt: float
    # Hex without "#": python-docx's RGBColor.from_string wants exactly that.
    heading_color: str
    accent_color: str
    muted_color: str


@dataclass(frozen=True)
class ResumeStyle:
    id: str
    name: str
    description: str
    css: str
    # Padding the screen frame adds around the body, so the preview shows the
    # same margins the PDF gets from @page. A style whose @page margin is 0
    # pads the body itself and passes "0".
    screen_padding: str
    docx: DocxTheme

    def stylesheet(self) -> str:
        return (
            _BASE_CSS
            + self.css
            + _SCREEN_CSS
            + f"@media screen {{ body {{ padding: {self.screen_padding}; }} }}"
        )


CLASSIC = ResumeStyle(
    id="classic",
    name="Классический",
    description="Спокойный одноколоночный лист без цвета. Подходит везде.",
    css="""
@page { size: A4; margin: 18mm 16mm; }
body { font-family: "DejaVu Sans", sans-serif; font-size: 10.5pt; line-height: 1.45;
       color: #111; margin: 0; }
.resume-header { margin: 0 0 6pt; }
h1 { font-size: 20pt; margin: 0 0 2pt; }
.subtitle { font-size: 12pt; color: #333; margin: 0 0 6pt; }
h2 { font-size: 12pt; margin: 14pt 0 4pt; border-bottom: 0.6pt solid #999; padding-bottom: 2pt; }
h3 { font-size: 11pt; margin: 9pt 0 1pt; }
.meta { color: #555; font-size: 9.5pt; margin: 0 0 3pt; }
.contacts { color: #333; font-size: 9.5pt; margin: 0 0 3pt; }
p { margin: 0 0 4pt; }
ul { margin: 2pt 0 6pt; padding-left: 4pt; }
li { margin: 0 0 2pt; }
.photo { float: right; width: 25mm; height: 32mm; margin: 0 0 4pt 6mm; border-radius: 2pt; }
""",
    screen_padding="18mm 16mm",
    docx=DocxTheme(
        font="Calibri",
        body_pt=10.5,
        name_pt=22,
        h2_pt=13,
        h3_pt=11,
        heading_color="111111",
        accent_color="333333",
        muted_color="555555",
    ),
)

ACCENT = ResumeStyle(
    id="accent",
    name="Акцент",
    description="Серая шапка во всю ширину, должность — оранжевой плашкой.",
    css="""
/* The header band runs edge to edge, so the first page has no top or side
   margin; later pages get a top margin back, or their text would start at
   the paper's edge. */
@page { size: A4; margin: 12mm 0 14mm; }
@page :first { margin-top: 0; }
body { font-family: "Inter", "Segoe UI", "DejaVu Sans", sans-serif; font-size: 10px;
       line-height: 1.6; color: #2a2a2a; margin: 0; padding: 0 44px;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
/* Plain blocks, not the template's flex column: WeasyPrint sizes a flex item
   to its min-content, and the name broke onto one word per line. */
.resume-header { display: block; background: #f2f2f2; padding: 26px 44px 22px;
                 margin: 0 -44px 22px; }
h1 { font-size: 30px; font-weight: 800; color: #1a1a1a; line-height: 1.1;
     letter-spacing: -0.4px; margin: 0 0 8px; }
.subtitle { display: inline-block; background: #e2611e; color: #fff; font-size: 11px;
            font-weight: 600; padding: 2px 10px 3px; border-radius: 3px; margin: 0; }
.contacts { font-size: 9.5px; color: #444; margin: 9px 0 0; line-height: 1.5; }
.resume-header .meta { font-size: 9.5px; color: #555; margin: 9px 0 0; line-height: 1.5; }
h2 { font-size: 13px; font-weight: 700; color: #1a1a1a; margin: 22px 0 10px;
     padding-bottom: 5px; border-bottom: 1.5px solid #d8d8d8; }
.job { margin-top: 16px; }
h3 { font-size: 10.5px; font-weight: 700; color: #1a1a1a; margin: 0 0 2px; }
.meta { color: #666; margin: 0 0 5px; }
p { margin: 0 0 5px; }
ul { padding: 0; margin: 7px 0 0; }
li { margin: 0 0 5px; }
li::marker { color: #e2611e; }
.photo { float: right; width: 90px; height: 112px; margin: 0 0 0 24px; border-radius: 4px; }
""",
    screen_padding="0 44px 44px",
    docx=DocxTheme(
        font="Inter",
        body_pt=10,
        name_pt=24,
        h2_pt=12,
        h3_pt=10.5,
        heading_color="1A1A1A",
        accent_color="E2611E",
        muted_color="666666",
    ),
)

COMPACT = ResumeStyle(
    id="compact",
    name="Компактный",
    description="Плотная вёрстка с узкими полями: длинный опыт помещается на меньше страниц.",
    css="""
@page { size: A4; margin: 11mm 12mm; }
html { font-size: 9.7pt; }
body { font-family: "Segoe UI", "Helvetica Neue", Arial, "DejaVu Sans", sans-serif;
       line-height: 1.33; color: #1a1a1a; margin: 0; }
.resume-header { margin: 0 0 1mm; }
h1 { font-size: 19pt; font-weight: 700; margin: 0 0 1.5mm; letter-spacing: -0.2px; }
.subtitle { font-weight: 600; margin: 0 0 1mm; }
h2 { font-size: 11.5pt; font-weight: 700; margin: 4mm 0 2mm; padding-bottom: 0.8mm;
     border-bottom: 1px solid #d0d0d0; }
h3 { font-size: 10.5pt; font-weight: 700; margin: 3mm 0 0.8mm; }
.job h3 { margin-top: 0; }
.job { margin-top: 3mm; }
.meta { color: #5a5a5a; font-size: 9pt; margin: 0 0 1mm; }
.contacts { font-size: 9pt; margin: 0 0 1mm; }
p { margin: 0 0 1.5mm; }
ul { margin: 0 0 2mm; padding-left: 1mm; }
li { margin-bottom: 0.9mm; }
.photo { float: right; width: 20mm; height: 26mm; margin: 0 0 1mm 5mm; border-radius: 1.5pt; }
""",
    screen_padding="11mm 12mm",
    docx=DocxTheme(
        font="Segoe UI",
        body_pt=9.5,
        name_pt=19,
        h2_pt=11.5,
        h3_pt=10.5,
        heading_color="1A1A1A",
        accent_color="1A1A1A",
        muted_color="5A5A5A",
    ),
)

GRAPHITE = ResumeStyle(
    id="graphite",
    name="Тёмная шапка",
    description="Графитовая шапка во всю ширину, должность светлой плашкой, контакты с иконками.",
    css="""
/* From the user's pandoc template. The header bleeds to the top and side
   edges of page one; later pages keep a top margin. Colours and sizes are
   the template's own, the portrait column included. */
@page { size: A4; margin: 26px 0; }
@page :first { margin: 0 0 26px; }
html { background: #fcf9f8; }
body { font-family: "IBM Plex Sans", "DejaVu Sans", sans-serif; font-size: 11.2px;
       line-height: 1.5; color: #0f141f; margin: 0; padding: 0 40px;
       background: #fcf9f8;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.resume-header { display: block; background: #342d2a; color: #f9f8f6;
                 padding: 22px 40px 20px; margin: 0 -40px 22px; }
h1 { font-size: 29px; font-weight: 700; color: #f9f8f6; line-height: 1.15; margin: 0 0 9px; }
.subtitle { display: inline-block; background: #fcf9f8; color: #342d2b; font-size: 16px;
            font-weight: 700; padding: 5px 13px; border-radius: 4px; margin: 0 0 11px; }
.contacts { font-size: 11px; line-height: 1.6; color: #f9f8f6; }
.contacts .sep { display: none; }
.contact { display: inline-block; margin: 0 17px 4px 0; white-space: nowrap; }
h2 { font-size: 15px; font-weight: 700; color: #0f141f; margin: 18px 0 9px; }
.resume-header + h2 { margin-top: 0; }
h2 + p, h2 ~ p { color: #2c2b29; }
p { margin: 0 0 4px; }
.job { margin-top: 14px; }
h2 + .job { margin-top: 0; }
h3 { font-size: 12.5px; font-weight: 700; color: #0f141f; margin: 0 0 2px; }
.job .meta { color: #2c2b29; margin: 0 0 5px; }
.job + p { color: #0f141f; margin: 0 0 7px; }
ul { padding: 0; margin: 0; }
li { margin: 0 0 3px; padding-left: 14px; text-indent: -10px; }
/* The template has no level/experience line: it is derived, not written. */
.resume-header .meta { display: none; }
/* The template's portrait column. The template takes it out of the flow
   with `position: absolute`, which here would move the header's text to the
   end of the PDF's text stream; two table cells give the same picture: the
   photo cell is as tall as the text beside it, and `contain` anchored
   left-bottom lets a cut-out grow to the header's full height without ever
   being clipped. The header's padding moves onto the text cell so the photo
   reaches the band's edges; 132 + 16 = the template's 148px text inset. */
.resume-header--photo { padding: 0; }
.photo { display: table-cell; width: 132px; min-width: 132px;
         background-size: contain; background-position: left bottom; }
.header-main { display: table-cell; vertical-align: top; padding: 22px 40px 20px 16px; }
"""
    + contact_icons("#f9f8f6", {"telegram": "#29a8e0"}),
    screen_padding="0 40px 26px",
    docx=DocxTheme(
        font="IBM Plex Sans",
        body_pt=10,
        name_pt=24,
        h2_pt=13,
        h3_pt=11,
        heading_color="0F141F",
        accent_color="342D2A",
        muted_color="2C2B29",
    ),
)

STYLES: dict[str, ResumeStyle] = {s.id: s for s in (CLASSIC, GRAPHITE, ACCENT, COMPACT)}
DEFAULT_STYLE = CLASSIC.id


def get_style(style_id: str | None) -> ResumeStyle:
    """The style by id; the default for None. Unknown ids raise KeyError.

    Unknown is an error rather than a silent default: a user who picked
    "Акцент" and got the classic sheet would think the choice is broken.
    """
    return STYLES[style_id or DEFAULT_STYLE]
