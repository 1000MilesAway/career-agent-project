"""The owner's source switches: a hidden channel or board leaves the results,
a renamed company is shown under its new name, and what a user made theirs
survives both.

Fetches are replaced; nothing leaves the process and no model is called.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import Evaluation, Resume, SourceSetting, Vacancy
from app.services import scan, sources
from app.services.pool import Sighting
from core.sources.habr_api import Posting
from tests.dbmark import requires_db
from tests.pool_helpers import make_vacancy

pytestmark = requires_db

PROFILE = {"queries": ["python"], "positive": ["python"]}


@pytest.fixture
async def admin_headers(session, user, auth_headers):
    user.is_admin = True
    await session.commit()
    return auth_headers


def _vacancy(user_id, **fields):
    return make_vacancy(user_id, last_seen_at=datetime.now(UTC), **fields)


async def _found(client, headers) -> dict[str, dict]:
    state = (await client.get("/api/state", headers=headers)).json()
    return {v["title"]: v for v in state["found"]}


async def test_non_admin_cannot_switch(client, auth_headers):
    assert (await client.get("/api/admin/sources", headers=auth_headers)).status_code == 403
    response = await client.put(
        "/api/admin/sources/hidden", headers=auth_headers,
        json={"source": "telegram", "key": "chan", "hidden": True},
    )
    assert response.status_code == 403
    response = await client.put(
        "/api/admin/sources/name", headers=auth_headers, json={"name": "A", "display_name": "B"}
    )
    assert response.status_code == 403


async def test_hidden_channel_leaves_results_but_not_the_saved(
    client, session, user, admin_headers
):
    session.add_all([
        _vacancy(user.id, source="telegram", external_id="chan/1", company="Ромашка",
                 title="Из канала", stage=scan.SHORTLIST),
        _vacancy(user.id, source="telegram", external_id="chan/2", company=None,
                 title="Сохранённая", stage=scan.SHORTLIST, is_saved=True),
        _vacancy(user.id, source="telegram", external_id="other/1", company="Ромашка",
                 title="Из другого", stage=scan.SHORTLIST),
    ])
    await session.commit()

    response = await client.put(
        "/api/admin/sources/hidden", headers=admin_headers,
        json={"source": "telegram", "key": "@chan", "hidden": True},
    )
    assert response.json() == {"source": "telegram", "key": "chan", "hidden": True}
    assert set(await _found(client, admin_headers)) == {"Сохранённая", "Из другого"}

    await client.put(
        "/api/admin/sources/hidden", headers=admin_headers,
        json={"source": "telegram", "key": "chan", "hidden": False},
    )
    assert len(await _found(client, admin_headers)) == 3


async def test_hidden_board_keeps_a_bought_report(client, session, user, admin_headers):
    resume = Resume(user_id=user.id, filename="cv.txt", raw_text="cv", profile={})
    bought = _vacancy(user.id, source="company", external_id="sber/sber/1",
                      company="ПАО Сбербанк", title="С отчётом", stage=scan.EVALUATED)
    session.add_all([
        resume,
        bought,
        _vacancy(user.id, source="company", external_id="sber/sber/2",
                 company="ПАО Сбербанк", title="Без отчёта", stage=scan.SHORTLIST),
        # Same prefix, other board: "huntflow/lime" must not hide "limehr".
        _vacancy(user.id, source="company", external_id="huntflow/limehr/1",
                 company="Lime", title="Lime", stage=scan.SHORTLIST),
    ])
    await session.flush()
    session.add(Evaluation(vacancy_id=bought.id, resume_id=resume.id, report={}, model="m"))
    session.add_all([
        SourceSetting(source="company", key="sber/sber", hidden=True),
        SourceSetting(source="company", key="huntflow/lime", hidden=True),
    ])
    await session.commit()

    assert set(await _found(client, admin_headers)) == {"С отчётом", "Lime"}


async def test_rename_shows_everywhere_and_clears(client, session, user, admin_headers):
    session.add(_vacancy(user.id, source="company", external_id="sber/sber/1",
                         company="ПАО Сбербанк", title="Аналитик", stage=scan.SHORTLIST))
    await session.commit()

    response = await client.put(
        "/api/admin/sources/name", headers=admin_headers,
        json={"name": "ПАО Сбербанк", "display_name": "  Сбербанк "},
    )
    assert response.json()["display_name"] == "Сбербанк"
    assert (await _found(client, admin_headers))["Аналитик"]["company"] == "Сбербанк"
    listing = (await client.get("/api/vacancies", headers=admin_headers)).json()
    assert listing[0]["company"] == "Сбербанк"

    await client.put(
        "/api/admin/sources/name", headers=admin_headers,
        json={"name": "ПАО Сбербанк", "display_name": ""},
    )
    assert (await _found(client, admin_headers))["Аналитик"]["company"] == "ПАО Сбербанк"


async def test_listing_groups_boards_and_names(client, session, user, admin_headers):
    session.add_all([
        _vacancy(user.id, source="company", external_id="sber/sber/1", company="ПАО Сбербанк"),
        _vacancy(user.id, source="company", external_id="sber/sber/2", company="ПАО Сбербанк"),
        _vacancy(user.id, source="company", external_id="sber/sber/3", company='АО "СберТех"'),
        _vacancy(user.id, source="telegram", external_id="lonely/1", company="Evenbeter"),
        _vacancy(user.id, source="telegram", external_id="lonely/2", company=None),
        _vacancy(user.id, source="habr", external_id="1", company="Ромашка"),
        SourceSetting(source="telegram", key="lonely", hidden=True),
    ])
    await session.commit()

    data = (await client.get("/api/admin/sources", headers=admin_headers)).json()

    sber = next(b for b in data["boards"] if b["key"] == "sber/sber")
    assert sber["label"] == "Сбер"
    assert sber["postings"] == 3
    assert [(n["name"], n["postings"]) for n in sber["names"]] == [
        ("ПАО Сбербанк", 2), ('АО "СберТех"', 1),
    ]
    # Only in the pool, from a named scan: listed so it can be switched.
    lonely = next(c for c in data["channels"] if c["key"] == "lonely")
    assert (lonely["group"], lonely["hidden"], lonely["postings"]) == (None, True, 2)
    # Counted per channel (the id's prefix), not split by the employers posts name.
    assert "names" not in lonely
    habr = next(a for a in data["aggregators"] if a["source"] == "habr")
    assert habr["postings"] == habr["active"] == 1


async def test_hidden_parts_are_neither_read_nor_matched(session, monkeypatch):
    from app.models import User

    user = User(email=f"u{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    session.add_all([
        user,
        SourceSetting(source="telegram", key="hidden_chan", hidden=True),
        SourceSetting(source="company", key="sber/sber", hidden=True),
    ])
    await session.flush()

    asked: dict[str, frozenset] = {}

    def telegram(channels, group, pages, skip=frozenset(), found=()):
        asked["telegram"] = skip
        return scan.Batch("telegram", [
            Sighting(Posting(id=f"{c}/1", url="u", title="Python", company=""), "Python")
            for c in ("hidden_chan", "open_chan")
        ])

    def company(queries, group, skip=frozenset()):
        asked["company"] = skip
        return scan.Batch("company", [
            Sighting(Posting(id="sber/sber/1", url="u", title="Python", company="Сбер"))
        ])

    monkeypatch.setattr(scan, "fetch_telegram", telegram)
    monkeypatch.setattr(scan, "fetch_employers", company)
    result = await scan.collect(session, user.id, PROFILE, ("telegram", "company"))
    await session.commit()

    assert asked == {"telegram": {"hidden_chan"}, "company": {"sber/sber"}}
    # Pooled for when the switch goes back, matched to nobody meanwhile.
    rows = (await session.execute(select(Vacancy))).scalars().all()
    assert [r.external_id for r in rows] == ["open_chan/1"]
    assert result["new"] == 1


def test_switches_without_settings_hide_nothing():
    state = sources.Switches()
    assert not state.is_hidden("telegram", "chan/1")
    assert state.company("ПАО Сбербанк") == "ПАО Сбербанк"
    assert state.company(None) is None


@pytest.fixture
def chat_provider(monkeypatch):
    """A fake tg-provider holding the chat list in memory."""
    from core.sources import tg_chats

    monkeypatch.setenv("TG_PROVIDER_URL", "http://provider:8091")
    monkeypatch.setenv("TG_PROVIDER_TOKEN", "secret")
    listed = [{"chat": "hr_chat", "enabled": True, "added_at": None}]

    def set_chat(name, enabled):
        if name == "bad name":
            raise tg_chats.ProviderError("провайдер Telegram-чатов ответил 400: not a chat")
        chat = name.removeprefix("https://t.me/").removeprefix("@")
        for item in listed:
            if item["chat"] == chat:
                item["enabled"] = enabled
                return item
        listed.append({"chat": chat, "enabled": enabled, "added_at": "2026-10-16T06:00:00+00:00"})
        return listed[-1]

    status = {"finished_at": "2026-10-16T06:21:00+00:00",
              "chats": [{"chat": "hr_chat", "emitted": 12}]}
    monkeypatch.setattr(tg_chats, "list_chats", lambda: (listed, status))
    monkeypatch.setattr(tg_chats, "set_chat", set_chat)
    return listed


async def test_chats_are_listed_added_and_switched(
    client, session, user, admin_headers, chat_provider
):
    session.add(_vacancy(user.id, source="telethon", external_id="hr_chat/1",
                         company="CVAT.ai", title="Из чата", stage=scan.SHORTLIST))
    await session.commit()

    data = (await client.get("/api/admin/sources", headers=admin_headers)).json()
    assert data["chats_error"] is None
    assert [(c["key"], c["hidden"], c["emitted"], c["postings"]) for c in data["chats"]] == [
        ("hr_chat", False, 12, 1)
    ]
    assert "names" not in data["chats"][0]
    # Telethon has its own tab, not a row among the aggregators.
    assert "telethon" not in {a["source"] for a in data["aggregators"]}

    added = await client.post(
        "/api/admin/sources/chats", headers=admin_headers, json={"chat": "https://t.me/devops_jobs"}
    )
    assert added.json() == {"source": "telethon", "key": "devops_jobs", "hidden": False}
    bad = await client.post(
        "/api/admin/sources/chats", headers=admin_headers, json={"chat": "bad name"}
    )
    assert bad.status_code == 422

    off = await client.put(
        "/api/admin/sources/hidden", headers=admin_headers,
        json={"source": "telethon", "key": "hr_chat", "hidden": True},
    )
    assert off.status_code == 200
    assert chat_provider[0]["enabled"] is False
    assert "Из чата" not in await _found(client, admin_headers)


async def test_chats_without_provider_say_why(client, admin_headers, monkeypatch):
    monkeypatch.delenv("TG_PROVIDER_URL", raising=False)
    data = (await client.get("/api/admin/sources", headers=admin_headers)).json()
    assert data["chats"] == [] and "не подключён" in data["chats_error"]
    response = await client.post(
        "/api/admin/sources/chats", headers=admin_headers, json={"chat": "devops_jobs"}
    )
    assert response.status_code == 503
