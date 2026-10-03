"""Employers' own career sites and ATS feeds.

Fixtures are live answers saved 2026-09-23, cut to two items each. The network
is a MockTransport routed by host; no request leaves the test.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from core.filters import PreScreen, SearchProfile
from core.sources import employers
from core.sources.employers import Board
from core.sources.habr_api import Posting

FIXTURES = Path(__file__).parent / "fixtures" / "employers"


def raw(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def js(name: str):
    return json.loads(raw(name))


BOARDS = [
    Board("sber", "Сбер"),
    Board("yandex", "Яндекс"),
    Board("vk", "VK"),
    Board("huntflow", "Lime", "limehr"),
    Board("greenhouse", "Wrike", "wrike", group="global"),
    Board("lever", "Xsolla", "xsolla", group="global"),
    Board("ashby", "Gismart", "gismart", group="global"),
]


def router(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "rabota.sber.ru":
        # The second page is empty: the list ends there.
        skip = int(request.url.params.get("skip", "0"))
        data = js("sber_list.json") if skip == 0 else {"data": {"vacancies": []}}
        return httpx.Response(200, json=data)
    if host == "yandex.ru" and path == "/jobs/api/publications":
        data = js("yandex_list.json")
        data["next"] = None
        return httpx.Response(200, json=data)
    if host == "yandex.ru" and path.startswith("/jobs/api/publications/"):
        return httpx.Response(200, json=js("yandex_detail.json"))
    if host == "team.vk.company" and path.startswith("/career/api/"):
        return httpx.Response(200, json={**js("vk_list.json"), "next": None})
    if host == "team.vk.company" and path.startswith("/vacancy/"):
        return httpx.Response(200, text=raw("vk_vacancy.html"))
    if host == "limehr.huntflow.io" and path == "/api/vacancy":
        return httpx.Response(200, json={**js("huntflow_list.json"), "total": 1})
    if host == "limehr.huntflow.io":
        return httpx.Response(200, json=js("huntflow_detail.json"))
    if host == "boards-api.greenhouse.io":
        return httpx.Response(200, json=js("greenhouse_list.json"))
    if host == "api.lever.co":
        return httpx.Response(200, json=js("lever_list.json"))
    if host == "api.ashbyhq.com":
        return httpx.Response(200, json=js("ashby_list.json"))
    return httpx.Response(404)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(employers, "DELAY_SECONDS", 0)
    return httpx.Client(transport=httpx.MockTransport(router))


class TestCollect:
    def test_every_board_contributes(self, client):
        out = employers.collect(["python"], ("ru", "global"), BOARDS, client)
        kinds = {p.id.split("/", 1)[0] for p in out.postings}
        assert kinds == {"sber", "yandex", "vk", "huntflow", "greenhouse", "lever", "ashby"}
        assert out.problems == []

    def test_groups_choose_the_boards(self, client):
        out = employers.collect(["python"], ("ru",), BOARDS, client)
        kinds = {p.id.split("/", 1)[0] for p in out.postings}
        assert kinds == {"sber", "yandex", "vk", "huntflow"}

    def test_boards_that_list_their_text_keep_it(self, client):
        out = employers.collect(["python"], ("ru", "global"), BOARDS, client)
        with_text = {p.id.split("/", 1)[0] for p in out.postings if p.id in out.texts}
        assert with_text == {"sber", "greenhouse", "lever", "ashby"}

    def test_a_failing_board_is_a_problem_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(employers, "DELAY_SECONDS", 0)

        def handler(request):
            if request.url.host == "api.lever.co":
                return httpx.Response(503)
            return router(request)

        http = httpx.Client(transport=httpx.MockTransport(handler))
        out = employers.collect(["python"], ("global",), BOARDS, http)
        assert [p["board"] for p in out.problems] == ["Xsolla"]
        assert any(p.id.startswith("greenhouse/") for p in out.postings)

    def test_board_accreditation_reaches_every_posting(self, client):
        boards = [Board("vk", "VK", accredited=True), Board("sber", "Сбер")]
        out = employers.collect(["python"], ("ru",), boards, client)
        by_kind = {p.id.split("/", 1)[0]: p.accredited for p in out.postings}
        assert by_kind == {"vk": True, "sber": None}

    def test_several_queries_do_not_duplicate_a_vacancy(self, client):
        out = employers.collect(["python", "backend"], ("ru",), BOARDS[:1], client)
        ids = [p.id for p in out.postings]
        assert len(ids) == len(set(ids)) == 2


class TestCompleteFeeds:
    """Which boards were read whole — the only ones whose silence means "gone"."""

    def test_feeds_are_complete_searchable_boards_are_not(self, client):
        out = employers.collect(["python"], ("ru", "global"), BOARDS, client)
        assert sorted(out.complete) == [
            "ashby/gismart", "greenhouse/wrike", "huntflow/limehr", "lever/xsolla",
        ]

    def test_a_failed_feed_is_not_complete(self, monkeypatch):
        monkeypatch.setattr(employers, "DELAY_SECONDS", 0)

        def handler(request):
            if request.url.host == "api.lever.co":
                return httpx.Response(503)
            return router(request)

        http = httpx.Client(transport=httpx.MockTransport(handler))
        out = employers.collect(["python"], ("global",), BOARDS, http)
        assert "lever/xsolla" not in out.complete

    def test_an_empty_feed_is_not_complete(self, monkeypatch):
        """A feed that suddenly lists nothing is likelier a broken API."""
        monkeypatch.setattr(employers, "DELAY_SECONDS", 0)

        def handler(request):
            if request.url.host == "boards-api.greenhouse.io":
                return httpx.Response(200, json={"jobs": []})
            return router(request)

        http = httpx.Client(transport=httpx.MockTransport(handler))
        out = employers.collect(["python"], ("global",), BOARDS, http)
        assert "greenhouse/wrike" not in out.complete

    def test_a_feed_cut_by_the_page_cap_is_not_complete(self, monkeypatch):
        monkeypatch.setattr(employers, "DELAY_SECONDS", 0)
        monkeypatch.setattr(employers, "HUNTFLOW_MAX_PAGES", 1)

        def handler(request):
            if request.url.host == "limehr.huntflow.io" and request.url.path == "/api/vacancy":
                return httpx.Response(200, json={**js("huntflow_list.json"), "total": 3})
            return router(request)

        http = httpx.Client(transport=httpx.MockTransport(handler))
        out = employers.collect(["python"], ("ru",), BOARDS, http)
        assert "huntflow/limehr" not in out.complete
        assert out.truncated == ["huntflow/limehr"]

    def test_board_key_and_feed_postings(self):
        assert employers.board_key("greenhouse/wrike/123") == "greenhouse/wrike"
        assert employers.is_feed_posting("lever/xsolla/abc-1")
        assert not employers.is_feed_posting("sber/sber/77")


class TestAdapters:
    def test_sber_text_has_the_sections(self):
        posting, text = employers.sber_posting(
            Board("sber", "Сбер"), js("sber_list.json")["data"]["vacancies"][0]
        )
        assert posting.url.startswith("https://rabota.sber.ru/search/")
        assert "Требования:" in text and "Обязанности:" in text
        assert posting.published == "2026-09-15"

    def test_sber_states_no_work_mode(self):
        """No work-mode field: None unless the text says remote."""
        posting, _ = employers.sber_posting(
            Board("sber", "Сбер"), {"internalId": 1, "title": "Бухгалтер", "conditions": "офис"}
        )
        assert posting.remote is None
        remote, _ = employers.sber_posting(
            Board("sber", "Сбер"),
            {"internalId": 2, "title": "Бухгалтер", "conditions": "Удалённая работа"},
        )
        assert remote.remote is True

    def test_yandex_next_link_is_reduced_to_its_cursor(self):
        """`next` points at an internal host; only the cursor may be reused."""
        assert employers._cursor(
            "http://femida.yandex-team.ru/_api/jobs/publications/?cursor=bz0xOSZwPTM%3D&x=1"
        ) == "bz0xOSZwPTM%3D"
        assert employers._cursor(None) is None

    def test_yandex_work_modes(self):
        assert employers._yandex_remote({"work_modes": [{"slug": "remote"}]}) is True
        assert employers._yandex_remote({"work_modes": [{"slug": "mixed"}]}) is False
        assert employers._yandex_remote({}) is None

    def test_vk_page_text_stops_before_other_vacancies(self):
        text = employers.vk_page_text(raw("vk_vacancy.html"))
        assert "Требования" in text
        assert "Похожие вакансии" not in text
        assert "Откликнуться" not in text

    def test_greenhouse_content_is_unescaped_prose(self):
        posting, text = employers.greenhouse_posting(
            Board("greenhouse", "Wrike", "wrike"), js("greenhouse_list.json")["jobs"][0]
        )
        assert posting.url.startswith("https://job-boards.greenhouse.io/wrike/")
        assert "Wrike is" in text
        assert "&lt;" not in text and "<div" not in text

    def test_lever_workplace(self):
        posting, _ = employers.lever_posting(
            Board("lever", "Xsolla", "xsolla"), js("lever_list.json")[0]
        )
        assert posting.remote is False  # "onsite"
        assert posting.published

    def test_ashby_remote_from_a_secondary_location(self):
        posting, _ = employers.ashby_posting(
            Board("ashby", "Gismart", "gismart"), js("ashby_list.json")["jobs"][0]
        )
        assert posting.remote is True

    def test_a_slug_cannot_redirect_the_fetcher(self):
        """The slug goes into a hostname; the data file must not aim it elsewhere."""
        with pytest.raises(ValueError):
            employers._huntflow_base(Board("huntflow", "x", "evil.example/"))
        with pytest.raises(ValueError):
            employers._ats_slug(Board("lever", "x", "../../admin"))


class TestDescribe:
    def test_only_boards_without_listed_text_are_asked(self, client):
        out = employers.collect(["python"], ("ru", "global"), BOARDS, client)
        described = employers.describe(out.postings, BOARDS, client)
        kinds = {pid.split("/", 1)[0] for pid in described}
        assert kinds == {"yandex", "vk", "huntflow"}
        assert all(text for text in described.values())

    def test_the_cap_holds(self, client):
        out = employers.collect(["python"], ("ru",), BOARDS, client)
        assert len(employers.describe(out.postings, BOARDS, client, limit=1)) == 1

    def test_a_page_that_fails_is_skipped(self, monkeypatch):
        monkeypatch.setattr(employers, "DELAY_SECONDS", 0)
        http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        p = Posting(id="yandex/yandex/1", url="", title="x", company="Яндекс")
        assert employers.describe([p], BOARDS, http) == {}


class TestUnknownRemote:
    def test_only_remote_keeps_a_board_that_does_not_say(self):
        """Silence about the work mode is not an office job."""
        profile = SearchProfile(positive=["бухгалтер"], remote_only=True)
        screen = PreScreen(profile)
        silent = Posting(id="1", url="", title="Бухгалтер", company="Сбер", remote=None)
        office = Posting(id="2", url="", title="Бухгалтер", company="Сбер", remote=False)
        assert screen.check(silent).keep is True
        assert screen.check(office).keep is False


class TestTheShippedList:
    def test_every_board_has_a_known_kind_and_group(self):
        boards = employers.load()
        assert boards, "data/sources/employers.json пуст или отсутствует"
        for board in boards:
            assert board.kind in employers.FETCHERS, board
            assert board.group in ("ru", "global"), board
            # False would drop a whole board under "only accredited", and a
            # missing Habr badge is no evidence of a missing accreditation.
            assert board.accredited in (True, None), board
