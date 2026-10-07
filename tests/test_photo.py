"""The resume portrait: what is accepted, and how it reaches the page."""

from __future__ import annotations

import base64
import struct
import zlib

import pytest

from core.photo import MAX_BYTES, PhotoError, sniff, validate


def png(width: int = 2, height: int = 3) -> bytes:
    """A real, minimal RGB PNG, built by hand: no imaging library is a dependency."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\x00" + b"\x80\x40\x20" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


# A baseline JFIF header up to the frame size: enough for the header reader.
JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000"
    "ffc0000b080003000201011100"
    "ffd9"
)
PNG = png()


class TestValidate:
    def test_png_and_jpeg_are_read_from_the_bytes(self):
        assert validate(PNG).media_type == "image/png"
        assert validate(JPEG).media_type == "image/jpeg"

    @pytest.mark.parametrize(
        "data",
        [
            b"GIF89a" + b"\x00" * 16,
            b"RIFF\x00\x00\x00\x00WEBPVP8 ",
            b"<svg xmlns='http://www.w3.org/2000/svg'/>",
            b"%PDF-1.7",
        ],
    )
    def test_other_formats_are_refused(self, data):
        """python-docx cannot place a WebP, and an SVG is markup, not a photo."""
        with pytest.raises(PhotoError, match="JPEG и PNG"):
            validate(data)

    def test_a_png_name_on_other_bytes_does_not_help(self):
        assert sniff(b"not an image") is None

    def test_a_right_signature_on_a_broken_header_is_refused(self):
        """Unchecked, this one crashed every DOCX download afterwards."""
        with pytest.raises(PhotoError, match="повреждён"):
            validate(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    def test_empty_is_refused(self):
        with pytest.raises(PhotoError, match="пустой"):
            validate(b"")

    def test_over_the_cap_is_refused(self):
        with pytest.raises(PhotoError, match="2 МБ"):
            validate(PNG + b"\x00" * MAX_BYTES)


def test_the_size_is_read():
    """Sanity of the fixtures the other tests lean on."""
    from docx.image.image import Image

    assert (Image.from_blob(PNG).px_width, Image.from_blob(PNG).px_height) == (2, 3)
    assert Image.from_blob(JPEG).px_width == 2


def test_data_uri_carries_the_bytes_and_type():
    uri = validate(JPEG).data_uri()
    prefix = "data:image/jpeg;base64,"
    assert uri.startswith(prefix)
    assert base64.b64decode(uri[len(prefix) :]) == JPEG
