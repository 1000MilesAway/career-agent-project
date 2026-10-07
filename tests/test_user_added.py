"""A vacancy the user brings by link or text, read without the model.

No network: pages are served by an httpx MockTransport, and name resolution
is patched where the private-address guard is under test.
"""

from __future__ import annotations

import json
import socket

import httpx
import pytest

from core.sources import user_added

OWNER = "0b6d4c1e-0000-0000-0000-000000000001"

TEXT = """🔥
Senior Python-разработчик
Компания: ООО Ромашка
Грейд: Senior
Стек: Python, FastAPI, PostgreSQL
Зарплата: от 300 000 до 400 000 ₽
Удалённо, полный день.
Обязанности: развивать платёжный сервис, писать тесты, ревьюить код коллег.
"""


def _page(posting: dict) -> str:
    return (
        "<html><head><script type='application/ld+json'>"
        + json.dumps(posting, ensure_ascii=False)
        + "</script></head><body>menu, footer</body></html>"
    )


JOB = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Аналитик данных",
    "hiringOrganization": {"@type": "Organization", "name": "Пример"},
    "description": "<p>Строить отчёты, считать метрики, говорить с бизнесом.</p>",
    "datePosted": "2026-10-01",
}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port: [(None, None, None, "", ("93.184.216.34", 0))]
    )


class TestText:
    def test_labelled_fields_are_read_and_the_rest_is_left_unknown(self):
        added = user_added.from_text(TEXT, OWNER)
        p = added.posting
        assert added.source == "text"
        # The lone emoji line is not a title (the godevjob lesson).
        assert p.title == "Senior Python-разработчик"
        assert p.company == "ООО Ромашка"
        assert p.qualification == "Senior"
        assert p.skills == ["Python", "FastAPI", "PostgreSQL"]
        assert (p.salary_from, p.salary_to, p.currency) == (300_000, 400_000, "RUB")
        assert p.remote is True
        assert p.url == ""
        assert added.text.startswith("🔥\nSenior")

    def test_silence_on_remote_work_is_unknown_not_office(self):
        text = TEXT.replace("Удалённо, полный день.\n", "")
        assert user_added.from_text(text, OWNER).posting.remote is None

    def test_the_id_is_the_owners_and_the_same_text_is_the_same_posting(self):
        a = user_added.from_text(TEXT, OWNER)
        b = user_added.from_text(TEXT + "\n\n\n", OWNER)
        assert a.posting.id == b.posting.id
        assert a.posting.id.startswith(f"{OWNER}:")
        assert user_added.from_text(TEXT, "someone-else").posting.id != a.posting.id

    def test_a_title_alone_is_refused(self):
        with pytest.raises(ValueError, match="короткий"):
            user_added.from_text("Python-разработчик", OWNER)

    def test_a_whole_page_is_refused(self):
        with pytest.raises(ValueError, match="длинный"):
            user_added.from_text("слово " * 5000, OWNER)


class TestIsLink:
    def test_a_lone_url_is_a_link(self):
        assert user_added.is_link("  https://example.com/jobs/1 \n")

    def test_a_text_with_a_link_inside_is_text(self):
        assert not user_added.is_link("Вакансия: https://example.com/jobs/1 откликайтесь")


class TestPage:
    def test_a_jobposting_page_is_read(self, public_dns):
        client = _client(lambda request: httpx.Response(200, text=_page(JOB)))
        added = user_added.from_url("https://jobs.example.com/a/1?utm=x#top", client)
        assert added.source == "url"
        assert added.posting.title == "Аналитик данных"
        assert added.posting.company == "Пример"
        assert "считать метрики" in added.text
        assert added.closed_reason is None

    def test_the_same_page_is_the_same_posting(self):
        assert user_added.page_id("https://Jobs.example.com/a/1/") == user_added.page_id(
            "https://jobs.example.com/a/1"
        )

    def test_an_expired_posting_is_added_closed(self, public_dns):
        job = {**JOB, "validThrough": "2020-01-01"}
        client = _client(lambda request: httpx.Response(200, text=_page(job)))
        assert user_added.from_url("https://jobs.example.com/a/1", client).closed_reason

    def test_a_page_without_a_posting_asks_for_the_text(self, public_dns):
        client = _client(lambda request: httpx.Response(200, text="<html>menu</html>"))
        with pytest.raises(ValueError, match="вставьте"):
            user_added.from_url("https://example.com/careers", client)

    def test_a_private_address_is_never_fetched(self, monkeypatch):
        """The server must not become a proxy into its own network."""
        monkeypatch.setattr(
            socket, "getaddrinfo", lambda host, port: [(None, None, None, "", ("10.0.0.5", 0))]
        )
        asked = []
        client = _client(lambda request: asked.append(request) or httpx.Response(200))
        with pytest.raises(ValueError, match="не на публичный"):
            user_added.from_url("http://db.internal/", client)
        assert asked == []

    def test_a_redirect_into_the_private_network_is_refused(self, monkeypatch):
        def resolve(host, port):
            ip = "127.0.0.1" if host == "localhost" else "93.184.216.34"
            return [(None, None, None, "", (ip, 0))]

        monkeypatch.setattr(socket, "getaddrinfo", resolve)
        asked = []

        def handler(request):
            asked.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://localhost:5432/"})

        with pytest.raises(ValueError, match="не на публичный"):
            user_added.from_url("https://jobs.example.com/a/1", _client(handler))
        assert asked == ["https://jobs.example.com/a/1"]

    def test_an_odd_port_is_refused(self, public_dns):
        with pytest.raises(ValueError, match="не на публичный"):
            user_added.from_url("https://jobs.example.com:8090/a", _client(lambda r: None))

    def test_a_telegram_link_asks_for_the_text(self):
        with pytest.raises(ValueError, match="текст поста"):
            user_added.from_url("https://t.me/somechannel/123")
