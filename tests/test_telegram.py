"""Telegram posts are flat text, so the cheap checks that rescue structure are
the ones worth pinning down.

No network: the page comes from tests/fixtures/tg_channel.html, hand-written
synthetic markup. Live LLM calls are banned by CLAUDE.md, and a live t.me fetch
would be just as flaky — the channel would answer differently tomorrow.
"""

from pathlib import Path

import httpx
import pytest

from core.sources import telegram, tg_channels
from core.sources.telegram import (
    HTMLParser,
    Post,
    _channel_is_silent,
    _company_of,
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
        # 11 blocks in the fixture, one of which is a forward from another
        # channel and is not this channel's publication to read.
        assert len(parse_fixture()) == 10

    def test_a_forward_from_another_channel_is_not_ours(self):
        """Its data-post names otherchannel — someone else published it.

        Counting it makes a channel look productive when it only reposts, and
        the same vacancy then arrives once per channel that forwarded it.
        """
        assert all(p.id.startswith("jobchannel/") for p in parse_fixture())
        assert not any("Системный аналитик" in p.text for p in parse_fixture())

    def test_inline_markup_does_not_break_a_line(self):
        """Only <br> ends a line: a bold label, a link and an emoji stay inline.

        Breaking on every node titled posts "Ищем" and "Middle PHP Developer в",
        and left "<b>Компания:</b> YADRO" as two lines no field rule matched.
        """
        node = HTMLParser(
            '<div class="tgme_widget_message_text"><i class="emoji"><b>🛰</b></i> '
            "<b>QA Engineer</b> в <a href=\"https://x.io\">Cyprus Constructions</a><br/><br/>"
            "<b>Компания:</b> YADRO<br/>ЗП:&nbsp;от 100 000 ₽</div>"
        ).css_first("div")
        assert telegram._message_text(node) == (
            "🛰 QA Engineer в Cyprus Constructions\n\nКомпания: YADRO\nЗП: от 100 000 ₽"
        )

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

    def test_drops_an_emoji_that_sits_next_to_words(self):
        assert _first_title_line("🔥 Go Developer") == "Go Developer"

    @pytest.mark.parametrize("text, title", [
        ("#вакансия #remote\n\nПривет! Мы в поиске Senior Python Developer для CVAT.ai!",
         "Senior Python Developer для CVAT.ai"),
        ("Всем привет, коллеги\nНа долгосрочный проект ищу Tech Lead NLP Engineer",
         "Tech Lead NLP Engineer"),
        ("Всем привет! На долгосрочный проект ищу Специалист комплаенс-контроля (KYC/AML)",
         "Специалист комплаенс-контроля (KYC/AML)"),
        ("Компания: Evenbeter\nPython Backend Developer", "Python Backend Developer"),
        ("Уровень\nMiddle\nGo Developer", "Go Developer"),
        ("#.NET\nSenior .NET Developer", "Senior .NET Developer"),
        ("📍Вакансия: Senior Backend Engineer Go — Custody Platform (The Vault)",
         "Senior Backend Engineer Go — Custody Platform (The Vault)"),
        ("Position: Machine Learning Engineer", "Machine Learning Engineer"),
        ("Вакансия Senior Android Developer", "Senior Android Developer"),
        ("Вакансия 1С-разработчик (КА 2, интеграции)", "1С-разработчик (КА 2, интеграции)"),
        ("iOS Developer (Senior) | VPN-приложение", "iOS Developer (Senior) | VPN-приложение"),
        # An English role does not decline, so "Ищем" goes; a Russian one
        # would read in the accusative without it, so it stays.
        ("🚀 Ищем C++ Developer (Middle)", "C++ Developer (Middle)"),
        ("Ищем Главного SRE/DevOps-инженера (Senior)", "Ищем Главного SRE/DevOps-инженера (Senior)"),
        ("Ищем в команду Scala разработчика.", "Ищем Scala разработчика"),
        ("Требуется Менеджер по работе с клиентами", "Менеджер по работе с клиентами"),
        # The label or the verb alone: the role is on the next line.
        ("Вакансия\nData Engineer DWH", "Data Engineer DWH"),
        ("Позиция:\nBackend-разработчик", "Backend-разработчик"),
        ("Ищем\nLLM / ML Engineer\nс опытом", "LLM / ML Engineer"),
        ("#вакансия\nhttps://avantstay.com/\nПривет! Ищем Backend Engineer.", "Backend Engineer"),
        ("Торговый представитель, г. Лесозаводск", "Торговый представитель, г. Лесозаводск"),
        ("❕ИТ-компания ищет Инженера данных)❕", "Инженера данных"),
        # "Employer" opens the title, it is not the "Employer:" field.
        ("Employer Brand Manager at Salmon", "Employer Brand Manager at Salmon"),
        # A link's caption sharing the title line.
        ("Junior Python Developer | Смотреть вакансию", "Junior Python Developer"),
        ("Hi! Now I'm looking for Senior DevOps Engineer in our company 🚀",
         "Senior DevOps Engineer"),
        ("Andersen Lab ☀️ is hiring a 𝐅𝐮𝐥𝐥 𝐒𝐭𝐚𝐜𝐤 𝐐𝐀 𝐄𝐧𝐠𝐢𝐧𝐞𝐞𝐫 for a project developing a platform",
         "Full Stack QA Engineer"),
        ("We are looking for a CPO (Chief Product Officer) at AskCraft — launching a product "
         "line in the international market with a long tail of words",
         "CPO (Chief Product Officer) at AskCraft"),
        ("✦ IT / SOLUTION ARCHITECT (CRYPTO) ✦ ║ ║ Infrastructure amp; Platform",
         "IT / SOLUTION ARCHITECT (CRYPTO) Infrastructure & Platform"),
        # A summary label and the offer tail on the headline line.
        ("tl;dr: founding full-stack engineer (early hire), $100k–130k + equity, remote",
         "Founding full-stack engineer (early hire)"),
        ("TL;DR — Senior Go Developer, remote, 300k", "Senior Go Developer"),
        ("tl;dr computer vision engineer, full-time, $3-6k per month, remote or Moscow",
         "Computer vision engineer"),
        ("Backend-разработчик, 250 000 ₽, удалённо", "Backend-разработчик"),
    ])
    def test_chat_prose_gives_the_role(self, text, title):
        """Chat messages (source "telethon") are prose, not channel layout."""
        assert _first_title_line(text) == title

    def test_empty_text_gives_empty_title(self):
        assert _first_title_line("\n  \n") == ""

    def test_decoration_only_text_gives_empty_title(self):
        assert _first_title_line("✨ — ✨\n···") == ""


class TestCompany:
    @pytest.mark.parametrize("text, company", [
        ("Компания: Evenbeter\nPython Backend Developer", "Evenbeter"),
        ("Компания — Combo Tech, продуктовая\nGo Developer", "Combo Tech"),
        ("Andersen Lab ☀️ is hiring a 𝐅𝐮𝐥𝐥 𝐒𝐭𝐚𝐜𝐤 𝐐𝐀 𝐄𝐧𝐠𝐢𝐧𝐞𝐞𝐫 for a project", "Andersen Lab"),
        ("Traffic Jam ищет Go-разработчика", "Traffic Jam"),
        ("Привет! Мы в поиске Senior Python Developer для CVAT.ai!", "CVAT.ai"),
        ("We are looking for a CPO (Chief Product Officer) at AskCraft — launching", "AskCraft"),
    ])
    def test_a_named_employer_is_found(self, text, company):
        assert _company_of(text, _first_title_line(text)) == company

    @pytest.mark.parametrize("text", [
        # An agency is not the employer.
        "Компания: Selecty (КА)\nQA Engineer",
        "Рекрутинговое агентство Brightman ищет аналитика",
        # Descriptions where a name should be.
        "Международная IT-команда в поиске Python Developer",
        "Компания: The largest online marketplace for debt trading\nDeveloper",
        "Мы ищем Java разработчика",
        "Python Developer для финтех-проекта",
        "Golang разработчик\nГрейд: Middle",
    ])
    def test_no_name_means_no_company_not_the_channel(self, text):
        assert _company_of(text, _first_title_line(text)) == ""

    @pytest.mark.parametrize("text, company", [
        # The bold label and its value are split by <br> in some channels.
        ("Middle Python Developer\nКомпания:\nSL Soft\nПолная занятость", "SL Soft"),
        ("Компания: Simple Group - ведущий импортер оборудования\nИнженер", "Simple Group"),
        ("Компания: NAS Technologies** (nastechnologies.ru)\nИнженер", "NAS Technologies"),
        # The field states a name, so lower-case words in it are part of it.
        ("Компания: Метр квадратный\nМенеджер", "Метр квадратный"),
        # A line introducing the employer.
        ("Junior backend-разработчик\nОфис (Москва)\nХ5 — ведущая розничная компания.", "Х5"),
        ("Java-разработчик\nСтек: Java\nСбер Банк — крупнейший российский финансовый "
         "конгломерат", "Сбер Банк"),
        ("Лидер команды\nв VK AdBlogger — внутренний стартап, который помогает", "VK AdBlogger"),
        ("Technical Lead\nKake is a technology company building web apps.", "Kake"),
        ("Python-разработчик\nБанк «Русский Стандарт» — крупный коммерческий банк", "Русский Стандарт"),
        # Quoted legal names and openings.
        ('Аккредитованная ИТ-компания "Системы и Алгоритмы" ищет Инженера данных', "Системы и Алгоритмы"),
        ("Мы в 2ГИС ищем Scala-разработчика в команду Рекламы.", "2ГИС"),
        ("Golang Developer\n250 000 ₽\nВФМ Технолоджи развивает сервис «Jam Работа».", "ВФМ Технолоджи"),
        # A name closing the title.
        ("Golang-разработчик в SimbirSoft\nУдалённо", "SimbirSoft"),
        ("Go-разработчик в TATLIN.UNIFIED — YADRO\nYADRO разрабатывает СХД.", "YADRO"),
        ("Backend Engineer на Go — Listee\nListee строит платформу.", "Listee"),
    ])
    def test_more_shapes_name_the_employer(self, text, company):
        assert _company_of(text, _first_title_line(text)) == company

    @pytest.mark.parametrize("text", [
        "Team Lead ML Engineer\nМы - уникальный продукт\nСейчас ищем активных ребят",
        "Senior iOS-разработчик\nУдалёнка\nRedhr — рекрутинговое агентство.",
        "Golang Developer\nСтек: Go\nTribe is a full-cycle HR agency offering recruitment",
        "В международную платежную компанию ищем\nSales Manager",
        "Требования — опыт работы от 3 лет в компании",
        # A team or a product after "в"/"для", not a company.
        "Senior Go-разработчик в LLM-проекты Яндекса",
        "IOS-разработчик в Маркетплейс",
        "Kotlin-разработчик для VPN-приложения",
        # A dash closing the title with no line of the body to back it.
        "Technical Lead — Legacy Financial System\nWe build software.",
        "Android-разработчик / Kotlin Multiplatform\nОпыт с Kotlin Multiplatform",
        # The value was a link, so the next line is the next field.
        "Middle Kotlin Developer\nCompany: http://blackrocksouth.com/\nWorkplace: Remote",
        # Placeholders where the name would be.
        "Middle Fullstack Developer\nCompany: NDA",
        "Вакансии и резюме в IT",
        # Prose read as a name, seen on the production pool 2026-10-19.
        "Go Developer\nБудет плюсом — опыт работы с платформой Kubernetes",
        "Go Developer\nОсновной фокус — развитие платформы",
        "Go Developer\nРебята активно ищут разработчика",
        "Go Developer\nПрекрасный Recraft.ai снова ищет разработчика",
        "Go Developer в Fintech",
        "Digest\nRetention Product Manager — Acme, удалённо, компания мечты",
        "Go Developer\nHead of Performance Marketing — компания Acme",
    ])
    def test_lookalikes_are_not_a_company(self, text):
        assert _company_of(text, _first_title_line(text)) == ""

    def test_the_company_moves_out_of_the_title(self):
        built = telegram.build_post("c", "c/1", "Golang-разработчик в SimbirSoft\nУдалённо", "", [])
        assert (built.title, built.company) == ("Golang-разработчик", "SimbirSoft")

    def test_a_title_only_naming_the_company_keeps_it(self):
        built = telegram.build_post("c", "c/1", "Компания: Aston\nAston", "", [])
        assert built.title == "Aston"

    def test_the_post_carries_it(self):
        built = telegram.build_post(
            "jobchannel", "jobchannel/1", "Компания: Evenbeter\nPython Backend Developer", "", []
        )
        assert built.company == "Evenbeter"


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

    def test_a_vacancy_card_without_hiring_words_is_a_vacancy(self):
        """talanto channels post field cards only: no "ищем", no "вакансия"."""
        card = (
            "Middle Python Developer\nФормат: гибрид (Москва)\nКомпания: Лига Цифровой Экономики\n"
            "ЗП: от 140 тыс руб\nПолная занятость\n#hybrid #middle #fulltime\n"
            "Обязанности: разработка сервисов на FastAPI, PostgreSQL.\n"
            "Откликнуться:\nhttps://talanto.work/jobs/53eb63bf"
        )
        assert looks_like_vacancy(post(card)) == (True, "")
        one_field = card.replace("ЗП:", "Зарплата от").replace("Формат:", "Формат —")
        assert looks_like_vacancy(post(one_field))[1] == "нет признаков найма"

    def test_a_recommended_candidate_is_not_a_vacancy(self):
        """Job chats carry "хочу порекомендовать <человека>" — a CV by proxy."""
        text = (
            "Всем привет, коллеги\nХочу порекомендовать Head of Marketing — ищем ей команду, "
            "в которой она сможет вырасти. Опыт восемь лет в B2B SaaS, запуск продуктов, "
            "performance и бренд. Вакансии и вопросы — в личку: @example_contact"
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


@pytest.mark.parametrize("line, expected", [
    ("Уровень: Middle+ / Senior — нужен самостоятельный специалист, способный вести блок",
     "Middle+ / Senior"),
    ("Грейд: Senior, опыт от 5 лет", "Senior"),
    ("Грейд: Middle (3+ года)", "Middle"),
    ("Уровень: специалист, который сам ведёт задачи и общается с заказчиком", "специалист"),
    ("Уровень: самостоятельный специалист который сам ведёт задачи и общается с заказчиком",
     ""),
])
def test_grade_is_the_level_not_the_sentence(line, expected):
    """It is stored in varchar(32): a long one broke the whole pool insert."""
    from core.sources.telegram import _grade_of

    assert _grade_of(f"Python-разработчик\n{line}") == expected


class TestApplyLink:
    """Where the user actually applies. The product never applies for them, so
    losing this link leaves them to re-find the apply path by hand."""

    def test_takes_the_posting_link_not_the_banner(self):
        """The banner comes FIRST in the post and would win a take-the-first rule.

        ya_jobs prefixes every post with /jobs/pages/benefits — and since a url
        is a dedup key, taking it would collapse a whole channel to one vacancy.
        """
        with_links = next(p for p in parse_fixture() if p.id == "jobchannel/108")
        assert with_links.apply_url == "https://vymyshlennaya.ru/jobs/vacancies/884412"

    def test_the_channel_promo_is_never_an_apply_link(self):
        with_links = next(p for p in parse_fixture() if p.id == "jobchannel/108")
        assert "t.me" not in with_links.apply_url

    def test_the_permalink_stays_the_dedup_key(self):
        """A repost of one vacancy shares the apply link; a digest shares it too."""
        with_links = next(p for p in parse_fixture() if p.id == "jobchannel/108")
        assert with_links.url == "https://t.me/jobchannel/108"

    def test_a_post_without_links_has_no_apply_url(self):
        plain = next(p for p in parse_fixture() if p.id == "jobchannel/101")
        assert plain.apply_url == ""

    def test_the_apply_link_reaches_the_model(self):
        with_links = next(p for p in parse_fixture() if p.id == "jobchannel/108")
        assert "Отклик: https://vymyshlennaya.ru/jobs/vacancies/884412" in with_links.as_text()


class TestSalaryBand:
    """Without a parsed band PreScreen.salary_min was inert on Telegram: every
    post passed as "no salary data" whatever it paid."""

    def test_reads_a_floor_from_the_prose(self):
        tagged = next(p for p in parse_fixture() if p.id == "jobchannel/102")
        assert (tagged.salary_from, tagged.salary_currency) == (250000, "RUB")

    def test_reads_a_range(self):
        with_links = next(p for p in parse_fixture() if p.id == "jobchannel/108")
        assert (with_links.salary_from, with_links.salary_to) == (220000, 280000)

    def test_a_post_stating_no_pay_gets_no_band(self):
        plain = next(p for p in parse_fixture() if p.id == "jobchannel/101")
        assert plain.salary_from is None and plain.salary_to is None


class TestMarkupChanged:
    """A markup change must not look like a quiet channel.

    Both arrive downstream as "no posts", so without this a t.me redesign would
    zero out every channel at once and the scan would report "ничего не найдено"
    as if that were the truth.
    """

    def _fetch(self, html: str, monkeypatch):
        import httpx

        from core.sources import telegram as tg

        class FakeResponse:
            status_code = 200
            text = html

            def raise_for_status(self):
                pass

        class FakeClient:
            def get(self, url):
                return FakeResponse()

            def close(self):
                pass

        monkeypatch.setattr(tg, "_client", lambda: FakeClient())
        monkeypatch.setattr(httpx, "Client", lambda **kw: FakeClient())
        return tg.fetch_channel("jobchannel")

    def test_posts_present_but_none_parsed_raises(self, monkeypatch):
        from core.sources.telegram import MarkupChanged

        # The wrapper is there, the inner nodes we read are not.
        html = """<div class="tgme_widget_message_wrap">
            <div class="tgme_widget_message" data-post="jobchannel/1">
              <div class="brand_new_text_class">Ищем Go-разработчика</div>
            </div></div>"""
        with pytest.raises(MarkupChanged):
            self._fetch(html, monkeypatch)

    def test_a_genuinely_empty_page_is_not_an_error(self, monkeypatch):
        assert self._fetch("<html><body>nothing here</body></html>", monkeypatch) == []

    def test_a_media_only_post_is_a_legitimate_drop(self, monkeypatch):
        """A photo post: no text node, and nothing in the block but the counters."""
        html = """<div class="tgme_widget_message_wrap">
            <div class="tgme_widget_message" data-post="jobchannel/1">
              <div class="tgme_widget_message_photo"></div>
              <div class="tgme_widget_message_footer">
                <span class="tgme_widget_message_views">1.2K</span>
                <time datetime="2099-09-16T09:00:00+00:00">16 Sep</time>
              </div>
            </div></div>"""
        assert self._fetch(html, monkeypatch) == []

    def test_a_page_of_only_forwards_is_a_legitimate_drop(self, monkeypatch):
        html = """<div class="tgme_widget_message_wrap">
            <div class="tgme_widget_message" data-post="otherchannel/9">
              <div class="tgme_widget_message_text">Ищем аналитика, писать @hr</div>
            </div></div>"""
        assert self._fetch(html, monkeypatch) == []


def _mock_client(handler) -> httpx.Client:
    """An httpx client answered by `handler` instead of the network.

    MockTransport ships with httpx, so pinning the fetching down costs no new
    dependency — and CLAUDE.md forbids adding one.
    """
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _html(body: str):
    return lambda _r: httpx.Response(
        200, headers={"content-type": "text/html"}, text=body
    )


@pytest.fixture
def no_sleep(monkeypatch):
    """Drop the politeness delay: these tests talk to MockTransport, not a host."""
    monkeypatch.setattr(telegram.time, "sleep", lambda _s: None)


class TestTeaserLinks:
    """A teaser keeps the vacancy behind a link; the href must survive parsing."""

    def test_the_description_link_is_collected(self):
        teaser = parse_fixture()[8]
        assert teaser.description_url == "https://telegra.ph/Vydumannaya-vakansiya-09-17"

    def test_a_link_outside_the_whitelist_is_ignored(self):
        assert parse_fixture()[9].description_url == ""

    def test_a_plain_post_has_no_description_link(self):
        assert parse_fixture()[0].description_url == ""

    def test_the_apply_link_is_picked_independently(self):
        """apply_link refuses telegra.ph, so a teaser has a description but no apply url."""
        teaser = parse_fixture()[8]
        assert teaser.description_url
        assert teaser.apply_url == ""


class TestTeaserLength:
    """MIN_VACANCY_CHARS exists to kill asides — it must not kill teasers."""

    def test_a_short_teaser_with_a_link_survives(self):
        teaser = parse_fixture()[8]
        assert len(teaser.text) < telegram.MIN_VACANCY_CHARS
        ok, why = looks_like_vacancy(teaser)
        assert ok, why

    def test_a_short_teaser_without_a_listed_link_is_still_dropped(self):
        ok, why = looks_like_vacancy(parse_fixture()[9])
        assert not ok
        assert "короткий" in why

    def test_a_short_aside_is_still_dropped(self):
        """"День программиста" has no link and must stay out."""
        ok, why = looks_like_vacancy(parse_fixture()[4])
        assert not ok
        assert "короткий" in why

    def test_the_teaser_floor_skips_only_the_length_check(self):
        """Everything else in looks_like_vacancy still has to pass."""
        p = post("Смотреть", description_url="https://telegra.ph/x")
        ok, why = looks_like_vacancy(p)
        assert not ok
        assert "короткий" in why


class TestReadableText:
    def test_chrome_is_stripped(self):
        text = telegram._readable_text(
            "<html><body><nav>меню</nav><script>x=1</script>"
            "<article><p>Требования: Python от трёх лет</p></article>"
            "<footer>подвал</footer></body></html>"
        )
        assert "Требования: Python от трёх лет" in text
        assert "меню" not in text
        assert "подвал" not in text
        assert "x=1" not in text

    def test_body_is_used_when_there_is_no_article(self):
        assert "Описание" in telegram._readable_text("<html><body><div>Описание</div></body></html>")

    def test_an_empty_page_gives_empty_text(self):
        assert telegram._readable_text("") == ""


class TestFetchDescription:
    def test_an_unlisted_host_is_refused_before_any_request(self):
        """No transport is touched: the whitelist check comes first."""
        assert telegram.fetch_description("https://example-tracker.invalid/x") == ""

    def test_a_listed_host_is_read(self):
        got = telegram.fetch_description(
            "https://telegra.ph/x",
            client=_mock_client(_html("<article>Требования: Python</article>")),
        )
        assert "Требования: Python" in got

    def test_a_404_gives_empty_not_an_exception(self):
        got = telegram.fetch_description(
            "https://telegra.ph/x", client=_mock_client(lambda _r: httpx.Response(404))
        )
        assert got == ""

    def test_a_timeout_gives_empty(self):
        """telegra.ph being slow must not sink the scan."""
        def handler(_r):
            raise httpx.ConnectTimeout("too slow")

        assert telegram.fetch_description("https://telegra.ph/x", client=_mock_client(handler)) == ""

    def test_a_non_html_response_is_refused(self):
        """A PDF would arrive as mojibake and be scored as if it were text."""
        handler = lambda _r: httpx.Response(  # noqa: E731
            200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4"
        )
        assert telegram.fetch_description("https://telegra.ph/x", client=_mock_client(handler)) == ""

    def test_a_long_page_is_capped(self):
        got = telegram.fetch_description(
            "https://telegra.ph/x",
            client=_mock_client(_html("<article>" + ("текст " * 20000) + "</article>")),
        )
        assert len(got) == telegram.MAX_EXTERNAL_CHARS


class TestFetchExternals:
    def test_posts_without_a_link_are_not_requested(self):
        """The handler would raise if it were called at all."""
        plain = post("Обычный пост")
        assert telegram.fetch_externals([plain], client=_mock_client(lambda _r: 1 / 0)) == 0

    def test_the_description_is_filled_in(self, no_sleep):
        p = post("Тизер", description_url="https://telegra.ph/a")
        assert telegram.fetch_externals(
            [p], client=_mock_client(_html("<article>Требования: Python</article>"))
        ) == 1
        assert "Требования: Python" in p.description_text

    def test_a_failed_fetch_leaves_the_post_alone(self, no_sleep):
        """One unreadable page must not fake a description or sink the scan."""
        p = post("Тизер", description_url="https://telegra.ph/a")
        assert telegram.fetch_externals(
            [p], client=_mock_client(lambda _r: httpx.Response(500))
        ) == 0
        assert p.description_text == ""

    def test_an_already_filled_post_is_not_refetched(self):
        p = post("Тизер", description_url="https://telegra.ph/a", description_text="уже есть")
        assert telegram.fetch_externals([p], client=_mock_client(lambda _r: 1 / 0)) == 0

    def test_limit_caps_the_requests(self, no_sleep):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text="<article>описание</article>")

        posts = [post("Тизер", description_url="https://telegra.ph/a") for _ in range(5)]
        assert telegram.fetch_externals(posts, limit=2, client=_mock_client(handler)) == 2
        assert len(calls) == 2


class TestFullText:
    def test_the_description_is_appended_and_labelled(self):
        """The model must be able to tell the channel's lines from the employer's page."""
        p = post("Python Backend",
                 description_url="https://telegra.ph/x",
                 description_text="Требования: Python, PostgreSQL")
        text = p.full_text()
        assert text.startswith("Python Backend")
        assert "Требования: Python, PostgreSQL" in text
        assert "https://telegra.ph/x" in text

    def test_without_a_description_the_text_is_unchanged(self):
        p = post("Обычный пост")
        assert p.full_text() == "Обычный пост"

    def test_a_link_with_no_text_read_changes_nothing(self):
        """The link alone is not a description: the fetch may well have failed."""
        p = post("Тизер", description_url="https://telegra.ph/x")
        assert p.full_text() == "Тизер"

    def test_as_text_carries_the_description_through(self):
        p = post("Тизер",
                 description_url="https://telegra.ph/x",
                 description_text="Требования: Python")
        assert "Требования: Python" in p.as_text()
