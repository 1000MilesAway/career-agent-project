"""Telegram job channels via the public `t.me/s/<channel>` web preview.

Why this route and not the Bot API or a userbot:
  - Bot API cannot read a channel the bot does not administer, and has no method
    for pulling history at all.
  - A userbot (Telethon) works, but it logs in as the user's own account and puts
    it at risk of a ban — too much to pay for data that is already public.
  - `t.me/s/<channel>` serves the last ~20 posts as plain HTML to anyone, with no
    key, no login and no rate limit we could trigger. Verified 2026-09-17.

GROUPS HAVE NO PREVIEW. `t.me/s/<name>` answers 302 for a chat (as opposed to a
channel), and that is not a block but a missing feature — there is no HTML to read.
`t.me/<name>` says which it is: "N members" for a chat, "N subscribers" for a
channel. Several of the liveliest job venues are chats and simply cannot be read
this way; data/sources/telegram-channels.json lists them under chats_unreachable.

The hard part is not fetching but STRUCTURE. A Habr posting arrives with title,
divisions, qualification and salary as fields, and core.filters works on those.
A Telegram post is flat text, so this module has to reconstruct what it can:
`Post.title` is the first non-hashtag line, and `looks_like_vacancy()` throws out
what is not an employer's job ad at all. Both are guesses, and both are cheap —
no model is called here.

MANY POSTS ARE ONLY A TEASER. A growing share of channels post four lines —
role, format, company, pay — and leave the description behind a link
("Смотреть вакансию" → telegra.ph). The evaluation then judged 250 characters
of headline while the requirements sat one hop away. `fetch_externals()`
follows that link; DESCRIPTION_HOSTS says which ones and why it is a whitelist.

Note the division of labour with `apply_link`: that module picks where the user
APPLIES, and deliberately refuses telegra.ph — nobody applies through an
article. This one picks where the vacancy is DESCRIBED, which is frequently
exactly telegra.ph. Same hrefs, two different questions.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from . import apply_link, description_link, salary_text

BASE = "https://t.me"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Be a polite guest, as with Habr: one request at a time.
DELAY_SECONDS = 1.0

# A channel that has not posted in this long is treated as dead. `t.me/s/` always
# serves the LAST page, so a dead channel answers 200 with a full 20 posts and
# looks perfectly healthy — golangjob happily hands back February 2023. Without
# this check a scan quietly ships years-old vacancies.
MAX_SILENCE_DAYS = 30

# Below this a post is an aside, a poll or a greeting, never a job ad. The
# shortest real vacancy seen in the 2026-09-17 survey was ~250 characters.
MIN_VACANCY_CHARS = 200

# A teaser is short BECAUSE its description lives behind a link, so the length
# check would throw away exactly the posts we now know how to read. It still has
# to pass every other check in looks_like_vacancy().
MIN_TEASER_CHARS = 60

# The fetched page is appended to the post, never substituted for it. Capped so
# one long article cannot crowd the other vacancies out of a triage batch.
MAX_EXTERNAL_CHARS = 8000

# The post is a candidate advertising themselves, not an employer hiring. Channels
# like django_jobs_board are half these, and they must not reach the report.
SEEKER_RE = re.compile(
    r"(#cv\b|#резюме|#opentowork|ищу работу|ищу проект|ищу заказы|ищу подработ"
    r"|open to work|рассматриваю предложения"
    # Someone recommending a candidate in a job chat: a CV by proxy.
    r"|хочу порекомендовать|рекомендую (?:кандидата|специалиста|коллегу))",
    re.IGNORECASE,
)
# An employer is hiring. "Грейд:"/"Стек:" are in here because the runello_rus_*
# family uses that template and often skips the verb entirely.
HIRING_RE = re.compile(
    r"(#вакансия|#vacancy|ваканси|ищем|мы ищем|требуется|в команду|открыта позиция"
    r"|vacancy|ищет|hiring|we are looking|грейд:|стек:)",
    re.IGNORECASE,
)
# A vacancy card states its fields and nothing else: "Middle Python Developer /
# Формат: удалённо / Компания: Rocket Science / ЗП: от 140 тыс / Откликнуться:".
# The talanto channels post only these, with no hiring word anywhere; two field
# lines are the hiring sign there (docs/decisions.md, 2026-10-18).
_CARD_FIELD_RE = re.compile(
    r"^\s*(?:компания|формат|зп|з/п|зарплата|занятость|локация|город|опыт|грейд|уровень"
    r"|стек|график|company|location|salary|format)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
CARD_MIN_FIELDS = 2


def _is_card(text: str) -> bool:
    return len(_CARD_FIELD_RE.findall(text)) >= CARD_MIN_FIELDS


# Some way to reach the employer. A job ad without one is a digest entry or an
# excerpt, not something the user could act on.
CONTACT_RE = re.compile(
    r"(@\w{4,}|t\.me/|https?://|откликн|сопроводительн|hh\.ru|резюме на|пишите|apply)",
    re.IGNORECASE,
)
# Course ads, webinars and channel cross-promotion dressed up as content. This is
# the user's main complaint about job channels, so it gets its own check.
PROMO_RE = re.compile(
    r"(курс[аыу]?\b|обучени|вебинар|интенсив|запишись|регистрируйся|промокод|скидк"
    r"|\berid\b|реклама|начни карьеру|с нуля до|научим|освой"
    r"|подборка каналов|папк[уа] telegram)",
    re.IGNORECASE,
)

# Fields the runello_rus_* family states explicitly. Free structure, so it is worth
# reading: these map onto what core.filters wants and nothing else in Telegram does.
# A line that is only emoji and punctuation is decoration, not a title.
_HAS_WORD_RE = re.compile(r"[^\W_]", re.UNICODE)

GRADE_RE = re.compile(r"^\s*(?:грейд|grade|уровень)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
# The grade lands in a varchar(32) column, and a filter compares it to a level
# word. "Middle+ / Senior — нужен самостоятельный специалист…" (hr_itwork) is a
# level and a sentence: the level is kept, a line with no short level is none.
GRADE_MAX = 32
_GRADE_TAIL_RE = re.compile(r"\s+[—–-]\s+|[,;(.]")


def _grade_of(text: str) -> str:
    m = GRADE_RE.search(text)
    if not m:
        return ""
    grade = _GRADE_TAIL_RE.split(m.group(1), maxsplit=1)[0].strip()
    return grade if len(grade) <= GRADE_MAX else ""


STACK_RE = re.compile(r"^\s*(?:стек|stack|технологии)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
REMOTE_RE = re.compile(r"(удал[её]нк|удал[её]нн|remote|из любой точки|гибрид)", re.IGNORECASE)

# A salary, written the many ways a human writes one. Used as a signal, never as a
# filter: Telegram states pay far less often than Habr, and demanding it threw out
# two thirds of the real vacancies in the survey.
SALARY_RE = re.compile(
    r"(\d[\d  ]{2,}\s*(?:₽|руб|р\.|\$|€|USD|EUR)"
    r"|от\s*\d{2,}\s*(?:000|k|к|тыс)"
    r"|(?:вилка|зп|зарплат\w*|оклад|salary)\s*[:\-–—]?\s*\d)",
    re.IGNORECASE,
)


# What full_text() puts between the post and the description it read.
DESCRIPTION_MARK = "\n\n--- Полное описание со страницы "


def own_text(full_text: str) -> str:
    """The post's own text out of a stored full_text(), without the description."""
    return full_text.split(DESCRIPTION_MARK, 1)[0]


@dataclass
class Post:
    """One channel post, shaped as close to habr_api.Posting as the text allows."""

    id: str
    url: str
    channel: str
    text: str
    published: str = ""
    # Where the user actually applies, when the post names it. `url` stays the
    # permalink and remains the dedup key: an apply link is shared by every
    # repost of one vacancy AND, for a digest, by several vacancies, so keying
    # on it would merge rows that are not the same posting.
    apply_url: str = ""
    apply_kind: str = ""
    title: str = ""
    # The employer, only where the text names it ("Компания: X", "X is
    # hiring", "… at X"); empty otherwise. Never the channel or chat: that is
    # where the post was read, not who hires (it lives in `id`).
    company: str = ""
    grade: str = ""
    stack: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    remote: bool = False
    has_salary: bool = False
    # The band as parsed out of the prose, monthly, or None when the text states
    # none this parser is sure of. `has_salary` stays a broader signal: it fires
    # on "зп обсуждается" too, where there is a mention but no figure.
    salary_from: int | None = None
    salary_to: int | None = None
    salary_currency: str = ""
    # Where the vacancy is DESCRIBED, when the post only teases it. Distinct
    # from apply_url: nobody applies through a telegra.ph article, and the two
    # are frequently different links in the same post.
    description_url: str = ""
    # Filled by fetch_externals(); empty means "not fetched" or "fetch failed".
    description_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "apply_url": self.apply_url,
            "channel": self.channel,
            "title": self.title,
            "company": self.company,
            "grade": self.grade,
            "stack": self.stack,
            "tags": self.tags,
            "remote": self.remote,
            "has_salary": self.has_salary,
            "salary_from": self.salary_from,
            "salary_to": self.salary_to,
            "salary_currency": self.salary_currency,
            "published": self.published,
            "description_url": self.description_url,
        }

    def full_text(self) -> str:
        """The post, plus the linked description when one was read.

        Labelled rather than merged: the model has to be able to tell the
        channel's own four lines from the employer's page, and whoever reads the
        stored text later needs to know where the bulk of it came from.

        Separate from as_text() because this is the RAW vacancy body — what the
        product stores and evaluates — while as_text() also prepends the fields
        the pipeline reconstructed.
        """
        if not self.description_text:
            return self.text
        return (
            f"{self.text}{DESCRIPTION_MARK}{self.description_url} ---\n"
            f"{self.description_text}"
        )

    def as_text(self) -> str:
        """The plain text the LLM pipeline expects, mirroring habr.Vacancy.as_text."""
        parts = [self.title or self.channel]
        parts.append(f"Источник: Telegram-канал @{self.channel}")
        if self.grade:
            parts.append(f"Грейд: {self.grade}")
        if self.stack:
            parts.append("Стек: " + ", ".join(self.stack))
        if self.apply_url:
            parts.append(f"Отклик: {self.apply_url}")
        parts.append("")
        parts.append(self.full_text())
        return "\n".join(parts)


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA, "Accept-Language": "ru"},
        timeout=30.0,
        follow_redirects=False,  # a 302 means "this is a chat"; see fetch_channel.
    )


class NoPreview(Exception):
    """The name is a group, or does not exist: `t.me/s/` serves no HTML for it."""


class MarkupChanged(Exception):
    """The page carries posts we failed to read — t.me changed its markup.

    Separate from NoPreview because the fix is ours, not the user's: it means
    this module needs updating, and saying "канал пуст" instead would hide a
    broken collector behind a plausible-looking empty result.
    """


# Lines that are a field of the ad, not its headline: "Компания: Evenbeter"
# and a bare "Уровень" came out as titles of chat vacancies.
_FIELD_LINE_RE = re.compile(
    r"^(?:компания|company|employer|работодатель|уровень|грейд|grade|level|формат|format"
    r"|локация|location|город|city|зарплата|зп|з/п|вилка|salary|оклад|стек|stack|график"
    r"|занятость|schedule|опыт|experience|контакты?|contacts?)\b"
    # The label, maybe a word more ("Формат работы:"), then a value or nothing.
    # "Employer Brand Manager at Salmon" starts with a label word and is a title.
    r"(?:[^:\n]{0,20}:|\s*[\-–—]|\s*$)",
    re.IGNORECASE,
)
# Greetings that open a chat message: "Привет!", "Всем привет, коллеги",
# "Hi all". Stripped from the start of a line; a line that is nothing else
# is skipped.
_GREETING_RE = re.compile(
    r"^(?:(?:всем\s+)?(?:привет|приветствую|здравствуйте)(?:\s+всем)?|добрый\s+(?:день|вечер)"
    r"|доброе\s+утро|hi|hello|hey)(?:\s+(?:всем|all|everyone|коллеги|друзья|team))?"
    r"(?:\s*,\s*(?:коллеги|друзья|все|all|everyone))?\s*[!.,)]*\s*",
    re.IGNORECASE,
)
# "Вакансия: X", "Position: X" — the label adds nothing to a title.
_LABEL_RE = re.compile(
    r"^(?:вакансия|позиция|должность|роль|position|vacancy|role|job)\s*[:\-–—]\s*",
    re.IGNORECASE,
)
# The role follows these words: "Мы в поиске X", "Andersen Lab is hiring a X".
_HIRING_RE = re.compile(
    r"(?:\bмы\s+)?(?:\bв\s+поиске|\bищем|\bищу|\bищет|\bтребуется|\bтребуются|\bнужен|\bнужна"
    r"|\bis\s+hiring|\bare\s+hiring|\bwe[’']re\s+hiring|\bhiring"
    r"|\bis\s+looking\s+for|\bare\s+looking\s+for|\blooking\s+for)\s+"
    r"(?:(?:a|an|two|three|опытного|опытную)\s+)?",
    re.IGNORECASE,
)
# "Ищем"/"Ищу" opening the ad takes the role in the accusative ("Ищем
# Главного SRE-инженера"): cut, it would read as "Главного SRE-инженера", so
# the verb stays — unless the role is in English and does not decline ("Ищем
# Growth Marketing Lead"). "Требуется"/"Нужен" take the nominative and are
# always cut ("Требуется Менеджер по работе с клиентами").
_RU_OPENING_RE = re.compile(r"^(?:ищем|ищу)\b", re.IGNORECASE)
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
# A line that is the label or the verb alone; the role is on the next line.
_LABEL_ONLY_RE = re.compile(
    r"^(?:вакансия|позиция|должность|роль|position|vacancy|role|job|коротко\s+о\s+компании"
    r"|о\s+компании|about\s+(?:us|the\s+company)|(?:мы\s+)?(?:ищем|ищу|в\s+поиске)"
    r"|требуется|требуются|нужен|нужна|we[’']re\s+hiring"
    r"|(?:we\s+are\s+)?looking\s+for(?:\s+an?)?)$",
    re.IGNORECASE,
)
# "Вакансия Senior Android Developer": the label with no colon, then the role.
# "Вакансия открыта до…" goes on in lower case and is a sentence, not a label.
_LABEL_WORD_RE = re.compile(r"^(?:[Вв]акансия|[Пп]озиция|[Vv]acancy|[Pp]osition)\s+(?=[A-ZА-ЯЁ0-9])")
# "Ищем в команду Scala разработчика": the filler below would eat the role too.
_VERB_FILLER_RE = re.compile(
    r"^((?:мы\s+)?(?:ищем|ищу|в\s+поиске))\s+в\s+(?:нашу\s+)?команду\s+", re.IGNORECASE
)
_URL_RE = re.compile(r"https?://\S+")
# A link's caption on the title line: "Junior Python Developer | Смотреть вакансию".
_LINK_TEXT_RE = re.compile(
    r"\s*[|•·—–-]\s*(?:смотреть|подробнее|откликнуться|читать)\b.*$", re.IGNORECASE
)
# Trailing filler after the role: "in our company", "для участия в проекте".
_FILLER_RE = re.compile(
    r"\s+(?:(?:in|into|to)\s+our\s+(?:company|team)|to\s+join|for\s+(?:a|our)\s+project"
    r"|в\s+(?:нашу\s+|нашей\s+|нашу\s+дружную\s+)?(?:компанию|команду|компании|команде)"
    r"|для\s+участия|на\s+(?:долгосрочный\s+)?проект)\b.*$",
    re.IGNORECASE,
)
# A line that is only a grade ("Middle", "Senior+") belongs to a field above.
_GRADE_ONLY_RE = re.compile(
    r"^(?:intern|junior|middle|senior|lead|стажер|стажёр|джун|мидл|сеньор)\+?$", re.IGNORECASE
)
# A summary label before the headline: "tl;dr: founding engineer, $100k…".
# "tl;dr computer vision engineer" goes without a colon too; "Кратко о компании" would not.
_SUMMARY_RE = re.compile(
    r"^(?:tl\s*;?\s*dr(?:\s*[:\-–—]\s*|\s+)|кратко\s*[:\-–—]\s*)", re.IGNORECASE
)
# The offer after the role on one line: ", $100k–130k + equity, remote". A
# comma then money or a work format; "Торговый представитель, г. Лесозаводск"
# keeps its town.
_OFFER_TAIL_RE = re.compile(
    r",\s*(?:[$€£₽]\s*\d|\d[\d\s.,]*\s*(?:k|к|тыс|[$€£₽])"
    r"|(?:remote|удал[её]нн?о|hybrid|гибрид|full[\s-]time|part[\s-]time|on[\s-]?site)\b).*$",
    re.IGNORECASE,
)
TITLE_MAX = 90


def _clean_line(raw: str) -> str:
    """One line without tags, emoji, links and decoration, in plain letters.

    NFKC turns the "bold" mathematical letters some posters use (𝐒𝐞𝐧𝐢𝐨𝐫)
    into plain ones; symbols (emoji, ✦, ║) and joiners are dropped, so the
    same vacancy titled with and without 🚀 compares equal in dedup. Markdown
    bold ("**NAS Technologies**") survives into some posts as literal stars.
    """
    line = unicodedata.normalize("NFKC", raw)
    line = _URL_RE.sub("", line)
    line = re.sub(r"\*{2,}|_{2,}", "", line)
    line = re.sub(r"&?amp;", "&", line)
    line = re.sub(r"#[\wЀ-ӿ.+]+", "", line)
    line = "".join(
        ch for ch in line
        if unicodedata.category(ch) not in ("So", "Sk", "Cf", "Mn", "Co")
    )
    return re.sub(r"\s+", " ", line).strip(" -–—:|,·•*\t")


def _shorten(title: str) -> str:
    """Cut at a natural break before TITLE_MAX, else at a word."""
    title = title.rstrip(" !.?;")
    if len(title) <= TITLE_MAX:
        return title
    for sep in (" — ", " – ", " | ", ". ", ", ", " ("):
        cut = title.find(sep, 20, TITLE_MAX)
        if cut != -1:
            return title[:cut].rstrip(" !.?;,")
    return title[:TITLE_MAX].rsplit(" ", 1)[0] + "…"


def _declines(role: str) -> bool:
    """Would the role read wrong without "Ищем"? Its first words are Russian."""
    return any(_CYRILLIC_RE.search(word) for word in role.split()[:2])


def _title_from_line(line: str) -> str:
    line = _GREETING_RE.sub("", line, count=1)
    summarized = bool(_SUMMARY_RE.match(line))
    line = _SUMMARY_RE.sub("", line, count=1)
    line = _LABEL_RE.sub("", line, count=1)
    line = _LABEL_WORD_RE.sub("", line, count=1)
    line = _VERB_FILLER_RE.sub(r"\1 ", line, count=1)
    m = _HIRING_RE.search(line)
    cut = bool(m) and not (_RU_OPENING_RE.match(line) and _declines(line[m.end():]))
    if cut:
        line = line[m.end():]
    # The first sentence: "Senior Python Developer для CVAT.ai! Удалённо…".
    # "г. Лесозаводск" is an abbreviation, not a sentence end.
    line = re.split(r"[!?]|(?<!\bг)(?<!\bул)\.\s", line, maxsplit=1)[0]
    line = _OFFER_TAIL_RE.sub("", line)
    line = _FILLER_RE.sub("", line)
    line = _LINK_TEXT_RE.sub("", line)
    line = line.strip(" -–—:|,·•*")
    # "ищет Инженера данных)❕" leaves the closing half of a smiley.
    if line.endswith(")") and line.count(")") > line.count("("):
        line = line[:-1].rstrip()
    # "ищу специалиста…" cut to "специалиста…" starts lower-case; a title as
    # written ("iOS Developer") keeps its own case.
    if (cut or summarized) and line[:1].islower():
        line = line[0].upper() + line[1:]
    return _shorten(line)


def _first_title_line(text: str) -> str:
    """The headline of a post: the role, as far as the text makes it out.

    Channels put the role on line one and tags above or below it, so the
    first line with words is the start. Chat messages are prose ("Привет! Мы
    в поиске Senior Python Developer для CVAT.ai!"), so greetings, field
    lines ("Компания: …"), labels ("Вакансия:") and the hiring phrase around
    the role are taken off. A line must contain a letter or a digit to count:
    godevjob opens with a lone "🔥".
    """
    for raw in text.split("\n"):
        line = _clean_line(raw)
        if (
            not _HAS_WORD_RE.search(line)
            or _FIELD_LINE_RE.match(line)
            or _GRADE_ONLY_RE.match(line)
            or _LABEL_ONLY_RE.match(_GREETING_RE.sub("", line, count=1))
        ):
            continue
        title = _title_from_line(line)
        if _HAS_WORD_RE.search(title) and not _LABEL_ONLY_RE.match(title):
            return title
    return ""


# "Компания: Evenbeter", "Работодатель — Combo Tech".
_COMPANY_FIELD_RE = re.compile(
    r"^(?:компания|company|employer|работодатель|наниматель)\s*[:\-–—]\s*(.+)$", re.IGNORECASE
)
# The label alone: "<b>Компания:</b><br>SL Soft" puts the name on the next line.
_COMPANY_LABEL_RE = re.compile(
    r"^(?:компания|company|employer|работодатель|наниматель)$", re.IGNORECASE
)
_ANY_FIELD_RE = re.compile(r"^[^\W\d][\w/ ]{0,20}:")
# "Andersen Lab is hiring", "Traffic Jam ищет", "Мы в 2ГИС ищем": the name is
# what precedes the verb. "Ищем" counts only after "Мы в": "Сейчас ищем",
# "Срочно ищем" open with an adverb, not a name.
_COMPANY_HIRING_RE = re.compile(
    r"^(?:мы\s+в\s+(.{2,40}?)\s+(?:ищем|в\s+поиске)"
    r"|(.{2,60}?)\s+(?:is\s+hiring|are\s+hiring|is\s+looking\s+for"
    r"|are\s+looking\s+for|ищет|ищут|в\s+поиске|в\s+поисках|расширяет\s+команду"
    r"|открывает\s+вакансию|развивает|строит))\b",
    re.IGNORECASE,
)
# A name the title closes with: "CPO at AskCraft", "Senior Python Developer
# для CVAT.ai", "Golang-разработчик в SimbirSoft". After "в" only a Latin name
# counts: "в Маркетплейс", "в LLM-проекты Яндекса" are a team, not a company.
_NAME = r"[A-ZА-ЯЁ0-9][\w.&+'’-]*(?:\s+[A-ZА-ЯЁ0-9][\w.&+'’-]*){0,3}"
_LATIN_NAME = r"[A-Z0-9][A-Za-z0-9.&+'’-]*(?:\s+[A-Z0-9][A-Za-z0-9.&+'’-]*){0,3}"
_COMPANY_TAIL_RE = re.compile(
    rf"\s(?:(?:at|@|для|в\s+компанию|в\s+компании)\s+({_NAME})"
    rf"|(?:в|компании)\s+({_LATIN_NAME}))(?:\s[—–|]\s.*)?$"
)
# "Backend Engineer на Go — Listee", "HR Business Partner / Naran". The same
# shape closes "Technical Lead — Legacy Financial System" and "Android / Kotlin
# Multiplatform", so the name must also open a line of the body ("Listee
# строит…", "Naran — международная компания").
_COMPANY_DASH_TAIL_RE = re.compile(rf"\s[—–/|]\s+({_NAME})$")
# "Аккредитованная ИТ-компания «Системы и Алгоритмы»", "ООО «7 Красных Линий»".
_COMPANY_QUOTED_RE = re.compile(
    r"(?:компани[яиюей]|банк|ООО|АО|ПАО|ЗАО|ГК|группа\s+компаний)\s+[«\"“]([^»\"”]{2,40})[»\"”]",
    re.IGNORECASE,
)
# A line introducing the employer: "Сбер Банк — крупнейший российский
# финансовый конгломерат", "Х5 — ведущая розничная компания", "Naran is an
# international company". The description must say it is an organisation:
# "Требования — опыт от 3 лет" has the shape too.
_COMPANY_INTRO_RE = re.compile(
    r"^(?:в\s+)?(.{2,50}?)\s+(?:[—–-]\s+(?:это\s+)?|is\s+(?:a|an|the)\s+)(.+)$", re.IGNORECASE
)
_ORG_WORD_RE = re.compile(
    r"компани|банк|конгломерат|оператор|агентств|платформ|сервис|производител|интегратор"
    r"|холдинг|групп|стартап|маркетплейс|ритейл|\bсеть\b|организаци|вендор|экосистем"
    r"|студи|лидер|провайдер|команда\s+из|сайт|импортер|дистрибьютор"
    r"|разработчик\s+(?:по|програм)"
    r"|company|bank|platform|startup|provider|agency|vendor|marketplace|studio|leader"
    r"|team\s+of|firm|fintech|operator|developer\s+of",
    re.IGNORECASE,
)
# "Мы — RecFaces: международная IT-компания".
_COMPANY_WE_ARE_RE = re.compile(r"^мы\s*[—–-]\s*(.+)$", re.IGNORECASE)
# A recruiter is not the employer: "Selecty (КА)", "кадровое агентство X".
# Their vacancy has no company rather than the agency's name, which would
# also merge different clients' openings with one title in dedup.
_AGENCY_RE = re.compile(
    r"\(\s*ка\s*\)|\bкадров\w*\s+агентств|\bрекрутингов\w*\s+агентств"
    r"|\brecruit(?:ing|ment)\s+agency\b|\bhr[- ]агентств|\bhr\s+agency\b",
    re.IGNORECASE,
)
# Words that stand where a name should but describe instead: "Мы ищем",
# "Международная IT-команда в поиске", "The largest online marketplace…",
# and section heads that share the "X — …" shape ("Требования — …").
_NOT_A_NAME_RE = re.compile(
    r"^(?:мы|я|we|i|our|наш\w*|команда|компания|стартап|проект|team|company|startup|the|a|an"
    r"|международн\w*|крупн\w*|российск\w*|продуктов\w*|молод\w*|быстрорастущ\w*"
    r"|hr|рекрутер|recruiter|коллеги|друзья|всем|о|об|about|это|требовани\w*|задачи"
    r"|условия|обязанности|опыт|зарплата|формат|сервис|клиент\w*|сейчас|сегодня|срочно"
    r"|также|в|во|на|для|по|из|с|к|у|кого|кто|что|ребят\w*|внутри|среди|будет|основн\w*"
    r"|сегодняшн\w*|небольш\w*|прекрасн\w*|продукт|портал|контакт"
    # Said where the name would be, to say there is none.
    r"|не\s+указан\w*|конфиденциальн\w*|скрыт\w*)\b",
    re.IGNORECASE,
)
# A role where the name should be: a digest lists "Retention Product Manager —
# <компания>, …" line after line.
_ROLE_WORD_RE = re.compile(
    r"\b(?:manager|engineer|developer|designer|analyst|specialist|head|director"
    r"|разработчик\w*|аналитик\w*"
    r"|менеджер\w*|инженер\w*|дизайнер\w*|специалист\w*)\b",
    re.IGNORECASE,
)
# A whole "name" that is a field or a placeholder: "Вакансии в IT", "Company: NDA".
_NOT_A_NAME_WORDS = {
    "it", "ит", "ai", "ml", "nda", "fintech", "edtech", "igaming", "web3", "crypto", "saas",
    "b2b", "b2c", "gamedev",
}
# Inside a name guessed from prose, a lower-case Russian word is prose: "Ребята
# активно ищут", "Прекрасный Recraft.ai снова ищет", "Основной фокус — …".
_LOWER_RU_WORD_RE = re.compile(r"(?:^|\s)[а-яё]")
# A hyphenated Russian word is a description, not a name: "для
# VPN-приложения", "для CEX-арбитража".
_DESCRIPTIVE_RE = re.compile(r"-[а-яё]")
COMPANY_MAX_WORDS = 4
# How deep into the post an opening ("X ищет", "X — компания") is looked for.
COMPANY_HEAD_LINES = 8


def _as_company(raw: str, *, guessed: bool = True) -> str:
    """A name as written, or "" when the words are a description or an agency.

    `guessed` is every shape but the "Компания:" field: there the poster said
    it is a name ("Метр квадратный"), elsewhere it is read out of prose.
    """
    if _AGENCY_RE.search(raw):
        return ""
    if m := _COMPANY_QUOTED_RE.search(raw):
        return m.group(1).strip()
    name = re.split(r"\s[—–|-]\s|[.!?;,(:]\s|[!?;(]", raw, maxsplit=1)[0]
    name = name.strip(" -–—:|,·•*\"«»'>")
    if name.count("«") != name.count("»"):
        name = name.replace("«", "").replace("»", "")
    if (
        not name
        or not name[:1].isupper() and not name[:1].isdigit()
        or len(name.split()) > COMPANY_MAX_WORDS
        or _NOT_A_NAME_RE.match(name)
        or name.casefold() in _NOT_A_NAME_WORDS
        or _ROLE_WORD_RE.search(name)
        or _DESCRIPTIVE_RE.search(name)
        or name.isdigit()
        or guessed and _LOWER_RU_WORD_RE.search(name)
    ):
        return ""
    return name


def _company_of(text: str, title: str) -> str:
    """The employer, where the post names it; "" otherwise.

    Only shapes that name a company outright (docs/decisions.md, 2026-10-16
    and 2026-10-19): a field line, a "X is hiring" opening, a name closing the
    title, a quoted legal name, a line introducing the company ("X — ведущая
    розничная компания"). Anything looser guessed descriptions ("Международная
    IT-команда") as names; an empty company is shown as "компания не
    указана", a wrong one misleads.
    """
    lines = [_GREETING_RE.sub("", _clean_line(raw), count=1) for raw in text.split("\n")]
    lines = [line for line in lines if _HAS_WORD_RE.search(line)]
    for i, line in enumerate(lines):
        if m := _COMPANY_FIELD_RE.match(line):
            return _as_company(m.group(1), guessed=False)
        if _COMPANY_LABEL_RE.match(line) and i + 1 < len(lines):
            # "Company: <link>" leaves the label alone too; the next line is
            # then the next field ("Workplace: Remote"), not the name.
            nxt = lines[i + 1]
            return "" if _ANY_FIELD_RE.match(nxt) else _as_company(nxt, guessed=False)
    head = lines[:COMPANY_HEAD_LINES]
    for line in head:
        if m := _COMPANY_HIRING_RE.match(line):
            if name := _as_company(m.group(1) or m.group(2)):
                return name
    if name := _dash_tail_company(lines, title):
        return name
    if m := _COMPANY_TAIL_RE.search(title):
        if name := _as_company(m.group(1) or m.group(2)):
            return name
    for line in head:
        if m := _COMPANY_QUOTED_RE.search(line):
            return m.group(1).strip()
    for line in head:
        if title and title in line:
            continue
        if m := _COMPANY_WE_ARE_RE.match(line):
            if name := _as_company(m.group(1)):
                return name
        m = _COMPANY_INTRO_RE.match(line)
        # The organisation word defines the name ("— ведущая розничная
        # компания"), not some later clause ("Будет плюсом — опыт с платформой").
        if m and _ORG_WORD_RE.search(" ".join(m.group(2).split()[:4])):
            # An agency introducing itself means the employer is not named.
            if _AGENCY_RE.search(line):
                return ""
            if name := _as_company(m.group(1)):
                return name
    return ""


def _dash_tail_company(lines: list[str], title: str) -> str:
    """"Go-разработчик в TATLIN.UNIFIED — YADRO": the name after the dash, when
    a line of the body starts with it."""
    if m := _COMPANY_DASH_TAIL_RE.search(title):
        name = _as_company(m.group(1))
        if name and any(line.startswith(name) and title not in line for line in lines):
            return name
    return ""


# The words that attach a company to the end of a title: "в SimbirSoft",
# "at AskCraft", "для международной IT-компании RecFaces", "— YADRO".
_TAIL_LEAD_RE = r"\s+(?:(?:at|@|для|в)\s+(?:\S+\s+){0,3}?|[—–/|]\s+)"


def _without_company(title: str, company: str) -> str:
    """The title with the company it ends with moved out to the company field.

    "Golang-разработчик в SimbirSoft" is the role plus the employer, and the
    list already shows the employer next to the title.
    """
    if not company:
        return title
    m = re.search(rf"{_TAIL_LEAD_RE}{re.escape(company)}$", title)
    if not m or m.start() == 0:
        return title
    return title[: m.start()].rstrip(" -–—:|,")


# Where the post itself breaks a line. Everything else in the markup is inline.
_BREAK_RE = re.compile(r"(?i)<br\s*/?>|</(?:p|div|blockquote|pre|li)>")


def _message_text(body) -> str:
    """The post's text with the poster's own line breaks and no others.

    Only <br> (and the rare block) ends a line. `.text(separator="\n")` put a
    break between EVERY pair of nodes, so "<b>Компания:</b> YADRO", "в <a>Cyprus
    Constructions</a>" and an emoji's <i> became separate lines: titles came
    out as "Ищем", "Позиция", "Middle PHP Developer в" and the company field
    was never matched (docs/decisions.md, 2026-10-19).
    """
    html = re.sub(r"\s+", " ", body.html or "")
    text = HTMLParser(f"<div>{_BREAK_RE.sub(chr(10), html)}</div>").text(separator="")
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _parse_post(node, channel: str) -> Post | None:
    """Build a Post from one `.tgme_widget_message_wrap` node."""
    body = node.css_first(".tgme_widget_message_text")
    if body is None:
        return None
    text = _message_text(body)
    if not text:
        return None

    holder = node.css_first(".tgme_widget_message")
    post_path = (holder.attributes.get("data-post") if holder else "") or ""
    # "channel/101", not "101". Telegram numbers posts within a channel, so
    # the bare number collides across channels — and the product stores it as
    # `external_id`, unique per (user, source, external_id). Two channels with
    # a post 101 collapsed into one row that kept the first post's text under
    # the second one's title and link, so an evaluation judged the wrong ad.

    # The preview embeds forwarded posts and pinned neighbours from OTHER
    # channels. They are someone else's publication: counting them makes a
    # channel look productive when it only reposts, and the same vacancy then
    # arrives once per channel that forwarded it.
    if post_path and post_path.split("/")[0] != channel:
        return None

    when = ""
    stamp = node.css_first("time[datetime]")
    if stamp is not None:
        when = stamp.attributes.get("datetime") or ""

    # Read the links from the MARKUP, before `.text()` flattens it: the anchors
    # are gone by then, and the apply path with them.
    hrefs = [
        a.attributes.get("href") or "" for a in body.css("a[href]")
    ]
    return build_post(channel, post_path, text, when, [h for h in hrefs if h])


def build_post(channel: str, post_path: str, text: str, when: str, hrefs: list[str]) -> Post:
    """A Post from a message's text and links, however the message was read.

    Shared by the `t.me/s/` preview and the tg-provider chats
    (core/sources/tg_chats.py), so a vacancy is parsed the same way whichever
    door it came through.
    """
    link = apply_link.pick(hrefs)
    # The same anchors answer a second question: where the vacancy is described.
    # A teaser's "Смотреть вакансию" is usually NOT the apply link — apply_link
    # refuses telegra.ph on purpose — so the two are picked independently.
    described_at = description_link.pick(hrefs)

    stack = STACK_RE.search(text)
    band = salary_text.parse_salary(text)
    title = _first_title_line(text)
    company = _company_of(text, title)
    title = _without_company(title, company)
    return Post(
        id=post_path,
        url=f"{BASE}/{post_path}" if post_path else f"{BASE}/{channel}",
        apply_url=link.url if link else "",
        apply_kind=link.kind if link else "",
        channel=channel,
        text=text,
        published=when,
        title=title,
        company=company,
        grade=_grade_of(text),
        stack=[s.strip() for s in stack.group(1).split(",") if s.strip()] if stack else [],
        tags=re.findall(r"#([\wЀ-ӿ]+)", text),
        remote=bool(REMOTE_RE.search(text)),
        has_salary=bool(SALARY_RE.search(text)),
        salary_from=band.min if band else None,
        salary_to=band.max if band else None,
        salary_currency=band.currency if band else "",
        description_url=described_at,
    )


def looks_like_vacancy(post: Post) -> tuple[bool, str]:
    """Is this an employer's job ad? Free check, runs before any filter or model.

    Habr needs nothing like this — everything in its listing is a vacancy. A
    Telegram channel also carries memes, greetings, course ads, weekly stats and
    candidates' own CVs, and every one of those would otherwise be scored as a job.

    Salary is deliberately NOT required. Telegram states pay far less often than
    Habr, and making it mandatory dropped 6 of 9 genuine vacancies in the survey.
    """
    # A teaser is short because its description is one link away, so the length
    # floor drops for it. Every other check below still applies unchanged.
    floor = MIN_TEASER_CHARS if post.description_url else MIN_VACANCY_CHARS
    if len(post.text) < floor:
        return False, "слишком короткий пост"
    # Checked before hiring words: "#резюме ... ищу работу" contains both, and the
    # seeker's own wording must win.
    if SEEKER_RE.search(post.text):
        return False, "резюме соискателя, не вакансия"
    # Promo is judged before the hiring check so the reason stays truthful: a course
    # ad usually has no hiring words either, and "нет признаков найма" would hide
    # what the post actually is. Promo only loses when the OPENING looks like
    # hiring — a real vacancy may well offer "обучение за счёт компании" further down.
    if PROMO_RE.search(post.text) and not HIRING_RE.search(post.text[:200]):
        return False, "реклама курсов или каналов"
    if not HIRING_RE.search(post.text) and not _is_card(post.text):
        return False, "нет признаков найма"
    if not CONTACT_RE.search(post.text):
        return False, "нет контакта для отклика"
    return True, ""


def _readable_text(html: str) -> str:
    """The body text of a description page, chrome stripped.

    Deliberately generic. The whitelist spans telegra.ph, Teletype, Notion and
    two job boards, and a per-host extractor for each would rot the moment any
    of them redesigns. <article> when it exists, <main> next, <body> otherwise:
    that finds the description everywhere, at the cost of some navigation text.
    """
    tree = HTMLParser(html)
    for bad in tree.css("script, style, nav, header, footer, noscript, aside"):
        bad.decompose()
    node = tree.css_first("article") or tree.css_first("main") or tree.css_first("body")
    if node is None:
        return ""
    return re.sub(r"\n{3,}", "\n\n", node.text(separator="\n", strip=True)).strip()


def _description_client() -> httpx.Client:
    # Redirects followed, unlike _client(): telegra.ph and the boards redirect
    # legitimately, and here a 302 carries none of the "this is a chat" meaning
    # it has on t.me.
    return httpx.Client(
        headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.9"},
        timeout=20.0,
        follow_redirects=True,
    )


def fetch_post(url: str, client: httpx.Client | None = None) -> tuple[str, list[str]] | None:
    """One post's text and links from its widget page; None when unreadable.

    For a post already pooled (app/services/tg_renormalize.py): the preview
    shows only the latest ~20 posts of a channel. The widget is the page
    core/liveness.py reads, and it carries the same `.tgme_widget_message_text`.
    """
    own = client is None
    client = client or _client()
    try:
        r = client.get(url.split("?", 1)[0].split("#", 1)[0] + "?embed=1&mode=tme")
        if r.status_code != 200:
            return None
        body = HTMLParser(r.text).css_first(".tgme_widget_message_text")
        if body is None:
            return None
        hrefs = [a.attributes.get("href") or "" for a in body.css("a[href]")]
        return _message_text(body), [h for h in hrefs if h]
    except httpx.HTTPError:
        return None
    finally:
        if own:
            client.close()


def fetch_description(url: str, client: httpx.Client | None = None) -> str:
    """Read one linked description page. Free in money, ~1 s in time.

    Raises nothing. A page we could not read must not sink a scan, so every
    failure comes back as "" and the post keeps its own text — which is exactly
    the behaviour before this stage existed.
    """
    if not description_link.is_description_link(url):
        return ""

    own = client is None
    client = client or _description_client()
    try:
        r = client.get(url)
        if r.status_code != 200:
            return ""
        # A PDF would arrive as mojibake and be scored as though it were text.
        if "html" not in r.headers.get("content-type", "").lower():
            return ""
        text = _readable_text(r.text)
    except httpx.HTTPError:
        return ""
    except Exception:
        # A malformed page tripping the parser is one lost description, not a
        # lost scan.
        return ""
    finally:
        if own:
            client.close()

    return text[:MAX_EXTERNAL_CHARS]


def fetch_externals(
    posts: list[Post],
    limit: int | None = None,
    client: httpx.Client | None = None,
) -> int:
    """Fill in `description_text` for posts that link to a description host.

    Costs no model money, but it is one HTTP request per post to someone else's
    host — so callers run it AFTER the free filters, over the survivors, never
    over everything collected. Returns how many posts gained a description.

    `limit` caps the requests per scan: without it a wide channel list would
    fire a hundred requests at telegra.ph in a row and earn the block it got.
    """
    targets = [p for p in posts if p.description_url and not p.description_text]
    if limit is not None:
        targets = targets[:limit]
    if not targets:
        return 0

    own = client is None
    client = client or _description_client()
    filled = 0
    try:
        for i, post in enumerate(targets):
            text = fetch_description(post.description_url, client=client)
            if text:
                post.description_text = text
                filled += 1
            if i < len(targets) - 1:
                time.sleep(DELAY_SECONDS)
    finally:
        if own:
            client.close()
    return filled


def _channel_is_silent(posts: list[Post], max_days: int) -> bool:
    stamps = [p.published for p in posts if p.published]
    if not stamps:
        return False
    try:
        newest = max(datetime.fromisoformat(s) for s in stamps)
    except ValueError:
        return False
    return datetime.now(UTC) - newest > timedelta(days=max_days)


def _get_page(channel: str, before: str | int | None, client: httpx.Client | None) -> str:
    url = f"{BASE}/s/{channel}"
    if before:
        url = f"{url}?before={before}"

    own = client is None
    client = client or _client()
    try:
        r = client.get(url)
        # 302 -> t.me/<channel>: a group (or a missing name). No preview exists.
        if r.status_code in (301, 302, 303, 307, 308):
            raise NoPreview(f"@{channel}: нет веб-превью (группа или канала не существует)")
        r.raise_for_status()
        return r.text
    finally:
        if own:
            client.close()


def _parse_page(tree: HTMLParser, channel: str) -> list[Post]:
    wraps = tree.css(".tgme_widget_message_wrap")
    posts = []
    for node in wraps:
        post = _parse_post(node, channel)
        if post is not None:
            posts.append(post)

    # A page that clearly HAS posts but yielded none means our selectors stopped
    # matching, not that the channel is quiet. Those two are indistinguishable
    # downstream — both look like an empty channel — so a markup change would
    # silently zero out every Telegram channel at once and the scan would report
    # "ничего не найдено" as if that were the truth. Media-only and forwarded
    # posts are legitimately dropped, so only an ALL-or-nothing miss counts.
    if wraps and not posts and not _all_dropped_legitimately(wraps, channel):
        raise MarkupChanged(
            f"@{channel}: на странице {len(wraps)} постов, но ни один не разобран — "
            "изменилась вёрстка t.me"
        )
    return posts


def fetch_channel(
    channel: str,
    before: str | int | None = None,
    client: httpx.Client | None = None,
) -> list[Post]:
    """One page (~20 posts) of a public channel, newest last.

    Raises NoPreview when the name is a group or does not exist.
    """
    return _parse_page(HTMLParser(_get_page(channel, before, client)), channel)


@dataclass
class Preview:
    """A channel's newest page with its header: what it is and who reads it."""

    posts: list[Post]
    title: str = ""
    subscribers: int | None = None


def _count(text: str) -> int | None:
    """"9.24K" -> 9240, "1.2M" -> 1200000, "852" -> 852."""
    m = re.fullmatch(r"([\d.,\s]+)\s*([KM]?)", text.strip(), re.IGNORECASE)
    if not m:
        return None
    number = float(m.group(1).replace(" ", "").replace(",", "."))
    return int(number * {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2).lower()])


def parse_preview(html: str, channel: str) -> Preview:
    tree = HTMLParser(html)
    title = tree.css_first(".tgme_channel_info_header_title")
    subscribers = None
    for counter in tree.css(".tgme_channel_info_counter"):
        kind = counter.css_first(".counter_type")
        value = counter.css_first(".counter_value")
        if kind and value and kind.text(strip=True) in ("subscribers", "подписчиков"):
            subscribers = _count(value.text(strip=True))
    return Preview(
        posts=_parse_page(tree, channel),
        title=title.text(strip=True) if title else "",
        subscribers=subscribers,
    )


def fetch_preview(channel: str, client: httpx.Client | None = None) -> Preview:
    """The newest page of a channel with its title and subscriber count.

    Used to judge a channel nobody has vetted yet (core/sources/tg_discovery.py).
    Raises NoPreview when the name is a group, a person or does not exist.
    """
    return parse_preview(_get_page(channel, None, client), channel)


def _all_dropped_legitimately(wraps, channel: str) -> bool:
    """Were the skipped posts skipped for reasons we understand?

    Media-only posts and forwards from other channels are expected drops. A page
    made entirely of those is not a parser failure.

    A missing `.tgme_widget_message_text` is NOT enough to call a post
    media-only: that is exactly what a renamed class looks like. The tell is the
    block's own text — a photo post carries none, a post whose text node we
    stopped recognizing is full of words.
    """
    for node in wraps:
        holder = node.css_first(".tgme_widget_message")
        path = (holder.attributes.get("data-post") if holder else "") or ""
        if path and path.split("/")[0] != channel:
            continue  # someone else's post, correctly skipped
        if node.css_first(".tgme_widget_message_text") is not None:
            return False  # we could see the text node and still produced nothing
        # No text node. Genuine media post, or a renamed class? A photo post's
        # block holds only the view count and the timestamp; prose in it means
        # there was a post here we failed to read. Two words of three-plus
        # letters is the line — "1.2K 16 Sep" does not reach it.
        body = node.text(separator=" ", strip=True) or ""
        if len(re.findall(r"[^\W\d_]{3,}", body)) >= 2:
            return False
    return True


def search_many(
    channels: list[str],
    pages: int = 1,
    max_silence_days: int = MAX_SILENCE_DAYS,
    skip_non_vacancies: bool = True,
) -> tuple[list[Post], list[tuple[str, str]]]:
    """Collect from several channels. Free — no model is called.

    Returns (posts, problems), where problems pairs a channel with why it was
    skipped. Nothing is silently dropped: a dead or unreachable channel is a fact
    the user needs, not noise to swallow.

    Deduplication is BY TEXT, not by post id. Channels repost the same vacancy
    under new ids — getitrussia served 20 posts holding 9 distinct texts — and
    id-based dedup would let every copy through to be scored separately.
    """
    seen: set[str] = set()
    merged: list[Post] = []
    problems: list[tuple[str, str]] = []

    with _client() as client:
        for i, channel in enumerate(channels):
            try:
                collected: list[Post] = []
                cursor: str | int | None = None
                for page in range(pages):
                    batch = fetch_channel(channel, before=cursor, client=client)
                    if not batch:
                        break
                    collected.extend(batch)
                    # `before` pages backwards from the oldest id on this page.
                    cursor = min((p.id for p in batch if p.id.isdigit()), key=int, default=None)
                    if cursor is None:
                        break
                    if page < pages - 1:
                        time.sleep(DELAY_SECONDS)
            except NoPreview as e:
                problems.append((channel, str(e)))
                continue
            except MarkupChanged as e:
                # Reported, not raised: one channel serving unexpected markup
                # must not abort the other channels' collection. It lands in
                # `problems`, where the user sees it, same as a dead channel.
                problems.append((channel, str(e)))
                continue
            except httpx.HTTPError as e:
                problems.append((channel, f"@{channel}: {type(e).__name__}"))
                continue

            if not collected:
                problems.append((channel, f"@{channel}: постов не найдено"))
                continue
            if _channel_is_silent(collected, max_silence_days):
                newest = max(p.published for p in collected if p.published)[:10]
                problems.append((channel, f"@{channel}: молчит с {newest} — пропускаю"))
                continue

            for post in collected:
                if skip_non_vacancies and not looks_like_vacancy(post)[0]:
                    continue
                key = " ".join(post.text.split())
                if key in seen:
                    continue
                seen.add(key)
                merged.append(post)

            if i < len(channels) - 1:
                time.sleep(DELAY_SECONDS)

    return merged, problems
