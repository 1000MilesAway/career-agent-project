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

    def test_telegram_cannot_be_asked(self):
        assert liveness.check("https://t.me/pythonrabota/2340").state == "unknown"
        assert liveness.check(None).state == "unknown"


class TestCheck:
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
