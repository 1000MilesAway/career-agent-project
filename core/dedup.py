"""Is this posting one we already have from another place? Free, no model.

The same vacancy reaches us several ways: posted on Habr and on geekjob, and
reposted by three Telegram channels, each wrapping it in its own header. Before
this module every copy was a separate row — and a separate triage and a
separate paid evaluation, for a vacancy the user had already been shown.

Three signals, cheapest first, each one precise enough to act on alone:

  1. A link. A Telegram post that links to career.habr.com/vacancies/<id> or
     geekjob.ru/vacancy/<id> IS that vacancy; no guessing involved.
  2. Company + title, normalised. Catches the same posting on two boards.
     Only when the company is known: a Telegram post's "company" is its
     channel, and two channels with a "Python developer" each are not one job.
  3. SimHash of the text (career-ops' fingerprint-core, same parameters).
     Catches reposts whose wrapper differs — the exact-text dedup inside
     core/sources/telegram.py misses those by construction.

A duplicate is never discarded: the caller stores it screened out, with the
original named in the reason, same as any other free drop.
"""

from __future__ import annotations

import hashlib
import re

from .sources.habr_api import Posting

# Shorter text carries too little signal to tell a real match from shared
# boilerplate (career-ops' value).
FINGERPRINT_MIN_TEXT = 200
# At most 8 of 64 bits apart. career-ops uses 0.92, tuned on bare job
# descriptions; a Telegram post adds a channel's header and footer, which moves
# a verbatim repost to ~0.9. Measured 2026-09-23 on 316 live posts from 37
# channels (~50k pairs): every pair at 0.84 and above was the same vacancy
# (reposts at 1.0, a cross-channel copy at 0.953, a re-posted gig at 0.891,
# a reformatted Android role at 0.844); different vacancies peaked at 0.797 —
# two Sber roles sharing the bank's boilerplate paragraph. 0.875 keeps a
# 0.075 margin above that and misses the 0.844 copy on purpose: a missed
# duplicate costs one triage slot, a false one hides a real job.
SIMILARITY_THRESHOLD = 0.875

# Legal forms and wrappers that differ between boards for the same employer.
_COMPANY_NOISE = re.compile(
    r"\b(ооо|оао|зао|пао|ао|ип|нко|гк|группа компаний|llc|inc|ltd|gmbh|corp|co)\b\.?",
    re.IGNORECASE,
)
# Latin letters that look Cyrillic. Boards are typed by hand, and "Aгентство"
# with a Latin A is on geekjob right now — without folding, the same company
# spelt two ways is two companies.
# Lower case only: keys are casefolded before this runs.
_HOMOGLYPHS = str.maketrans("aceopxy", "асеорху")

_LINKS = (
    (re.compile(r"career\.habr\.com/vacancies/(\d{6,})"), "habr"),
    (re.compile(r"geekjob\.ru/vacancy/([a-f0-9]{12,32})"), "geekjob"),
)


def _fold(text: str) -> str:
    text = text.casefold().replace("ё", "е")
    text = re.sub(r"[^\w]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fold_mixed_script(word: str) -> str:
    """Map Latin lookalikes to Cyrillic, but only inside a word that has Cyrillic.

    "python" must stay "python"; "aгентство" must become "агентство".
    """
    if re.search(r"[а-я]", word) and re.search(r"[a-z]", word):
        return word.translate(_HOMOGLYPHS)
    return word


def company_key(company: str) -> str:
    if not company or company.startswith("@"):
        return ""
    text = _COMPANY_NOISE.sub(" ", company.casefold())
    return " ".join(_fold_mixed_script(w) for w in _fold(text).split())


def title_key(title: str) -> str:
    return " ".join(_fold_mixed_script(w) for w in _fold(title or "").split())


def posting_key(posting: Posting) -> str:
    """"company|title", or "" when either half is missing.

    The grade stays in: a Senior and a Middle opening at one company are two
    vacancies, and merging them would hide one.
    """
    company = company_key(posting.company)
    title = title_key(posting.title)
    if not company or not title:
        return ""
    return f"{company}|{title}"[:255]


def linked_vacancies(text: str) -> list[tuple[str, str]]:
    """(source, external_id) for every vacancy of ours the text links to."""
    out: list[tuple[str, str]] = []
    for pattern, source in _LINKS:
        for m in pattern.finditer(text or ""):
            if (source, m.group(1)) not in out:
                out.append((source, m.group(1)))
    return out


def _normalize(text: str) -> str:
    text = re.sub(r"https?://\S+", " ", (text or "").casefold())
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", text)).strip()


def fingerprint(text: str) -> str:
    """64-bit SimHash over 3-word shingles, 16 hex chars; "" when too short."""
    normalized = _normalize(text)
    if len(normalized) < FINGERPRINT_MIN_TEXT:
        return ""
    tokens = normalized.split(" ")
    if len(tokens) < 3:
        return ""
    weights = [0] * 64
    for i in range(len(tokens) - 2):
        digest = hashlib.sha1(" ".join(tokens[i : i + 3]).encode()).digest()
        bits = int.from_bytes(digest[:8], "big")
        for bit in range(64):
            weights[bit] += 1 if bits >> (63 - bit) & 1 else -1
    value = 0
    for bit in range(64):
        if weights[bit] > 0:
            value |= 1 << (63 - bit)
    return f"{value:016x}"


def similarity(a: str, b: str) -> float:
    """1 - hamming/64. Empty or malformed fingerprints never match."""
    if len(a) != 16 or len(b) != 16:
        return 0.0
    try:
        distance = (int(a, 16) ^ int(b, 16)).bit_count()
    except ValueError:
        return 0.0
    return 1 - distance / 64
