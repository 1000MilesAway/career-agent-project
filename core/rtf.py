"""RTF -> plain text, standard library only.

RTF matters twice here: as .rtf, and far more often under a .doc name — 1C,
HR systems and several job sites export "Word" resumes that are really RTF.
Only the text is wanted, so this is a tokenizer with a group stack, not a
document model: formatting is dropped, destinations that are not body text
(font table, styles, pictures, field instructions) are skipped.

The part that decides whether a Russian CV comes out readable is the code
page: 8-bit text arrives as \\'xx escapes, and the bytes mean different
letters depending on the font's \\fcharset or the document's \\ansicpg.
"""

from __future__ import annotations

import re

# \fcharset -> Python codec. ANSI (0) and DEFAULT (1) mean "the document's
# \ansicpg": 1C writes Cyrillic in charset-0 fonts under \ansicpg1251, and
# reading those as cp1252 would turn every letter into Latin-1 accents.
CHARSET_CODEC = {
    77: "mac_roman",
    128: "cp932",
    129: "cp949",
    134: "gbk",
    136: "big5",
    161: "cp1253",
    162: "cp1254",
    163: "cp1258",
    177: "cp1255",
    178: "cp1256",
    186: "cp1257",
    204: "cp1251",
    222: "cp874",
    238: "cp1250",
}

# Groups whose content is not body text. Most are also marked \*, but
# writers are not consistent about it.
SKIP_DESTINATIONS = {
    "colortbl",
    "datastore",
    "filetbl",
    "fldinst",
    "generator",
    "info",
    "latentstyles",
    "listoverridetable",
    "listtable",
    "nonshppict",
    "objdata",
    "pict",
    "revtbl",
    "rsidtbl",
    "shprslt",
    "sp",
    "stylesheet",
    "tc",
    "themedata",
    "xe",
    "xmlnstbl",
}

# Control words that stand for a character.
CONTROL_TEXT = {
    "par": "\n",
    "line": "\n",
    "sect": "\n",
    "page": "\n",
    "row": "\n",
    "nestrow": "\n",
    "cell": "\t",
    "nestcell": "\t",
    "tab": "\t",
    "emdash": "—",
    "endash": "–",
    "bullet": "•",
    "lquote": "‘",
    "rquote": "’",
    "ldblquote": "“",
    "rdblquote": "”",
    "emspace": " ",
    "enspace": " ",
    "qmspace": " ",
}

CONTROL_SYMBOL = {"\\": "\\", "{": "{", "}": "}", "~": " ", "_": "-", "\n": "\n", "\r": "\n"}

TOKEN = re.compile(
    r"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?"  # control word, optional parameter
    r"|\\'([0-9a-fA-F]{2})"  # 8-bit byte in the current code page
    r"|\\(.)"  # control symbol
    r"|([{}])"
    r"|[\r\n]+"  # line breaks in the source are not text
    r"|([^\\{}\r\n]+)",
    re.S,
)


class _Group:
    __slots__ = ("skip", "font", "uc", "fonttbl")

    def __init__(self, skip=False, font=None, uc=1, fonttbl=False):
        self.skip, self.font, self.uc, self.fonttbl = skip, font, uc, fonttbl

    def copy(self) -> _Group:
        return _Group(self.skip, self.font, self.uc, self.fonttbl)


def rtf_to_text(data: bytes) -> str:
    # latin-1 maps every byte to one char, so raw 8-bit text survives the
    # tokenizer and goes back to bytes unchanged before decoding.
    src = data.decode("latin-1")
    out: list[str] = []
    pending = bytearray()  # consecutive \'xx bytes, decoded together (DBCS)
    pending_codec = "cp1252"

    default_codec = "cp1252"
    default_font: int | None = None
    font_codec: dict[int, str] = {}
    fonttbl_font: int | None = None

    state = _Group()
    stack: list[_Group] = []
    skip_chars = 0  # fallback characters still to drop after \uN

    def codec() -> str:
        if state.font is not None and state.font in font_codec:
            return font_codec[state.font]
        return default_codec

    def flush() -> None:
        if pending:
            try:
                out.append(pending.decode(pending_codec, "replace"))
            except LookupError:
                # \ansicpg/\cpg can name a code page Python has no codec for.
                out.append(pending.decode("cp1252", "replace"))
            pending.clear()

    def add_bytes(raw: bytes) -> None:
        nonlocal pending_codec
        c = codec()
        if pending and c != pending_codec:
            flush()
        pending_codec = c
        pending.extend(raw)

    pos = 0
    while pos < len(src):
        m = TOKEN.match(src, pos)
        if m is None:  # a lone trailing backslash
            break
        pos = m.end()
        word, param, hexbyte, symbol, brace, text = m.groups()

        if brace == "{":
            stack.append(state.copy())
            skip_chars = 0
            continue
        if brace == "}":
            flush()
            if stack:
                state = stack.pop()
            skip_chars = 0
            continue

        if word is not None:
            n = int(param) if param is not None else None
            if word == "bin" and n:
                # Raw binary payload: its bytes are not RTF and must not be
                # tokenized, or a stray brace in it unbalances the groups.
                pos += n
                continue
            if state.fonttbl:
                if word == "f" and n is not None:
                    fonttbl_font = n
                elif word == "fcharset" and fonttbl_font is not None and n in CHARSET_CODEC:
                    font_codec[fonttbl_font] = CHARSET_CODEC[n]
                elif word == "cpg" and fonttbl_font is not None and n:
                    font_codec[fonttbl_font] = f"cp{n}"
                continue
            if word == "shpinst":
                # Marked \* but holds text boxes ({\shptxt}), where CV
                # templates put the contacts column. Its fallback copy in
                # \shprslt is skipped instead, or the text would come twice.
                state.skip = stack[-1].skip if stack else False
                continue
            if word == "fonttbl":
                state.fonttbl = state.skip = True
                continue
            if word in SKIP_DESTINATIONS:
                state.skip = True
                continue
            if word == "ansicpg" and n:
                default_codec = f"cp{n}"
                continue
            if word == "deff":
                default_font = n
                continue
            if word == "uc" and n is not None:
                state.uc = n
                continue
            if state.skip:
                continue
            if word == "u" and n is not None:
                flush()
                out.append(chr(n + 65536 if n < 0 else n))
                skip_chars = state.uc
                continue
            skip_chars = 0
            if word == "f":
                flush()
                state.font = n
            elif word == "plain":
                flush()
                state.font = default_font
            elif word in CONTROL_TEXT:
                flush()
                out.append(CONTROL_TEXT[word])
            continue

        if symbol is not None:
            if symbol == "*":
                state.skip = True
            elif not state.skip and symbol in CONTROL_SYMBOL:
                skip_chars = 0
                flush()
                out.append(CONTROL_SYMBOL[symbol])
            continue

        if state.skip:
            continue
        if hexbyte is not None:
            if skip_chars:
                skip_chars -= 1
            else:
                add_bytes(bytes([int(hexbyte, 16)]))
            continue
        if text is not None:
            if skip_chars:
                dropped = min(skip_chars, len(text))
                text, skip_chars = text[dropped:], skip_chars - dropped
            if text:
                add_bytes(text.encode("latin-1"))

    flush()
    result = "".join(out)
    try:
        # Unpaired halves of a \u surrogate pair would make the text unencodable.
        result = result.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeDecodeError:
        result = re.sub(r"[\ud800-\udfff]", "", result)
    result = re.sub(r"[\x00-\x08\x0b-\x1f\uf000-\uf0ff]", "", result)
    result = "\n".join(line.rstrip() for line in result.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", result).strip()
