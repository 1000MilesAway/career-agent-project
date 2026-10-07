"""The user's portrait for the resume header.

Only PNG and JPEG are accepted, and the type is read from the bytes, not from
the file name or the browser's Content-Type: both are whatever the client says.
The two formats are the ones every renderer here draws — WeasyPrint for the
PDF and python-docx, which cannot place a WebP or HEIC at all.

The cabinet downsizes the picture in the browser before upload (a canvas
re-encode), so a 12 MB phone shot arrives as a few hundred kilobytes. The
server does not resize: that would need Pillow as a direct dependency, and the
size cap below is enough to keep a rogue client from filling the database.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

MAX_BYTES = 2 * 1024 * 1024

_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


class PhotoError(ValueError):
    """The upload is not a picture we can put on a resume. The text is for the user."""


@dataclass(frozen=True)
class Photo:
    data: bytes
    media_type: str

    def data_uri(self) -> str:
        """The picture inlined, so the HTML stays one self-contained page —
        WeasyPrint and the sandboxed preview iframe both fetch nothing."""
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.media_type};base64,{encoded}"


def sniff(data: bytes) -> str | None:
    """The media type the bytes actually are, or None."""
    for signature, media_type in _SIGNATURES:
        if data.startswith(signature):
            return media_type
    return None


def validate(data: bytes) -> Photo:
    if not data:
        raise PhotoError("Файл пустой")
    if len(data) > MAX_BYTES:
        raise PhotoError("Фото больше 2 МБ")
    media_type = sniff(data)
    if media_type is None:
        raise PhotoError("Поддерживаются фото в JPEG и PNG")
    _check_header(data)
    return Photo(data=data, media_type=media_type)


def _check_header(data: bytes) -> None:
    """Refuse a file whose signature is right but whose header is not.

    python-docx reads the header to size the picture and raises on a
    truncated one — which, unchecked, turned every later DOCX download into a
    500. Parsing it once at upload, with the same reader, is the check that
    matches. (WeasyPrint skips an unreadable image with a warning instead.)
    """
    from docx.image.image import Image  # noqa: PLC0415

    try:
        image = Image.from_blob(data)
    except Exception:  # noqa: BLE001 — any parser error means "not a usable picture"
        raise PhotoError("Файл повреждён или это не фото") from None
    if not image.px_width or not image.px_height:
        raise PhotoError("Файл повреждён или это не фото")
