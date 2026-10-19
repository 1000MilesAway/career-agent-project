"""hh.ru through the hh-scrapper provider.

The provider is replaced by a fake `httpx.get`; nothing leaves the process.
The scan tests at the bottom need Postgres and skip without it.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.models import Posting as PooledPosting
from app.models import Vacancy
from app.services import pool, scan
from core import dedup, pipeline
from core.filters import SearchProfile
from core.sources import hh
from tests.dbmark import requires_db

CARD = {
    "id": "137961395",
    "url": "https://hh.ru/vacancy/137961395",
    "title": "Аналитик данных",
    "company": "МЕДИА ХИЛС",
    "area": "Москва",
    "salary_from": 130000,
    "salary_to": 160000,
    "currency": "RUB",
    "salary": {"mode": "MONTH", "salary_from": 130000, "salary_to": 160000, "currency": "RUB"},
    "experience": "between1And3",
    "experience_text": "От 1 года до 3 лет",
    "work_formats": ["REMOTE"],
    "remote": True,
    "skills": ["SQL", "Python"],
    "published": "2026-10-03T12:00:00+03:00",
}


@pytest.fixture
def provider(monkeypatch):
    """Configure the provider and record what is asked of it."""
    monkeypatch.setenv("HH_PROVIDER_URL", "http://provider:8090/")
    monkeypatch.setenv("HH_PROVIDER_TOKEN", "secret")
    calls: list[tuple[str, object]] = []
    answers: dict[str, httpx.Response] = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        assert headers == {"Authorization": "Bearer secret"}
        path = url.removeprefix("http://provider:8090")
        return answers.get(path, httpx.Response(404, json={"detail": "Not Found"}))

    monkeypatch.setattr(hh.httpx, "get", fake_get)
    return calls, answers


def test_not_configured_is_off_not_broken(monkeypatch):
    monkeypatch.delenv("HH_PROVIDER_URL", raising=False)
    monkeypatch.delenv("HH_PROVIDER_TOKEN", raising=False)
    assert hh.configured() is False
    assert "hh" not in scan.active_sources()
    with pytest.raises(ValueError, match="hh.ru не подключён"):
        scan.plan("hh", SearchProfile(queries=["python"]), scan.Options())


def test_configured_joins_the_scan_of_everything(provider):
    assert "hh" in scan.active_sources()


def test_vacancy_urls():
    assert hh.vacancy_id("https://hh.ru/vacancy/137961395") == "137961395"
    assert hh.vacancy_id("https://spb.hh.ru/vacancy/137961395?from=x") == "137961395"
    assert hh.vacancy_id("https://hh.ru.evil.com/vacancy/137961395") is None
    assert hh.vacancy_id("http://hh.ru/vacancy/137961395") is None
    assert hh.vacancy_id("https://hh.ru/employer/1740") is None


def test_search_asks_russia_once_and_maps_cards(provider):
    calls, answers = provider
    answers["/search"] = httpx.Response(200, json={"vacancies": [CARD], "truncated": True})
    postings, truncated = hh.search("python", remote=True)

    (url, params), = calls
    assert url == "http://provider:8090/search"
    assert ("area", hh.AREA_RUSSIA) in params
    assert ("work_format", "REMOTE") in params
    assert ("pages", 1) in params
    assert truncated is True
    p, = postings
    assert (p.id, p.title, p.company, p.location) == ("137961395", "Аналитик данных",
                                                      "МЕДИА ХИЛС", "Москва")
    assert (p.salary_from, p.salary_to, p.currency) == (130000, 160000, "RUB")
    assert p.salary_text == "130 000–160 000 ₽"
    assert p.remote is True
    assert p.skills == ["SQL", "Python"]
    # hh states years, not a grade; the grade filter must not judge a guess.
    assert p.qualification == ""


def test_shift_pay_is_named_and_not_a_monthly_band():
    card = {**CARD, "salary_from": None, "salary_to": None,
            "salary": {"mode": "SHIFT", "salary_from": 5000, "salary_to": None,
                       "currency": "RUB"}}
    p = hh.as_posting(card)
    assert p.salary_from is None
    assert p.salary_text == "от 5 000 ₽ за смену"


def test_a_captcha_is_an_error_not_an_empty_list(provider):
    _, answers = provider
    answers["/search"] = httpx.Response(503, json={"error": "blocked", "detail": "captcha"})
    with pytest.raises(hh.ProviderError, match="капча"):
        hh.search("python")


def test_provider_down_is_an_error(monkeypatch, provider):
    def boom(*a, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(hh.httpx, "get", boom)
    with pytest.raises(hh.ProviderError, match="не ответил"):
        hh.search("python")


def test_evaluation_text_comes_from_the_vacancy_page(provider):
    _, answers = provider
    full = {**CARD, "description": "Ищем аналитика.\n- SQL", "archived": False}
    answers["/vacancy/137961395"] = httpx.Response(200, json={"status": "ok", "vacancy": full})
    text, meta = pipeline.load_vacancy("https://hh.ru/vacancy/137961395")
    assert text.startswith("Аналитик данных\nКомпания: МЕДИА ХИЛС")
    assert "Формат: удалённо" in text
    assert text.endswith("Ищем аналитика.\n- SQL")
    assert meta["title"] == "Аналитик данных"


SNIPPET = {"requirement": "Опыт работы с SQL от года.", "responsibility": "Строить отчёты.",
           "conditions": ""}


def test_card_text_keeps_the_snippet_labelled_as_a_fragment():
    text = hh.card_text({**CARD, "snippet": SNIPPET})
    assert text.startswith("Аналитик данных\nКомпания: МЕДИА ХИЛС")
    assert "полное описание не загружено" in text
    assert "Требования: Опыт работы с SQL от года." in text
    assert "Обязанности: Строить отчёты." in text
    # An empty part of the snippet is not printed as an empty label.
    assert "Условия" not in text


def test_a_card_without_a_snippet_has_no_text():
    assert hh.card_text({**CARD, "snippet": None}) == ""


def _pooled(**kw):
    posting = PooledPosting(source="hh", external_id="137961395", title="Аналитик данных",
                            url="https://hh.ru/vacancy/137961395", **kw)
    return Vacancy(posting=posting)


def test_only_an_unread_hh_card_is_a_snippet():
    assert pool.is_snippet(_pooled(raw_text="x", needs_text=True).posting)
    assert not pool.is_snippet(_pooled(raw_text="x", needs_text=False).posting)
    # A Telegram teaser waiting for its page is the post itself, not a snippet.
    teaser = PooledPosting(source="telegram", external_id="c/1", title="t", needs_text=True)
    assert not pool.is_snippet(teaser)


async def test_evaluation_replaces_the_snippet_with_the_description(provider):
    from app.api.evaluations import _vacancy_text

    calls, answers = provider
    full = {**CARD, "description": "Ищем аналитика.\n- SQL", "archived": False}
    answers["/vacancy/137961395"] = httpx.Response(200, json={"status": "ok", "vacancy": full})
    vacancy = _pooled(raw_text=hh.card_text({**CARD, "snippet": SNIPPET}), needs_text=True)

    text = await _vacancy_text(vacancy)

    assert text.endswith("Ищем аналитика.\n- SQL")
    assert vacancy.posting.raw_text == text
    assert vacancy.posting.needs_text is False
    # Read once: the next evaluation takes the stored description.
    assert await _vacancy_text(vacancy) == text
    assert len(calls) == 1


def test_evaluation_of_a_deleted_vacancy_says_so(provider):
    _, answers = provider
    answers["/vacancy/1234567"] = httpx.Response(200, json={"status": "not_found",
                                                            "vacancy": None})
    with pytest.raises(ValueError, match="не найдена"):
        pipeline.load_vacancy("https://hh.ru/vacancy/1234567")


def test_a_telegram_post_linking_hh_points_at_the_hh_posting():
    text = "Подробнее и отклик: https://spb.hh.ru/vacancy/137961395?utm=tg"
    assert ("hh", "137961395") in dedup.linked_vacancies(text)


def test_hh_listing_is_read_once_a_day():
    assert pool.FRESH_FOR["hh"] == timedelta(days=1)


def test_plan_has_one_listing_per_query(provider):
    p = scan.plan("hh", SearchProfile(queries=["Python", "Django "], remote_only=True),
                  scan.Options())
    assert len(p.listings) == 2
    # Same query, other case: same listing, so a second user's scan reuses it.
    again = scan.plan("hh", SearchProfile(queries=["python"], remote_only=True), scan.Options())
    assert again.listings[0].key == p.listings[0].key


async def _user(session):
    import uuid

    from app.models import User

    user = User(email=f"u{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    return user


@requires_db
class TestScanHh:
    async def test_second_scan_within_a_day_does_not_ask_hh(self, session, monkeypatch, provider):
        calls, answers = provider
        card = {**CARD, "snippet": SNIPPET}
        answers["/search"] = httpx.Response(200, json={"vacancies": [card], "truncated": False})
        profile = {"queries": ["аналитик"], "positive": ["аналитик"]}

        first = await scan.collect(session, (await _user(session)).id, profile, ("hh",))
        second = await scan.collect(session, (await _user(session)).id, profile, ("hh",))
        await session.commit()

        assert len(calls) == 1
        assert first["sources"]["hh"]["new"] == 1
        assert second["sources"]["hh"]["from_pool"] == 1
        assert second["sources"]["hh"]["new"] == 1
        pooled = (await session.execute(
            select(PooledPosting).where(PooledPosting.source == "hh")
        )).scalars().all()
        assert [p.external_id for p in pooled] == ["137961395"]
        # The snippet is kept; the description is read on evaluation.
        assert "Требования: Опыт работы с SQL от года." in pooled[0].raw_text
        assert pool.is_snippet(pooled[0])
        # One employer's cards for several cities share a snippet: no
        # fingerprint, or they would fold into one vacancy.
        assert pooled[0].text_fp is None

    async def test_a_card_pooled_without_text_gains_the_snippet(
        self, session, monkeypatch, provider
    ):
        from core.sources.habr_api import Posting

        _, answers = provider
        answers["/search"] = httpx.Response(
            200, json={"vacancies": [{**CARD, "snippet": SNIPPET}], "truncated": False}
        )
        await pool.upsert(session, "hh", [pool.Sighting(Posting(
            id="137961395", url=CARD["url"], title=CARD["title"], company=CARD["company"],
        ))])
        await pool.upsert(session, "hh", scan.fetch_hh("аналитик").sightings)
        row = (await session.execute(
            select(PooledPosting).where(PooledPosting.source == "hh")
        )).scalar_one()
        assert "Обязанности: Строить отчёты." in row.raw_text
        assert pool.is_snippet(row)

    async def test_provider_failure_is_reported_by_name(self, session, monkeypatch, provider):
        _, answers = provider
        answers["/search"] = httpx.Response(503, json={"error": "blocked"})
        user = await _user(session)
        result = await scan.collect(
            session, user.id, {"queries": ["python"], "positive": ["python"]}, ("hh",)
        )
        assert "капча" in result["sources"]["hh"]["error"]
        rows = (await session.execute(select(Vacancy).where(Vacancy.user_id == user.id)))
        assert rows.scalars().all() == []
