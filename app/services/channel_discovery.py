"""Telegram channel discovery: leads in, vetted channels out, hidden.

The judging is in core (core/sources/tg_discovery.py); this module gathers
the leads (the provider's weekly discovery, links in pooled posts), skips the
channels already known, and stores the verdicts. An accepted channel joins
its group hidden — a `source_settings` row — so no user sees it until the
owner turns it on (docs/decisions.md, 2026-10-18).

Runs in the worker once a day (`worker.discovery_loop`) and by hand with
`python -m app.cli discover-channels`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from core.sources import tg_channels, tg_chats, tg_discovery

from ..models import FoundChannel, SourceSetting
from ..models import Posting as Pooled

log = logging.getLogger(__name__)

# The channel list's groups a scan can read; `rejected` and
# `chats_unreachable` are names already looked at, not feeds.
FILE_GROUPS = ("core", "special", "adjacent")
FILE_KNOWN = (*FILE_GROUPS, "rejected", "chats_unreachable")
# A rejected lead is looked at again after this: channels change hands and
# topics, and a quiet one may come back.
RECHECK_AFTER = timedelta(days=60)
# Links are read from posts pooled this recently.
LINKS_WINDOW = timedelta(days=14)
# Once a day is plenty: the provider's discovery is weekly, and the pool's
# links change by the day. A worker restart must not re-run it.
EVERY = timedelta(hours=20)


def _file_names(groups: tuple[str, ...]) -> list[str]:
    return tg_channels.channel_names(groups=groups)


async def _known(session: AsyncSession, now: datetime) -> set[str]:
    known = {n.casefold() for n in _file_names(FILE_KNOWN)}
    rows = await session.execute(select(FoundChannel.name, FoundChannel.status,
                                        FoundChannel.checked_at))
    for name, status, checked_at in rows:
        if status == "added" or now - checked_at < RECHECK_AFTER:
            known.add(name.casefold())
    return known


async def _seeds(session: AsyncSession) -> list[str]:
    """Channels to ask Telegram for similar ones of: every vetted one."""
    added = await session.execute(
        select(FoundChannel.name).where(FoundChannel.status == "added")
    )
    return [*_file_names(FILE_GROUPS), *added.scalars()]


async def _leads_from_pool(session: AsyncSession, now: datetime) -> dict[str, tg_discovery.Lead]:
    rows = await session.execute(
        select(Pooled.external_id, Pooled.raw_text).where(
            Pooled.source.in_(("telegram", "telethon")),
            Pooled.last_seen_at >= now - LINKS_WINDOW,
            Pooled.raw_text.is_not(None),
        )
    )
    leads: dict[str, tg_discovery.Lead] = {}
    for external_id, text in rows:
        where = external_id.split("/", 1)[0]
        for name in tg_discovery.linked_channels(text):
            if name.casefold() == where.casefold():
                continue
            leads.setdefault(name.casefold(), tg_discovery.Lead(name)).add(f"ссылка в @{where}")
    return leads


def _leads_from_provider(seeds: list[str]) -> tuple[dict[str, tg_discovery.Lead], str | None]:
    """The provider's last weekly discovery; seeds for the next one go back."""
    if not tg_chats.configured():
        return {}, "провайдер не подключён"
    try:
        tg_chats.put_seeds(seeds)
        data = tg_chats.candidates()
    except tg_chats.ProviderError as e:
        return {}, str(e)
    leads: dict[str, tg_discovery.Lead] = {}
    for item in data.get("candidates") or []:
        name = str(item.get("channel") or "")
        if not name:
            continue
        lead = leads.setdefault(name.casefold(), tg_discovery.Lead(name))
        for why in item.get("via") or []:
            lead.add(str(why))
    return leads, None


async def due(session: AsyncSession, now: datetime | None = None) -> bool:
    last = (await session.execute(select(func.max(FoundChannel.checked_at)))).scalar()
    return last is None or (now or datetime.now(UTC)) - last >= EVERY


async def run(session: AsyncSession, limit: int = tg_discovery.MAX_CHECKS) -> dict[str, Any]:
    """One discovery pass. Free: a provider read and up to `limit` previews."""
    now = datetime.now(UTC)
    known = await _known(session, now)
    leads, provider_error = await run_in_threadpool(_leads_from_provider, await _seeds(session))
    for key, lead in (await _leads_from_pool(session, now)).items():
        merged = leads.setdefault(key, tg_discovery.Lead(lead.name))
        for why in lead.via:
            merged.add(why)

    fresh = [lead for key, lead in leads.items() if key not in known]
    # Named by several seeds or posts first: the stronger leads get the checks.
    fresh.sort(key=lambda lead: -len(lead.via))
    verdicts = await run_in_threadpool(tg_discovery.check, fresh[:limit])
    via = {lead.name.casefold(): lead.via for lead in fresh}

    added: list[str] = []
    rejected = unread = 0
    for v in verdicts:
        if v.reason.startswith("не прочитан"):
            unread += 1
            continue
        status = "added" if v.accepted else "rejected"
        values = {
            "status": status, "reason": v.reason[:255],
            "via": via.get(v.name.casefold(), []),
            "title": (v.title or None) and v.title[:255], "subscribers": v.subscribers,
            "posts": v.posts, "vacancies": v.vacancies, "last_post_at": v.last_post_at,
            "checked_at": now,
        }
        # A lead spelled one way may be stored another (the preview's
        # spelling); one row per channel whatever the case.
        existing = (await session.execute(
            select(FoundChannel).where(func.lower(FoundChannel.name) == v.name.casefold())
        )).scalar_one_or_none()
        if existing is None:
            session.add(FoundChannel(name=v.name, **values))
        else:
            for k, value in values.items():
                setattr(existing, k, value)
        if v.accepted:
            # Hidden from users until the owner turns it on; an existing
            # switch (the owner's choice) is left alone.
            await session.execute(
                insert(SourceSetting)
                .values(source="telegram", key=v.name, hidden=True)
                .on_conflict_do_nothing(index_elements=["source", "key"])
            )
            added.append(v.name)
        else:
            rejected += 1
    await session.flush()
    result = {
        "leads": len(leads), "checked": len(verdicts), "added": len(added),
        "rejected": rejected, "unread": unread,
    }
    if provider_error:
        result["provider_error"] = provider_error
    if added:
        log.info("discovery added (hidden): %s", ", ".join(added))
    return result

