"""The shared Telegram collect+screen step.

It exists so the CLI and the web screen cannot drift apart. The tests here
pin the two things that would make them drift silently: what a post turns
into before the filters see it, and what happens with no search profile.

No network, no LLM — search_many is stubbed.
"""

import pytest

from core import tg_scan
from core.sources import telegram


def post(pid: str, title: str = "Python разработчик", **kw) -> telegram.Post:
    base = {
        "id": pid,
        "url": f"https://t.me/chan/{pid}",
        "channel": "chan",
        "title": title,
        "text": f"{title}\nЗарплата 200000. Пишите @hr",
        "grade": "Middle",
        "stack": ["Python"],
        "tags": [],
        "remote": True,
        "has_salary": True,
        "published": "2026-09-15T10:00:00+00:00",
    }
    return telegram.Post(**{**base, **kw})


@pytest.fixture
def no_network(monkeypatch):
    """search_many returns whatever the test set, without touching t.me."""
    def _set(posts, problems=()):
        monkeypatch.setattr(
            telegram, "search_many", lambda *a, **k: (list(posts), list(problems))
        )
    return _set


class TestAsPosting:
    def test_channel_becomes_the_company(self):
        assert tg_scan.as_posting(post("1")).company == "@chan"

    def test_divisions_stay_empty_so_the_specialization_check_cannot_fire(self):
        """Telegram states no specialization. Guessing one would drop real jobs."""
        assert not tg_scan.as_posting(post("1")).divisions

    def test_grade_and_stack_carry_over(self):
        p = tg_scan.as_posting(post("1"))
        assert p.qualification == "Middle"
        assert p.skills == ["Python"]


class TestCollect:
    def test_without_a_profile_the_starter_template_screens(self, no_network):
        """An unconfigured run must not dump raw posts into the found list.

        Telegram survivors share that list with Habr's, so passing everything
        through fills the screen with posts nobody asked for and no reason
        attached — the Habr bug of 2026-09-19 by another route.
        """
        no_network([post("1"), post("2", title="SMM-менеджер")])
        r = tg_scan.collect(channels=["chan"], search_profile=None)
        assert r["filtered"] is True
        assert r["filtered_by"] == "starter"
        assert [p.id for p, _ in r["kept"]] == ["1"]
        assert [p.id for p, _ in r["dropped"]] == ["2"]

    def test_with_a_profile_the_free_filters_run(self, no_network):
        no_network([post("1"), post("2", title="Бариста в кофейню")])
        r = tg_scan.collect(
            channels=["chan"],
            search_profile={"queries": ["python"], "positive": ["разработчик"]},
        )
        assert r["filtered"] is True
        assert [p.id for p, _ in r["kept"]] == ["1"]
        assert [p.id for p, _ in r["dropped"]] == ["2"]

    def test_every_dropped_post_carries_a_reason(self, no_network):
        """The screen stays auditable: nothing disappears unexplained."""
        no_network([post("2", title="Бариста в кофейню")])
        r = tg_scan.collect(
            channels=["chan"],
            search_profile={"queries": ["python"], "positive": ["разработчик"]},
        )
        assert all(why for _p, why in r["dropped"])

    def test_problems_are_reported_not_swallowed(self, no_network):
        """A silent channel answers 200 with old posts — it must be named."""
        no_network([post("1")], [("dead", "@dead: молчит с 2026-01-01 — пропускаю")])
        r = tg_scan.collect(channels=["chan", "dead"])
        assert r["problems"][0]["channel"] == "dead"

    def test_empty_channel_list_is_not_a_crash(self, no_network):
        r = tg_scan.collect(channels=[], groups=("nope",))
        assert r["collected"] == 0


class TestRows:
    def test_row_carries_the_post_text(self):
        """t.me has no page to fetch later: the text collected here is the vacancy."""
        rows = tg_scan.as_rows([(post("1"), "")])
        assert "Пишите @hr" in rows[0]["text"]

    def test_source_is_marked(self):
        assert tg_scan.as_rows([(post("1"), "")])[0]["source"] == "telegram"

    def test_missing_salary_says_so_instead_of_faking_one(self):
        rows = tg_scan.as_rows([(post("1", has_salary=False), "")])
        assert rows[0]["salary_text"] == ""

    def test_drop_reasons_are_counted_by_kind(self):
        counted = tg_scan.drop_reasons([
            (post("1"), "стоп-слово в заголовке: бариста"),
            (post("2"), "стоп-слово в заголовке: повар"),
            (post("3"), "грейд ниже минимального"),
        ])
        assert counted["стоп-слово в заголовке"] == 2
        assert counted["грейд ниже минимального"] == 1


class TestSalaryReachesTheFilter:
    """The point of parsing a band out of prose: before this, salary_min was
    inert on Telegram — no post carried salary_from, so every one passed."""

    def test_a_rouble_band_is_handed_to_prescreen(self):
        p = tg_scan.as_posting(
            post("1", salary_from=180000, salary_to=250000, salary_currency="RUB")
        )
        assert (p.salary_from, p.salary_to) == (180000, 250000)

    def test_a_low_band_is_now_actually_dropped(self):
        from core.filters import PreScreen, SearchProfile

        screen = PreScreen(SearchProfile.from_dict({"salary_min": 200000}))
        low = tg_scan.as_posting(
            post("1", salary_from=120000, salary_to=120000, salary_currency="RUB")
        )
        decision = screen.check(low)
        assert not decision.keep
        # The invariant: a rejection always says why, in a form the user reads.
        assert "120 000 ₽" in decision.reason

    def test_a_foreign_band_does_not_reach_a_rouble_threshold(self):
        """salary_min is a bare number with no currency, so handing it 4500 for
        a "$4500/мес" posting would read as 4500 ₽ and drop a well-paid job."""
        from core.filters import PreScreen, SearchProfile

        screen = PreScreen(SearchProfile.from_dict({"salary_min": 200000}))
        usd = tg_scan.as_posting(
            post("1", salary_from=4500, salary_to=4500, salary_currency="USD")
        )
        assert usd.salary_from is None
        assert screen.check(usd).keep

    def test_the_row_shows_the_band_not_just_that_one_exists(self):
        rows = tg_scan.as_rows(
            [(post("1", salary_from=180000, salary_to=250000, salary_currency="RUB"), "")]
        )
        assert rows[0]["salary_text"] == "180 000 — 250 000 ₽"

    def test_a_mention_without_a_figure_still_says_so(self):
        """"зп обсуждается" has no band but is not silence either."""
        rows = tg_scan.as_rows([(post("1", has_salary=True), "")])
        assert rows[0]["salary_text"] == "зарплата указана"

    def test_the_apply_link_travels_to_the_row(self):
        rows = tg_scan.as_rows([(post("1", apply_url="https://acme.ru/jobs/dev-42"), "")])
        assert rows[0]["apply_url"] == "https://acme.ru/jobs/dev-42"


class TestEnrichment:
    """Linked descriptions cost no money, but they do cost someone else's bandwidth."""

    @pytest.fixture
    def spy(self, monkeypatch):
        """Record which posts were handed to the fetcher, and fill them in."""
        seen: list[list[str]] = []

        def fake(posts, limit=None, client=None):
            targets = [p for p in posts if p.description_url and not p.description_text]
            seen.append([p.id for p in targets])
            for p in targets:
                p.description_text = "полное описание вакансии"
            return len(targets)

        monkeypatch.setattr(telegram, "fetch_externals", fake)
        return seen

    def test_only_survivors_are_fetched(self, no_network, spy):
        """A dropped post is never evaluated: fetching its page is pure waste."""
        no_network([
            post("1", description_url="https://telegra.ph/a"),
            post("2", title="Бариста в кофейню", description_url="https://telegra.ph/b"),
        ])
        r = tg_scan.collect(
            channels=["chan"],
            search_profile={"queries": ["python"], "positive": ["разработчик"]},
        )
        assert [p.id for p, _ in r["kept"]] == ["1"]
        assert spy == [["1"]]

    def test_enrich_can_be_turned_off(self, no_network, spy):
        no_network([post("1", description_url="https://telegra.ph/a")])
        r = tg_scan.collect(channels=["chan"], enrich=False)
        assert spy == []
        assert r["enriched"] == 0

    def test_the_count_is_reported(self, no_network, spy):
        no_network([post("1", description_url="https://telegra.ph/a")])
        assert tg_scan.collect(channels=["chan"])["enriched"] == 1

    def test_the_description_reaches_the_row_text(self, no_network, spy):
        """The evaluation reads the row text: a description stopping here bought nothing."""
        no_network([post("1", description_url="https://telegra.ph/a")])
        rows = tg_scan.as_rows(tg_scan.collect(channels=["chan"])["kept"])
        assert "полное описание вакансии" in rows[0]["text"]
        assert rows[0]["description_url"] == "https://telegra.ph/a"

    def test_the_row_url_still_points_at_the_post(self, no_network, spy):
        """The permalink is the dedup key and what the user opens — not a swap."""
        no_network([post("1", description_url="https://telegra.ph/a")])
        rows = tg_scan.as_rows(tg_scan.collect(channels=["chan"])["kept"])
        assert rows[0]["url"] == "https://t.me/chan/1"

    def test_a_post_with_no_description_says_so_plainly(self, no_network, spy):
        no_network([post("1")])
        rows = tg_scan.as_rows(tg_scan.collect(channels=["chan"])["kept"])
        assert rows[0]["description_url"] == ""
