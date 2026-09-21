"""Telegram posts are flat text, so the cheap checks that rescue structure are
the ones worth pinning down.

No network: the page comes from tests/fixtures/tg_channel.html, hand-written
synthetic markup. Live LLM calls are banned by CLAUDE.md, and a live t.me fetch
would be just as flaky — the channel would answer differently tomorrow.
"""

from pathlib import Path

import pytest

from core.sources import tg_channels
from core.sources.telegram import (
    HTMLParser,
    Post,
    _channel_is_silent,
    _first_title_line,
    _parse_post,
    looks_like_vacancy,
)

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


class TestParsing:
    def test_reads_every_post_on_the_page(self):
        assert len(parse_fixture()) == 7

    def test_keeps_line_breaks(self):
        """<br> carries the layout: without it "Грейд: Middle+" merges into the next field."""
        first = parse_fixture()[0]
        assert "Golang разработчик" in first.text.split("\n")[0]
        assert "Грейд: Middle+" in first.text

    def test_reads_id_url_and_date(self):
        first = parse_fixture()[0]
        # Channel-qualified, because Telegram numbers posts per channel: a
        # bare "101" collides with post 101 of every other channel, and the
        # product stores this as the row's external_id.
        assert first.id == "jobchannel/101"
        assert first.url == "https://t.me/jobchannel/101"
        assert first.published.startswith("2099-09-16")

    def test_the_id_carries_the_channel_so_posts_cannot_collide(self):
        """Two channels both have a post 101; they must stay two rows."""
        first = parse_fixture()[0]
        assert first.id.startswith(f"{first.channel}/")
        assert first.id != first.id.split("/")[-1]

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
    """These read the shipped list, so they fail loudly when it is missing.

    `load()` returns {} for an absent file on purpose — a user who does not
    want Telegram is not an error. That turned a lost shipping file into
    `assert 'godevjob' in []` and a bare KeyError, which is what CI showed
    when data/ was still ignored wholesale. The check below names the cause
    instead.
    """

    @pytest.fixture(autouse=True)
    def the_list_is_shipped(self):
        if not tg_channels.DEFAULT_PATH.exists():
            pytest.fail(
                f"список каналов не найден: {tg_channels.DEFAULT_PATH}. "
                "Он поставляется с репозиторием — проверьте .gitignore"
            )

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
    from core.sources.telegram import GRADE_RE

    m = GRADE_RE.search(name)
    assert m and m.group(1).strip() == expected
