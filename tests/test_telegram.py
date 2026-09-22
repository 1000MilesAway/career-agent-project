"""Telegram posts are flat text, so the cheap checks that rescue structure are
the ones worth pinning down.

No network: the page comes from tests/fixtures/tg_channel.html, hand-written
synthetic markup. Live LLM calls are banned by CLAUDE.md, and a live t.me fetch
would be just as flaky — the channel would answer differently tomorrow.
"""

from pathlib import Path

import httpx
import pytest
from probe.sources.telegram import (
    HTMLParser,
    Post,
    _channel_is_silent,
    _first_title_line,
    _parse_post,
    _readable_text,
    fetch_external,
    fetch_externals,
    is_description_link,
    looks_like_vacancy,
)

from probe.sources import telegram, tg_channels

FIXTURE = Path(__file__).parent / "fixtures" / "tg_channel.html"


def parse_fixture() -> list[Post]:
    tree = HTMLParser(FIXTURE.read_text(encoding="utf-8"))
    out = []
    for node in tree.css(".tgme_widget_message_wrap"):
        post = _parse_post(node, "jobchannel")
        if post is not None:
            out.append(post)
    return out


def post(text: str, **kw) -> Post:
    base = dict(id="1", url="https://t.me/jobchannel/1", channel="jobchannel", text=text)
    base.update(kw)
    return Post(**base)


def _mock_client(handler) -> httpx.Client:
    """An httpx client answered by `handler` instead of the network.

    MockTransport ships with httpx, so pinning the fetching down costs no new
    dependency — and CLAUDE.md forbids adding one for a test.
    """
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.fixture
def no_sleep(monkeypatch):
    """Drop the politeness delay: the tests talk to MockTransport, not to a host."""
    monkeypatch.setattr(telegram.time, "sleep", lambda _s: None)


class TestParsing:
    def test_reads_every_post_on_the_page(self):
        assert len(parse_fixture()) == 9

    def test_keeps_line_breaks(self):
        """<br> carries the layout: without it "Грейд: Middle+" merges into the next field."""
        first = parse_fixture()[0]
        assert "Golang разработчик" in first.text.split("\n")[0]
        assert "Грейд: Middle+" in first.text

    def test_reads_id_url_and_date(self):
        first = parse_fixture()[0]
        assert first.id == "101"
        assert first.url == "https://t.me/jobchannel/101"
        assert first.published.startswith("2099-09-16")

    def test_reads_the_runello_template_fields(self):
        """"Грейд:"/"Стек:" are the only real structure Telegram ever offers."""
        first = parse_fixture()[0]
        assert first.grade == "Middle+"
        assert first.stack == ["Go", "PostgreSQL", "Kafka", "Redis", "REST API"]

    def test_absent_template_fields_stay_empty(self):
        tagged = parse_fixture()[1]
        assert tagged.grade == ""
        assert tagged.stack == []

    def test_reads_tags_and_flags(self):
        tagged = parse_fixture()[1]
        assert "python" in tagged.tags
        assert tagged.remote is True
        assert tagged.has_salary is True


class TestTitleLine:
    def test_takes_the_first_real_line(self):
        assert _first_title_line("Golang разработчик\nГрейд: Middle") == "Golang разработчик"

    def test_skips_a_line_that_is_only_hashtags(self):
        text = "#вакансия #python #удалёнка\nPython-разработчик (FastAPI)"
        assert _first_title_line(text) == "Python-разработчик (FastAPI)"

    def test_keeps_a_line_that_mixes_tags_and_words(self):
        """"#golang Senior Go Engineer" is a title; only the tags are stripped."""
        assert _first_title_line("#golang Senior Go Engineer") == "Senior Go Engineer"

    def test_skips_a_lone_emoji_line(self):
        """godevjob opens with "🔥" — taking it literally titled every vacancy "🔥"."""
        assert _first_title_line("🔥\nGo Developer\nот 150 000 ₽") == "Go Developer"

    def test_keeps_an_emoji_that_sits_next_to_words(self):
        assert _first_title_line("🔥 Go Developer") == "🔥 Go Developer"

    def test_empty_text_gives_empty_title(self):
        assert _first_title_line("\n  \n") == ""

    def test_decoration_only_text_gives_empty_title(self):
        assert _first_title_line("✨ — ✨\n···") == ""


class TestLooksLikeVacancy:
    def test_accepts_an_employer_ad(self):
        ok, why = looks_like_vacancy(parse_fixture()[0])
        assert ok, why

    def test_rejects_a_candidate_cv(self):
        """django_jobs_board is half these, and they must never be scored as jobs."""
        ok, why = looks_like_vacancy(parse_fixture()[2])
        assert not ok
        assert "резюме" in why

    def test_cv_marker_beats_hiring_words(self):
        """A CV saying "ищу работу" also says "рассматриваю предложения" — the CV wins."""
        text = (
            "#cv #резюме Python Backend Developer. Ищу работу в продуктовой команде, "
            "требуется удалёнка. Опыт пять лет: Python, FastAPI, PostgreSQL, Docker, CI. "
            "Готов к интервью, рассматриваю предложения. Связь: @kandidat"
        )
        ok, why = looks_like_vacancy(post(text))
        assert not ok
        assert "резюме" in why

    def test_rejects_a_course_ad(self):
        ok, why = looks_like_vacancy(parse_fixture()[3])
        assert not ok
        assert "реклама" in why

    def test_rejects_short_offtopic_with_a_job_word(self):
        """"День программиста" passed every keyword filter in the 2026-09-17 survey."""
        ok, why = looks_like_vacancy(parse_fixture()[4])
        assert not ok
        assert "короткий" in why

    def test_rejects_a_vacancy_with_no_way_to_apply(self):
        ok, why = looks_like_vacancy(parse_fixture()[6])
        assert not ok
        assert "контакт" in why

    def test_salary_is_not_required(self):
        """Demanding a salary dropped 6 of 9 real vacancies on getitrussia."""
        text = (
            "Ищем Go-разработчика в команду платежей. Задачи: развивать сервисы, "
            "проектировать API, участвовать в ревью. Требования: Go от двух лет, "
            "PostgreSQL, понимание очередей. Зарплату обсуждаем на интервью. "
            "Отклик: @recruiter"
        )
        ok, why = looks_like_vacancy(post(text))
        assert ok, why

    def test_perk_wording_does_not_make_a_vacancy_an_ad(self):
        """A real job ad may mention "обучение за счёт компании" — promo must not win."""
        text = (
            "Вакансия: Python-разработчик в команду отчётности. Ищем инженера на "
            "FastAPI и PostgreSQL, удалённо, оформление по ТК. Задачи: развивать "
            "сервис выгрузок, покрывать код тестами, участвовать в проектировании "
            "схемы данных. Что предлагаем: ДМС, гибкий график, обучение за счёт "
            "компании и оплату профильных курсов. Писать: @hr_primer"
        )
        ok, why = looks_like_vacancy(post(text))
        assert ok, why


class TestSilence:
    def test_a_channel_posting_today_is_alive(self):
        assert not _channel_is_silent(parse_fixture(), max_days=30)

    def test_a_long_silent_channel_is_dead(self):
        """golangjob answers 200 with 20 posts and looks healthy — dated 2023-02."""
        stale = [post("...", published="2023-02-10T08:00:00+00:00")]
        assert _channel_is_silent(stale, max_days=30)

    def test_undated_posts_do_not_count_as_dead(self):
        assert not _channel_is_silent([post("...")], max_days=30)


class TestChannelList:
    def test_core_names_are_loaded(self):
        names = tg_channels.channel_names()
        assert "godevjob" in names
        assert len(names) > 5

    def test_adjacent_is_opt_in(self):
        core = tg_channels.channel_names()
        both = tg_channels.channel_names(groups=("core", "adjacent"))
        assert "mobiledevjob" not in core
        assert "mobiledevjob" in both

    def test_names_do_not_repeat_across_groups(self):
        names = tg_channels.channel_names(groups=("core", "adjacent", "special"))
        assert len(names) == len(set(names))

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert tg_channels.channel_names(path=tmp_path / "nope.json") == []

    def test_unreachable_chats_are_recorded(self):
        """The list of groups exists so the same dead ends are not retried monthly."""
        data = tg_channels.load()
        assert "hr_breakfast_emergency" in data["chats_unreachable"]["names"]


class TestDedup:
    def test_same_text_under_two_ids_is_one_vacancy(self):
        """getitrussia served 20 posts holding 9 distinct texts."""
        posts = parse_fixture()
        tagged, repost = posts[1], posts[5]
        assert tagged.id != repost.id
        assert tagged.text == repost.text


@pytest.mark.parametrize(
    "name,expected",
    [("Грейд: Senior", "Senior"), ("грейд:  Middle+  ", "Middle+"), ("Уровень: Junior", "Junior")],
)
def test_grade_line_variants(name, expected):
    from probe.sources.telegram import GRADE_RE

    m = GRADE_RE.search(name)
    assert m and m.group(1).strip() == expected


class TestDescriptionLinks:
    """A teaser post keeps the vacancy behind a link; the href must survive parsing."""

    def test_whitelisted_link_is_collected(self):
        teaser = parse_fixture()[7]
        assert teaser.links == ["https://telegra.ph/Vydumannaya-vakansiya-09-17"]

    def test_link_outside_the_whitelist_is_ignored(self):
        """An arbitrary href is attacker-controlled: our fetcher must not follow it."""
        other = parse_fixture()[8]
        assert other.links == []

    def test_a_plain_post_has_no_links(self):
        assert parse_fixture()[0].links == []

    def test_subdomains_of_a_listed_host_pass(self):
        assert is_description_link("https://company.notion.site/Backend-1234")

    def test_a_lookalike_host_does_not_pass(self):
        """"telegra.ph.evil.com" contains the host name and must still be refused."""
        assert not is_description_link("https://telegra.ph.evil.com/job")

    def test_non_http_schemes_are_refused(self):
        assert not is_description_link("file:///etc/passwd")
        assert not is_description_link("javascript:alert(1)")

    def test_garbage_url_is_refused_not_raised(self):
        assert not is_description_link("http://[")


class TestTeaserLength:
    """MIN_VACANCY_CHARS exists to kill asides — it must not kill teasers."""

    def test_a_short_teaser_with_a_link_survives(self):
        teaser = parse_fixture()[7]
        assert len(teaser.text) < telegram.MIN_VACANCY_CHARS
        ok, why = looks_like_vacancy(teaser)
        assert ok, why

    def test_a_short_aside_without_a_link_is_still_dropped(self):
        ok, why = looks_like_vacancy(parse_fixture()[4])
        assert not ok
        assert "короткий" in why

    def test_a_teaser_still_has_to_look_like_hiring(self):
        """The relaxed floor only skips the length check, not the rest of them."""
        p = post("Смотреть", links=["https://telegra.ph/x"])
        ok, why = looks_like_vacancy(p)
        assert not ok
        assert "короткий" in why


class TestExternalText:
    def test_readable_text_strips_chrome(self):
        html = (
            "<html><body><nav>меню</nav><script>x=1</script>"
            "<article><p>Требования: Python от трёх лет</p></article>"
            "<footer>подвал</footer></body></html>"
        )
        text = _readable_text(html)
        assert "Требования: Python от трёх лет" in text
        assert "меню" not in text
        assert "подвал" not in text
        assert "x=1" not in text

    def test_body_is_used_when_there_is_no_article(self):
        assert "Описание" in _readable_text("<html><body><div>Описание</div></body></html>")

    def test_fetch_refuses_a_host_outside_the_whitelist(self):
        """No request is made at all: an unlisted host is refused before the client."""
        assert fetch_external("https://example-tracker.invalid/job/42") == ""

    def test_as_text_appends_the_description_and_labels_it(self):
        p = post(
            "Python Backend",
            external_url="https://telegra.ph/x",
            external_text="Требования: Python, PostgreSQL",
        )
        text = p.as_text()
        assert "Требования: Python, PostgreSQL" in text
        # The model must be able to tell the channel's own lines from the employer's.
        assert "https://telegra.ph/x" in text
        assert text.index("Python Backend") < text.index("Требования")

    def test_as_text_without_a_description_is_unchanged(self):
        assert "Полное описание" not in post("Обычный пост").as_text()

    def test_fetch_externals_skips_posts_with_no_links(self):
        """No links means no request: the transport would raise if one were made."""
        plain = post("Обычный пост")
        assert fetch_externals([plain], client=_mock_client(lambda r: 1 / 0)) == 0

    def test_fetch_externals_fills_only_the_first_link(self, no_sleep):
        """Later links are the company blog or the HR's other channel, not the job."""
        seen: list[str] = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text="<article>Требования: Python</article>")

        p = post("Тизер", links=["https://telegra.ph/a", "https://telegra.ph/b"])
        assert fetch_externals([p], client=_mock_client(handler)) == 1
        assert seen == ["https://telegra.ph/a"]
        assert p.external_url == "https://telegra.ph/a"
        assert "Требования: Python" in p.external_text

    def test_a_failed_fetch_leaves_the_post_alone(self, no_sleep):
        """One unreadable page must not sink the scan or fake a description."""
        handler = lambda r: httpx.Response(404)  # noqa: E731
        p = post("Тизер", links=["https://telegra.ph/a"])
        assert fetch_externals([p], client=_mock_client(handler)) == 0
        assert p.external_url == ""
        assert p.external_text == ""

    def test_a_timeout_is_swallowed(self, no_sleep):
        """telegra.ph being slow is not a reason to lose the whole scan."""
        def handler(request):
            raise httpx.ConnectTimeout("too slow")

        p = post("Тизер", links=["https://telegra.ph/a"])
        assert fetch_externals([p], client=_mock_client(handler)) == 0

    def test_a_non_html_response_is_refused(self, no_sleep):
        """A PDF would come back as mojibake and be scored as if it were text."""
        handler = lambda r: httpx.Response(  # noqa: E731
            200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4"
        )
        p = post("Тизер", links=["https://telegra.ph/a"])
        assert fetch_externals([p], client=_mock_client(handler)) == 0

    def test_a_long_page_is_capped(self, no_sleep):
        """One long article must not crowd the triage batch that holds many vacancies."""
        handler = lambda r: httpx.Response(  # noqa: E731
            200, headers={"content-type": "text/html"},
            text="<article>" + ("текст " * 20000) + "</article>",
        )
        p = post("Тизер", links=["https://telegra.ph/a"])
        fetch_externals([p], client=_mock_client(handler))
        assert len(p.external_text) == telegram.MAX_EXTERNAL_CHARS

    def test_limit_caps_the_requests(self, no_sleep):
        calls: list[str] = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text="<article>описание</article>")

        posts = [post("Тизер", links=["https://telegra.ph/a"]) for _ in range(5)]
        assert fetch_externals(posts, limit=2, client=_mock_client(handler)) == 2
        assert len(calls) == 2
        assert sum(1 for p in posts if p.external_text) == 2
