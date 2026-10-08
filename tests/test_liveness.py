"""Is a vacancy still open? The rule: only positive evidence closes it.

Fixtures are real pages saved 2026-09-23 (and 2026-10-08 for the company
boards). No network here. The company pages are trimmed of inline scripts,
styles and images — the JSON-LD and the `.content` node `classify` reads
are kept whole.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from core import liveness

FIXTURES = Path(__file__).parent / "fixtures"
HABR = "https://career.habr.com/vacancies/1000168684"
GEEKJOB = "https://geekjob.ru/vacancy/6ab2b32973abaf8df70aa37d"
TG = "https://t.me/runello_rus_python/1803"
SBER = "https://rabota.sber.ru/search/4566142/"
SBER_SLUG = "https://rabota.sber.ru/search/razrabotchik-s-pangolin-db-4566142/"
YANDEX = "https://yandex.ru/jobs/vacancies/multitrack-_-noviy-format-nayma-dlya-opitnih-bekenderov-15322"
VK = "https://team.vk.company/vacancy/54546/"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestClassify:
    def test_a_live_habr_page_is_open(self):
        assert liveness.classify(HABR, 200, fixture("habr_open.html")).state == "open"

    def test_habr_archive_answered_with_200_is_closed(self):
        """Some archived Habr vacancies answer 200 with a stub, not 410."""
        verdict = liveness.classify(HABR, 200, fixture("habr_archived.html"))
        assert verdict.closed
        assert "архив" in verdict.reason

    def test_gone_is_closed(self):
        assert liveness.classify(HABR, 410, "").closed
        assert liveness.classify(GEEKJOB, 404, "").closed

    def test_a_live_geekjob_page_is_open(self):
        assert liveness.classify(GEEKJOB, 200, fixture("geekjob_vacancy.html")).state == "open"

    def test_geekjob_archive_banner_is_closed(self):
        """geekjob keeps the old JSON-LD on archived pages; the banner is the signal."""
        verdict = liveness.classify(GEEKJOB, 200, fixture("geekjob_archived.html"))
        assert verdict.closed

    def test_server_trouble_is_not_evidence(self):
        """A false "closed" hides a real job; 5xx and 429 say nothing about it."""
        for status in (429, 500, 503):
            assert liveness.classify(HABR, status, "").state == "unknown"

    def test_an_unrecognised_page_is_not_closed(self):
        assert liveness.classify(GEEKJOB, 200, "<html>редизайн</html>").state == "unknown"

    def test_a_deleted_telegram_post_is_closed(self):
        """t.me answers 200 for a deleted post; the widget's error is the signal."""
        url = TG + "?embed=1&mode=tme"
        verdict = liveness.classify(url, 200, fixture("telegram_post_deleted.html"))
        assert verdict.closed
        assert "удалён" in verdict.reason

    def test_a_live_telegram_post_is_open(self):
        url = "https://t.me/runello_rus_python/1818?embed=1&mode=tme"
        verdict = liveness.classify(url, 200, fixture("telegram_post_open.html"))
        assert verdict.state == "open"
        # The post's date travels: the age mark needs it (services/liveness.py).
        assert verdict.published == "2026-09-28T05:00:36+00:00"

    def test_a_missing_channel_is_not_a_deleted_post(self):
        """A renamed channel says nothing about the post; closing it would be a guess."""
        url = "https://t.me/nonexistent_chan_zzq123/5?embed=1&mode=tme"
        verdict = liveness.classify(url, 200, fixture("telegram_post_no_channel.html"))
        assert verdict.state == "unknown"

    def test_only_a_single_post_is_checkable(self):
        assert liveness.can_check(TG)
        assert not liveness.can_check("https://t.me/s/runello_rus_python")
        assert not liveness.can_check("https://t.me/runello_rus_python")
        assert liveness.check(None).state == "unknown"


class TestCompanyPages:
    """Pages of the searchable employer boards, verified live 2026-10-08."""

    def test_a_live_company_page_is_open(self):
        assert liveness.classify(SBER_SLUG, 200, fixture("sber_open.html")).state == "open"
        assert liveness.classify(YANDEX, 200, fixture("yandex_open.html")).state == "open"
        assert liveness.classify(VK, 200, fixture("vk_open.html")).state == "open"

    def test_gone_is_closed_for_company_pages(self):
        """All three answer 404 for an id the board does not know."""
        assert liveness.classify(SBER, 404, "").closed
        assert liveness.classify(YANDEX, 404, "").closed
        assert liveness.classify(VK, 404, "").closed

    def test_yandex_expired_valid_through_is_closed(self):
        verdict = liveness.classify(YANDEX, 200, fixture("yandex_expired.html"))
        assert verdict.closed
        assert "срок публикации истёк" in verdict.reason

    def test_a_recognised_200_without_a_posting_is_not_evidence(self):
        """An archived Yandex/VK page may answer 200; without the structure
        the live pages carry, that says nothing."""
        assert liveness.classify(YANDEX, 200, "<html>редизайн</html>").state == "unknown"
        assert liveness.classify(VK, 200, "<html>редизайн</html>").state == "unknown"

    def test_sber_page_is_open_even_without_structure(self):
        """Sber carries no JSON-LD and no banner; the URL came from the
        board's own API, so a 200 is the page existing."""
        assert liveness.classify(SBER_SLUG, 200, "<html></html>").state == "open"

    def test_server_trouble_is_not_evidence_for_company_pages(self):
        for status in (429, 500, 503):
            assert liveness.classify(SBER, status, "").state == "unknown"

    def test_only_our_company_urls_are_checkable(self):
        assert liveness.can_check(SBER)
        assert liveness.can_check(SBER_SLUG)
        assert liveness.can_check(YANDEX)
        assert liveness.can_check(VK)
        # ATS feeds close by absence from the feed, not by their page.
        assert not liveness.can_check("https://boards.greenhouse.io/acme/jobs/4001")
        assert not liveness.can_check("https://acme.huntflow.io/vacancy/backend")
        # A URL we never built: the sweep must not fetch it.
        assert not liveness.can_check("https://example.com/vacancy/1")

    def test_sber_is_asked_through_its_redirect(self):
        """The bare id URL 307s to the slug page; the slug is still ours."""
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.url.path == "/search/4566142/":
                return httpx.Response(307, headers={"location": SBER_SLUG})
            return httpx.Response(200, text=fixture("sber_open.html"))

        http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        assert liveness.check(SBER, http=http).state == "open"
        assert seen == [SBER, SBER_SLUG]


class TestCheck:
    def test_a_telegram_post_is_asked_through_its_widget(self):
        """The bare post URL is a stub that looks alike for live and deleted posts."""
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text=fixture("telegram_post_deleted.html"))

        http = httpx.Client(transport=httpx.MockTransport(handler))
        assert liveness.check(TG, http=http).closed
        assert seen == [TG + "?embed=1&mode=tme"]

    def test_network_failure_is_unknown_not_an_exception(self):
        def boom(request):
            raise httpx.ConnectError("нет сети")

        http = httpx.Client(transport=httpx.MockTransport(boom))
        assert liveness.check(HABR, http=http).state == "unknown"

    def test_a_redirect_off_the_source_is_not_the_vacancy(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "career.habr.com":
                return httpx.Response(302, headers={"location": "https://example.com/"})
            return httpx.Response(404)

        http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        assert liveness.check(HABR, http=http).state == "unknown"
