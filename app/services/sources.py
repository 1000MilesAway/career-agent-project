"""The owner's switches over sources: hidden channels and boards, shown names.

Two settings, both from the admin's "Источники" page:

  - a Telegram channel, a Telegram chat (Telethon) or an employer's board
    can be hidden. It is then not
    read, not matched to anyone, and its rows leave the search results —
    except a vacancy the user saved or bought a report for, which is theirs;
  - a company name as a source wrote it can be shown under another name,
    "ПАО Сбербанк" as "Сбербанк". Applied on the way out, never stored on
    `postings` (see `CompanyName`).

The aggregators (Habr, hh, geekjob) have no switch: they are searched by
query, so there is no channel-sized part of them to turn off.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, and_, func, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from core.sources import employers

from ..models import CompanyName, SourceSetting
from ..models import Posting as Pooled

# Sources with parts the owner can hide: channels and boards.
SWITCHABLE = ("telegram", "company", "telethon")


def board_of(external_id: str) -> str:
    """The board key (`kind/slug`) of an employer posting's id."""
    return employers.board_key(external_id)


def channel_of(external_id: str) -> str:
    """A Telegram posting's channel or chat: its id is "channel/101".

    Not `company`: that is the employer the post names, or empty.
    """
    return external_id.split("/", 1)[0]


# SQL twin of channel_of().
_CHANNEL = func.split_part(Pooled.external_id, "/", 1)


@dataclass
class Switches:
    """Hidden parts per source, and the display names. One read per request."""

    hidden: dict[str, set[str]] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)

    def channels(self) -> set[str]:
        return self.hidden.get("telegram", set())

    def boards(self) -> set[str]:
        return self.hidden.get("company", set())

    def chats(self) -> set[str]:
        """Telegram chats switched off on the Telethon tab. The provider stops
        scanning them too; this hides what the pool already holds."""
        return self.hidden.get("telethon", set())

    def is_hidden(self, source: str, external_id: str) -> bool:
        if source == "telegram":
            return channel_of(external_id) in self.channels()
        if source == "company":
            return board_of(external_id) in self.boards()
        if source == "telethon":
            return channel_of(external_id) in self.chats()
        return False

    def company(self, name: str | None) -> str | None:
        """The name users see. Unrenamed names pass through."""
        if not name:
            return name
        return self.names.get(name, name)

    def visible(self, source: str) -> ColumnElement[bool]:
        """SQL: a pooled posting of `source` is not in a hidden part."""
        if source == "telegram" and self.channels():
            return _CHANNEL.not_in(self.channels())
        if source == "telethon" and self.chats():
            return _CHANNEL.not_in(self.chats())
        if source == "company" and self.boards():
            # `kind/slug/native`: the trailing slash keeps "huntflow/lime"
            # from also hiding a board slugged "huntflow/limehr".
            return not_(
                or_(*(Pooled.external_id.startswith(f"{b}/", autoescape=True)
                      for b in self.boards()))
            )
        return true()

    def visible_any(self) -> ColumnElement[bool]:
        """SQL: a pooled posting of any source is not in a hidden part."""
        if not self.channels() and not self.boards() and not self.chats():
            return true()
        return or_(
            Pooled.source.not_in(SWITCHABLE),
            *(and_(Pooled.source == s, self.visible(s)) for s in SWITCHABLE),
        )


async def load(session: AsyncSession) -> Switches:
    hidden: dict[str, set[str]] = {}
    rows = await session.execute(
        select(SourceSetting.source, SourceSetting.key).where(SourceSetting.hidden.is_(True))
    )
    for source, key in rows:
        hidden.setdefault(source, set()).add(key)
    names = await session.execute(select(CompanyName.name, CompanyName.display_name))
    return Switches(hidden=hidden, names={name: shown for name, shown in names})
