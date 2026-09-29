"""Contacts out of the resume text, without the model.

A contact is copied, not interpreted: an e-mail either appears in the text or
it does not. Asking the model for it would cost a call, would miss every CV
already parsed before the field existed, and would add the one failure a
regex cannot have — an address that looks right and is invented. So this runs
over `resumes.raw_text` at render time: free, and it works for old resumes.

Only unambiguous shapes are taken. A bare "@handle" is a Telegram contact only
when the word Telegram (or tg) stands right before it; otherwise it may be a
Twitter handle, part of an e-mail or a code sample. A number is a phone only
when it starts like a Russian one (+7 / 8) and has exactly eleven digits, so
"2016 — 2020" and salary figures stay out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["email", "phone", "telegram", "github", "linkedin"]


@dataclass(frozen=True)
class Contact:
    kind: Kind
    label: str
    href: str


_EMAIL = re.compile(r"(?<![\w.+-])([\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-zA-Z]{2,})(?![\w-])")
_PHONE = re.compile(r"(?<![\d+])((?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2})(?!\d)")
_TG_LINK = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][\w]{4,31})\b", re.I)
_TG_HANDLE = re.compile(r"(?:telegram|телеграм|tg)\s*[:\-—]?\s*@([A-Za-z][\w]{4,31})\b", re.I)
_GITHUB = re.compile(r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\b", re.I)
_LINKEDIN = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/([\w-]{3,100})", re.I)


def _phone_label(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 11:
        return None
    digits = "7" + digits[1:]
    return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"


def extract_contacts(text: str | None) -> list[Contact]:
    """Contacts in the order a CV usually lists them, one of each at most.

    One per kind: a CV that mentions its author's e-mail twice, or quotes a
    former employer's address in an achievement, must not get a header full
    of addresses. The first occurrence wins, which in practice is the header.
    """
    text = text or ""
    found: dict[str, Contact] = {}

    if m := _EMAIL.search(text):
        email = m.group(1)
        found["email"] = Contact("email", email, f"mailto:{email}")

    for m in _PHONE.finditer(text):
        label = _phone_label(m.group(1))
        if label:
            found["phone"] = Contact("phone", label, "tel:" + label.replace(" ", "").replace("-", ""))
            break

    m = _TG_LINK.search(text) or _TG_HANDLE.search(text)
    if m:
        handle = m.group(1)
        found["telegram"] = Contact("telegram", f"@{handle}", f"https://t.me/{handle}")

    if m := _GITHUB.search(text):
        user = m.group(1)
        found["github"] = Contact("github", f"github.com/{user}", f"https://github.com/{user}")

    if m := _LINKEDIN.search(text):
        slug = m.group(1)
        found["linkedin"] = Contact(
            "linkedin", f"linkedin.com/in/{slug}", f"https://www.linkedin.com/in/{slug}"
        )

    order = ("email", "phone", "telegram", "github", "linkedin")
    return [found[k] for k in order if k in found]
