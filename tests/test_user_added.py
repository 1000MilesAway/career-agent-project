"""Which links a user may bring, decided before any request.

No network: a link to a site we do not read on demand is refused before its
host is even resolved.
"""

from __future__ import annotations

import socket

import pytest

from core.sources import user_added


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a link to another site must not be resolved")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)


class TestLink:
    def test_a_lone_url_is_a_link(self):
        assert user_added.is_link("  https://example.com/jobs/1 \n")

    def test_a_text_with_a_link_inside_is_not(self):
        assert not user_added.is_link("Вакансия: https://example.com/jobs/1 откликайтесь")

    def test_text_is_refused(self, no_network):
        with pytest.raises(ValueError, match="проверенных ресурсов") as e:
            user_added.from_url("Senior Python-разработчик, удалённо, от 300 000 ₽")
        assert not isinstance(e.value, user_added.OtherSite)

    @pytest.mark.parametrize(
        "url",
        [
            "https://jobs.example.com/a/1",
            "http://db.internal/",
            "http://localhost:5432/",
            "http://169.254.169.254/latest/meta-data/",
        ],
    )
    def test_another_site_is_refused_without_a_request(self, no_network, url):
        """The server must not fetch whatever address a user names; the caller
        may still find the link among pooled employer postings."""
        with pytest.raises(user_added.OtherSite, match="проверенных ресурсов"):
            user_added.from_url(url)

    def test_a_telegram_link_is_refused_outright(self, no_network):
        """A post can say anything: not even looked up in the pool."""
        with pytest.raises(ValueError, match="проверенных ресурсов") as e:
            user_added.from_url("https://t.me/somechannel/123")
        assert not isinstance(e.value, user_added.OtherSite)

    def test_the_stored_spellings_of_a_link(self):
        assert user_added.url_variants("https://a.ru/v/1/#x") == [
            "https://a.ru/v/1/",
            "https://a.ru/v/1",
        ]
