"""Legacy Word (.doc, Word 97-2003) -> plain text, standard library only.

A .doc is an OLE compound file (a small FAT file system inside one file) whose
"WordDocument" stream holds the text in pieces, listed by a piece table in the
"0Table"/"1Table" stream. Reading it takes two small parsers and no third-party
code: antiword or LibreOffice would mean a system package in the image and a
subprocess per upload, olefile a new dependency — for a format that only needs
its text pulled out, not its layout.

References: [MS-CFB] for the container, [MS-DOC] 2.4.1 "Retrieving Text".
Only what text extraction needs is implemented; formatting is ignored.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from .rtf import rtf_to_text

CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
RTF_MAGIC = b"{\\rtf"

# Sector ids with a special meaning; anything >= MAXREGSECT ends a chain.
MAXREGSECT = 0xFFFFFFFA


class DocError(ValueError):
    """The file cannot be read as a .doc; the message is shown to the user."""


class _Compound:
    """Just enough of [MS-CFB] to read a named stream from the root storage."""

    def __init__(self, data: bytes) -> None:
        if data[:8] != CFB_MAGIC or len(data) < 512:
            raise DocError("файл не похож на документ Word 97–2003")
        self.data = data
        self.sector_size = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_size = 1 << struct.unpack_from("<H", data, 0x20)[0]
        (n_fat,) = struct.unpack_from("<I", data, 0x2C)
        (first_dir,) = struct.unpack_from("<I", data, 0x30)
        (self.mini_cutoff,) = struct.unpack_from("<I", data, 0x38)
        (first_minifat,) = struct.unpack_from("<I", data, 0x3C)
        (first_difat,) = struct.unpack_from("<I", data, 0x44)

        # The DIFAT lists the sectors that hold the FAT: 109 slots in the
        # header, the rest in a chain whose last slot points to the next link.
        fat_sectors = list(struct.unpack_from("<109I", data, 0x4C))
        per_sector = self.sector_size // 4
        sect, seen = first_difat, set()
        while sect < MAXREGSECT and sect not in seen:
            seen.add(sect)
            ids = struct.unpack_from(f"<{per_sector}I", self._sector(sect))
            fat_sectors.extend(ids[:-1])
            sect = ids[-1]
        fat_sectors = [s for s in fat_sectors if s < MAXREGSECT][:n_fat]

        self.fat: list[int] = []
        for s in fat_sectors:
            self.fat.extend(struct.unpack_from(f"<{per_sector}I", self._sector(s)))

        entries = self._directory(self._chain(first_dir, self.fat, self.sector_size))
        if not entries or entries[0][1] != 5:
            raise DocError("повреждённый документ Word: нет корневого каталога")
        root = entries[0]
        self.entries = self._children(entries, root[4][2])
        self.mini_stream = self._chain(root[2], self.fat, self.sector_size)[: root[3]]
        minifat = self._chain(first_minifat, self.fat, self.sector_size)
        self.minifat = list(struct.unpack_from(f"<{len(minifat) // 4}I", minifat))

    def _sector(self, sect: int) -> bytes:
        start = (sect + 1) * self.sector_size
        chunk = self.data[start : start + self.sector_size]
        if len(chunk) < self.sector_size:
            raise DocError("повреждённый документ Word: файл обрезан")
        return chunk

    def _chain(self, first: int, fat: list[int], size: int, source: bytes | None = None) -> bytes:
        out, sect, seen = [], first, set()
        while sect < MAXREGSECT:
            # A corrupt FAT can loop; a cycle would otherwise never end.
            if sect in seen or sect >= len(fat):
                raise DocError("повреждённый документ Word: битая цепочка секторов")
            seen.add(sect)
            if source is None:
                out.append(self._sector(sect))
            else:
                out.append(source[sect * size : (sect + 1) * size])
            sect = fat[sect]
        return b"".join(out)

    @staticmethod
    def _directory(raw: bytes) -> list[tuple[str, int, int, int, tuple[int, int, int]]]:
        """Every entry, in order: (name, type, start, size, (left, right, child))."""
        entries = []
        for off in range(0, len(raw) - 127, 128):
            (name_len,) = struct.unpack_from("<H", raw, off + 64)
            name = raw[off : off + max(name_len - 2, 0)].decode("utf-16-le", "replace")
            links = struct.unpack_from("<III", raw, off + 68)
            # Only the low half of the 64-bit size is used: version 3 files
            # (512-byte sectors) may leave garbage in the high half.
            start, size = struct.unpack_from("<II", raw, off + 116)
            entries.append((name, raw[off + 66], start, size, links))
        return entries

    @staticmethod
    def _children(entries: list, first: int) -> dict[str, tuple[int, int]]:
        """Streams directly under the root, found by walking its sibling tree.

        Names are unique only within one storage: an embedded Excel sheet or
        another Word document carries its own "WordDocument", and a flat
        name lookup would read that one instead of the CV.
        """
        found: dict[str, tuple[int, int]] = {}
        todo, seen = [first], set()
        while todo:
            i = todo.pop()
            if i >= len(entries) or i in seen:
                continue
            seen.add(i)
            name, kind, start, size, (left, right, _child) = entries[i]
            todo += [left, right]
            if kind == 2:
                found[name.lower()] = (start, size)
        return found

    def stream(self, name: str) -> bytes:
        entry = self.entries.get(name.lower())
        if entry is None:
            raise KeyError(name)
        start, size = entry
        if size < self.mini_cutoff:
            data = self._chain(start, self.minifat, self.mini_size, self.mini_stream)
        else:
            data = self._chain(start, self.fat, self.sector_size)
        return data[:size]


# Word's in-text control characters. Paragraph, line and page breaks become
# newlines, table cell marks a tab; object anchors and optional hyphens vanish.
_CONTROL = str.maketrans(
    {
        "\r": "\n",
        "\x0b": "\n",
        "\x0c": "\n",
        "\x0e": "\n",
        "\x07": "\t",
        "\x1e": "-",
        "\xa0": " ",
        "\x01": None,
        "\x02": None,
        "\x03": None,
        "\x04": None,
        "\x05": None,
        "\x08": None,
        "\x1f": None,
    }
)


def _strip_fields(text: str) -> str:
    """Keep a field's result, drop its instruction.

    A hyperlink is stored as 0x13 'HYPERLINK "https://…"' 0x14 'shown text'
    0x15; without this a CV's contacts come out as field code. Fields nest,
    so this walks the string instead of using a regex.
    """
    out: list[str] = []
    # One flag per open field: True while still inside its instruction part.
    stack: list[bool] = []
    for ch in text:
        if ch == "\x13":
            stack.append(True)
        elif ch == "\x14":
            if stack:
                stack[-1] = False
        elif ch == "\x15":
            if stack:
                stack.pop()
        elif not any(stack):
            out.append(ch)
    return "".join(out)


def _text_from_pieces(word: bytes, table: bytes, fc_clx: int, lcb_clx: int) -> str:
    clx = table[fc_clx : fc_clx + lcb_clx]
    pos = 0
    # The Clx is a run of Prc blocks (0x01, formatting we skip) and then one
    # Pcdt (0x02) with the piece table.
    while pos < len(clx) and clx[pos] == 0x01:
        (cb,) = struct.unpack_from("<h", clx, pos + 1)
        pos += 3 + cb
    if pos >= len(clx) or clx[pos] != 0x02:
        raise DocError("повреждённый документ Word: не найдена таблица текста")
    (lcb,) = struct.unpack_from("<I", clx, pos + 1)
    plc = clx[pos + 5 : pos + 5 + lcb]
    # PlcPcd: n+1 character positions (4 bytes) then n descriptors (8 bytes).
    n = (len(plc) - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}I", plc)
    parts = []
    for i in range(n):
        (fc,) = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * i + 2)
        count = cps[i + 1] - cps[i]
        if fc & 0x40000000:
            # "Compressed" pieces are one byte per char in cp1252; Cyrillic is
            # never stored this way, it always lands in a UTF-16 piece.
            off = (fc & 0x3FFFFFFF) // 2
            parts.append(word[off : off + count].decode("cp1252", "replace"))
        else:
            parts.append(word[fc : fc + 2 * count].decode("utf-16-le", "replace"))
    return "".join(parts)


def extract_text(data: bytes) -> str:
    """Text of a Word 97-2003 document, paragraphs separated by newlines."""
    doc = _Compound(data)
    try:
        word = doc.stream("WordDocument")
    except KeyError:
        raise DocError("файл не похож на документ Word 97–2003") from None
    ident = struct.unpack_from("<H", word, 0)[0] if len(word) >= 2 else 0
    # Word 6/95 has a different header and no table stream, and its text is in
    # whatever ANSI code page the author's Windows had. Rare enough by now to
    # ask for a resave rather than guess the encoding of a Russian CV.
    if ident == 0xA5DC or (ident == 0xA5EC and struct.unpack_from("<H", word, 2)[0] < 101):
        raise DocError("документ в формате Word 95 или старше — пересохраните его как DOCX")
    if ident != 0xA5EC or len(word) < 0x1AA:
        raise DocError("файл не похож на документ Word 97–2003")
    (flags,) = struct.unpack_from("<H", word, 0x0A)
    if flags & 0x0100:
        raise DocError("документ защищён паролем — снимите защиту или сохраните как PDF")
    table_name = "1Table" if flags & 0x0200 else "0Table"
    try:
        table = doc.stream(table_name)
    except KeyError:
        raise DocError("повреждённый документ Word: нет таблицы текста") from None
    fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x01A2)
    text = _text_from_pieces(word, table, fc_clx, lcb_clx)
    text = _strip_fields(text)
    # A table row ends with a cell mark followed by a row mark, both 0x07.
    # Telling them apart properly needs the formatting tables; the pair is a
    # good enough sign, and a CV's "period | company" table stays one row per
    # line instead of merging into a single tab-separated run.
    text = text.replace("\x07\x07", "\x07\n").translate(_CONTROL)
    # Whatever control codes are left are structure we don't model, and
    # U+F000..U+F0FF are Symbol/Wingdings glyphs — list bullets, mostly.
    text = re.sub(r"[\x00-\x08\x0e-\x1f-]", "", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def read_doc(path: Path) -> str:
    """Read a .doc upload, including the two common impostors.

    Files named .doc are often something else: RTF, which 1C, HR systems and
    job sites export under a .doc name, or a .docx the user renamed. Both are
    read as what they are, since Word opens them without a word of warning
    and the user has no reason to think the file is not a .doc.
    """
    data = path.read_bytes()
    if data.startswith(ZIP_MAGIC):
        import docx

        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs).strip()
    if data.lstrip().startswith(RTF_MAGIC):
        return rtf_to_text(data)
    return extract_text(data)
