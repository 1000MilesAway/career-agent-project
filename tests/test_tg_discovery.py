"""Telegram channel discovery: leads judged by their preview, added hidden.

No network: previews are built here and the provider is faked. The tests at
the bottom need Postgres and skip without it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import FoundChannel, SourceSetting
from app.services import channel_discovery, scan, sources
from core.sources import telegram, tg_chats, tg_discovery
from tests.dbmark import requires_db
from tests.pool_helpers import make_vacancy

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
VACANCY = (
    "Python Backend Developer (Middle)\n"
    "Компания: Ромашка. Ищем разработчика в команду платёжного сервиса.\n"
    "Требования: Python 3.12, FastAPI, PostgreSQL, опыт от трёх лет.\n"
    "Условия: удалённо, зарплата от 250 000 ₽, официальное оформление.\n"
    "Откликнуться: https://example.com/jobs/1"
)
CHATTER = "Как вам новый релиз Python? Делитесь мнением в комментариях."


def _preview(texts: list[str], channel: str = "jobs_ru", days_ago: int = 1,
             subscribers: int | None = 5000) -> telegram.Preview:
    when = (NOW - timedelta(days=days_ago)).isoformat()
    posts = [
        telegram.build_post(channel, f"{channel}/{i}", text, when, [])
        for i, text in enumerate(texts, 1)
    ]
    return telegram.Preview(posts=posts, title="Вакансии", subscribers=subscribers)


def test_links_to_channels_but_not_bots_or_service_paths():
    text = (
        "Ещё вакансии: https://t.me/pypijob и t.me/s/JobGolang, "
        "бот t.me/hh_vacancy_bot, папка t.me/addlist/abcde, пост t.me/rabotaphp/123"
    )
    assert tg_discovery.linked_channels(text) == {"pypijob", "JobGolang", "rabotaphp"}


def test_a_job_channel_is_accepted_with_its_own_spelling():
    verdict = tg_discovery.judge("JOBS_RU", _preview([VACANCY] * 8 + [CHATTER] * 2), NOW)
    assert verdict.accepted
    assert verdict.name == "jobs_ru"
    assert (verdict.posts, verdict.vacancies, verdict.reason) == (10, 8, "вакансий 8 из 10")


@pytest.mark.parametrize("preview, reason", [
    (_preview([VACANCY] * 3), "в превью 3 постов"),
    (_preview([VACANCY] * 8, days_ago=90), "молчит с 2026-07-20"),
    (_preview([VACANCY] * 8, subscribers=120), "подписчиков 120"),
    (_preview([VACANCY.replace("Ромашка", "Acme")] * 2 + ["Senior Go developer wanted. " * 12] * 6),
     "по-русски 2 постов из 8"),
    (_preview([VACANCY] * 3 + [CHATTER] * 7), "вакансий 3 из 10"),
])
def test_a_channel_that_is_not_a_live_russian_job_feed_is_rejected(preview, reason):
    verdict = tg_discovery.judge("jobs_ru", preview, NOW)
    assert not verdict.accepted
    assert verdict.reason == reason


def test_preview_header_gives_title_and_subscribers():
    html = (
        '<div class="tgme_channel_info_header_title"><span>Вакансии Python</span></div>'
        '<div class="tgme_channel_info_counter"><span class="counter_value">9.24K</span> '
        '<span class="counter_type">subscribers</span></div>'
        '<div class="tgme_channel_info_counter"><span class="counter_value">298</span> '
        '<span class="counter_type">photos</span></div>'
    )
    preview = telegram.parse_preview(html, "pypijob")
    assert (preview.title, preview.subscribers, preview.posts) == ("Вакансии Python", 9240, [])


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("TG_PROVIDER_URL", "http://provider:8091")
    monkeypatch.setenv("TG_PROVIDER_TOKEN", "secret")
    seeds: list[list[str]] = []
    monkeypatch.setattr(tg_chats, "put_seeds", lambda channels: seeds.append(channels) or {})
    monkeypatch.setattr(tg_chats, "candidates", lambda: {
        "finished_at": "2026-10-13T07:30:00+00:00",
        "candidates": [
            {"channel": "pypijob", "via": ["похож на @pydevjob", "похож на @godevjob"]},
            {"channel": "pyproglib", "via": ["похож на @pydevjob"]},
            # Already in the list file.
            {"channel": "godevjob", "via": ["похож на @pydevjob"]},
        ],
    })
    return seeds


@pytest.fixture
def previews(monkeypatch):
    checked: list[str] = []

    def check(leads):
        checked.extend(lead.name for lead in leads)
        out = []
        for lead in leads:
            texts = [VACANCY] * 9 if lead.name.casefold() != "pyproglib" else [CHATTER] * 9
            out.append(tg_discovery.judge(lead.name, _preview(texts, lead.name.lower()),
                                          datetime.now(UTC) + timedelta(days=1)))
        return out

    monkeypatch.setattr(tg_discovery, "check", check)
    return checked


@requires_db
class TestRun:
    async def test_job_channels_are_added_hidden_the_rest_kept_with_a_reason(
        self, session, user, provider, previews
    ):
        session.add(make_vacancy(
            user.id, source="telegram", external_id="godevjob/7", title="Go",
            raw_text="Больше вакансий в t.me/RabotaPHP", last_seen_at=datetime.now(UTC),
        ))
        await session.commit()

        result = await channel_discovery.run(session)
        await session.commit()

        assert "godevjob" not in previews
        assert previews[0] == "pypijob"  # named by two seeds: checked first
        assert (result["added"], result["rejected"]) == (2, 1)
        rows = {r.name: r for r in (await session.execute(select(FoundChannel))).scalars()}
        assert rows["pypijob"].status == "added"
        assert rows["pypijob"].via == ["похож на @pydevjob", "похож на @godevjob"]
        assert rows["rabotaphp"].via == ["ссылка в @godevjob"]
        assert (rows["pyproglib"].status, rows["pyproglib"].reason) == (
            "rejected", "вакансий 0 из 9"
        )
        hidden = (await session.execute(
            select(SourceSetting.key).where(SourceSetting.hidden.is_(True))
        )).scalars().all()
        assert set(hidden) == {"pypijob", "rabotaphp"}
        # The file's channels go to the provider as seeds for the next week.
        assert "godevjob" in provider[0]

    async def test_a_second_run_skips_what_it_judged_and_the_owners_switch_stays(
        self, session, provider, previews
    ):
        await channel_discovery.run(session)
        # The owner turned it on.
        (await session.get(SourceSetting, ("telegram", "pypijob"))).hidden = False
        await session.commit()
        previews.clear()

        await channel_discovery.run(session)
        await session.commit()

        assert previews == []
        assert not await channel_discovery.due(session)
        switch = await session.get(SourceSetting, ("telegram", "pypijob"))
        assert switch.hidden is False
        # Seeds now include the channel the discovery added.
        assert "pypijob" in provider[-1]

        await channel_discovery.run(session, recheck=True)
        assert previews == ["pyproglib"]

    async def test_a_switched_on_channel_is_read_with_its_group(self, session, monkeypatch):
        from app.models import User

        user = User(email=f"u{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
        session.add_all([
            user,
            FoundChannel(name="pypijob", status="added", reason="вакансий 9 из 9", via=[],
                         checked_at=datetime.now(UTC)),
            FoundChannel(name="hidden_one", status="added", reason="вакансий 9 из 9", via=[],
                         checked_at=datetime.now(UTC)),
            FoundChannel(name="spam", status="rejected", reason="вакансий 0 из 9", via=[],
                         checked_at=datetime.now(UTC)),
            SourceSetting(source="telegram", key="hidden_one", hidden=True),
        ])
        await session.flush()
        state = await sources.load(session)
        assert sorted(state.found["core"]) == ["hidden_one", "pypijob"]

        asked: dict[str, list[str]] = {}

        def search_many(names, pages=1):
            asked["names"] = names
            return [], []

        monkeypatch.setattr(scan.telegram, "search_many", search_many)
        await scan.collect(
            session, user.id, {"queries": ["python"], "positive": ["python"]}, ("telegram",)
        )
        assert "pypijob" in asked["names"]
        assert "hidden_one" not in asked["names"]
        assert "spam" not in asked["names"]

    async def test_admin_lists_a_found_channel_with_how_it_was_found(
        self, client, session, user, auth_headers
    ):
        user.is_admin = True
        session.add_all([
            FoundChannel(name="pypijob", status="added", reason="вакансий 9 из 10",
                         via=["похож на @pydevjob"], subscribers=9240,
                         checked_at=datetime.now(UTC)),
            SourceSetting(source="telegram", key="pypijob", hidden=True),
        ])
        await session.commit()

        data = (await client.get("/api/admin/sources", headers=auth_headers)).json()
        item = next(c for c in data["channels"] if c["key"] == "pypijob")
        assert (item["group"], item["hidden"]) == ("core", True)
        assert item["found"]["via"] == ["похож на @pydevjob"]
        assert item["found"]["subscribers"] == 9240

