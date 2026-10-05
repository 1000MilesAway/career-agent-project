"""Is a vacancy still open? The rule: only positive evidence closes it.

Fixtures are real pages saved 2026-09-23. No network here.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from core import liveness

FIXTURES = Path(__file__).parent / "fixtures"
HABR = "https://career.habr.com/vacancies/1000168684"
GEEKJOB = "https://geekjob.ru/vacancy/6ab2b32973abaf8df70aa37d"
TG = "https://t.me/runello_rus_python/1803"


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
