"""geekjob.ru and the schema.org JobPosting reader it relies on.

Fixtures are real pages saved 2026-09-23, scripts and styles stripped, JSON-LD
kept. No network here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from core.sources import geekjob, jobposting

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestListing:
    def test_every_card_on_the_page_is_read(self):
        rows = geekjob.parse_list(fixture("geekjob_list.html"), today=date(2026, 9, 23))
        assert len(rows) == 6
        first = rows[0]
        assert first.id == "6ab2b32973abaf8df70aa37d"
        assert first.url == "https://geekjob.ru/vacancy/6ab2b32973abaf8df70aa37d"
        assert first.title == "Senior Backend Developer (Python + AI)"
        assert first.remote is True
        assert first.published == "2026-09-22"

    def test_a_rouble_band_feeds_the_filter(self):
        rows = geekjob.parse_list(fixture("geekjob_list.html"), today=date(2026, 9, 23))
        first = rows[0]
        assert (first.salary_from, first.salary_to) == (270_000, 270_000)
        assert first.salary_text == "270K ₽"

    def test_a_dollar_band_travels_as_text_only(self):
        """`salary_min` is roubles: 4500 from "4.5K $" would drop a well-paid job."""
        rows = geekjob.parse_list(fixture("geekjob_list.html"), today=date(2026, 9, 23))
        dollars = next(r for r in rows if "$" in r.salary_text)
        assert dollars.salary_from is None and dollars.salary_to is None
        assert dollars.currency == "USD"

    def test_an_office_role_is_not_remote(self):
        rows = geekjob.parse_list(fixture("geekjob_list.html"), today=date(2026, 9, 23))
        office = next(r for r in rows if r.title == "Специалист отдела кадров")
        assert office.remote is False
        assert office.location == "Череповец, Россия"

    def test_a_card_from_a_later_month_is_last_year(self):
        """The card says "22 сентября" with no year; in January it is last September."""
        rows = geekjob.parse_list(fixture("geekjob_list.html"), today=date(2027, 1, 5))
        assert rows[0].published == "2026-09-22"

    def test_cards_that_no_longer_parse_fail_loudly(self):
        """A redesign must not read as "no vacancies today"."""
        html = '<ul><li class="collection-item avatar"><p>что-то новое</p></li></ul>'
        with pytest.raises(geekjob.MarkupChanged):
            geekjob.parse_list(html)

    def test_an_empty_page_is_just_empty(self):
        assert geekjob.parse_list("<html><body></body></html>") == []

    def test_a_link_off_the_board_is_ignored(self):
        html = fixture("geekjob_list.html").replace(
            'href="/vacancy/6ab2b32973abaf8df70aa37d" class="title"',
            'href="https://evil.example/vacancy/6ab2b32973abaf8df70aa37d" class="title"',
        )
        ids = [r.id for r in geekjob.parse_list(html, today=date(2026, 9, 23))]
        assert "6ab2b32973abaf8df70aa37d" not in ids


class TestCollect:
    def _client(self, pages: dict[str, str]) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            body = pages.get(str(request.url))
            if body is None:
                return httpx.Response(404)
            return httpx.Response(200, text=body)

        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_stops_when_the_board_repeats_itself(self, monkeypatch):
        monkeypatch.setattr(geekjob, "DELAY_SECONDS", 0)
        page = fixture("geekjob_list.html")
        client = self._client(
            {geekjob.list_url(1): page, geekjob.list_url(2): page, geekjob.list_url(3): page}
        )
        rows = geekjob.collect(pages=3, client=client)
        assert len(rows) == 6

    def test_stops_at_a_missing_page(self, monkeypatch):
        monkeypatch.setattr(geekjob, "DELAY_SECONDS", 0)
        client = self._client({geekjob.list_url(1): fixture("geekjob_list.html")})
        assert len(geekjob.collect(pages=5, client=client)) == 6


class TestJobPosting:
    def test_the_full_vacancy_comes_from_json_ld(self):
        posting = jobposting.parse(fixture("geekjob_vacancy.html"))
        assert posting is not None
        assert posting.title == "Senior Backend Developer (Python + AI)"
        assert posting.company == "Агентство Martsun & Partners"
        assert posting.salary_from == 270_000
        assert posting.currency == "RUB"
        assert posting.date_posted == "2026-09-22"
        # The description, as prose: tags gone, list items on their own lines.
        assert "Обязанности:" in posting.description
        assert "<" not in posting.description

    def test_as_text_leads_with_the_fields(self):
        text = jobposting.parse(fixture("geekjob_vacancy.html")).as_text()
        assert text.startswith("Senior Backend Developer (Python + AI)\nКомпания:")
        assert "Зарплата: 270000 RUB" in text

    def test_a_page_without_a_posting_is_none(self):
        assert jobposting.parse("<html><body>нет вакансии</body></html>") is None

    def test_a_broken_block_does_not_hide_the_next_one(self):
        html = (
            '<script type="application/ld+json">{ битый </script>'
            '<script type="application/ld+json">'
            '{"@graph": [{"@type": "JobPosting", "title": "QA"}]}</script>'
        )
        assert jobposting.parse(html).title == "QA"

    def test_a_yearly_band_becomes_monthly_and_an_hourly_one_is_dropped(self):
        yearly = jobposting._salary(
            {"currency": "USD", "value": {"minValue": 120000, "unitText": "YEAR"}}
        )
        assert yearly[0] == 10_000
        hourly = jobposting._salary({"currency": "USD", "value": {"value": 50, "unitText": "HOUR"}})
        assert hourly[:2] == (None, None)

    def test_expiry_needs_a_date(self):
        """No `validThrough` is absence of evidence, not a closed vacancy."""
        assert jobposting.JobPosting(title="x").expired() is False
        past = jobposting.JobPosting(title="x", valid_through="2026-01-01")
        assert past.expired(today=date(2026, 9, 23)) is True
        garbage = jobposting.JobPosting(title="x", valid_through="скоро")
        assert garbage.expired() is False


class TestFetchVacancy:
    def test_an_archived_page_is_refused_not_scored(self):
        """The archive page still carries the old JSON-LD — only the banner says it is dead."""
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, text=fixture("geekjob_archived.html"))
            )
        )
        with pytest.raises(ValueError, match="архив"):
            geekjob.fetch_vacancy("https://geekjob.ru/vacancy/5f8484c8c80e7e5472532a51", client)

    def test_only_geekjob_urls_are_fetched(self):
        with pytest.raises(ValueError):
            geekjob.fetch_vacancy("https://geekjob.ru.evil.example/vacancy/abc")
