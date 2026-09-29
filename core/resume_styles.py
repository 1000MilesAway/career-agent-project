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
p { margin: 0 0 4pt; }
ul { margin: 2pt 0 6pt; padding-left: 4pt; }
li { margin: 0 0 2pt; }
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
p { margin: 0 0 1.5mm; }
ul { margin: 0 0 2mm; padding-left: 1mm; }
li { margin-bottom: 0.9mm; }
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

STYLES: dict[str, ResumeStyle] = {s.id: s for s in (CLASSIC, ACCENT, COMPACT)}
DEFAULT_STYLE = CLASSIC.id


def get_style(style_id: str | None) -> ResumeStyle:
    """The style by id; the default for None. Unknown ids raise KeyError.

    Unknown is an error rather than a silent default: a user who picked
    "Акцент" and got the classic sheet would think the choice is broken.
    """
    return STYLES[style_id or DEFAULT_STYLE]
