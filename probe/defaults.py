"""Starter search profile shipped with the product.

career-ops ships `templates/portals.example.yml` and the user copies it — the
keyword lists are part of the tool, not homework for the user. This is our
equivalent: a sane IT default the agent then adapts to the actual CV.

The lists below are seeded from the user's own career-ops portals.yml
(title_filter.positive / negative), trimmed to what applies on Habr Career.
"""

from __future__ import annotations

from copy import deepcopy

from .filters import SearchProfile

# Habr specializations that almost never fit a backend/ML profile. Checked
# against the `divisions` field, which is stated per posting — this is what
# catches a QA vacancy whose title merely mentions Python.
#
# These are matched as substrings against the division name Habr actually
# states, so they are kept as ROOTS ("Фронтенд" catches "Фронтенд разработчик").
# The names below were read off live postings on 2026-09-14, not guessed:
# Habr's own labels are "Фронтенд разработчик", "Разработчик мобильных
# приложений", "DevOps-инженер", "Разработчик баз данных", "Десктоп
# разработчик", "Инженер встраиваемых систем".
DIVISIONS_NEGATIVE_IT = [
    "Тестирован",
    "тестирования",
    "QA",
    "Фронтенд",
    "Копирайтер",
    "Бизнес-аналитик",
    "BI-разработчик",
    "Инженер по данным",
    "Промпт-инженер",
    "Дизайн",
    "Менеджер",
    "Маркетинг",
    "Продаж",
    "Поддержк",
    "1С",
]

# Neighbouring STACKS, not other professions. These are still development work,
# and Habr's label is often simply wrong — so a specific technology in the title
# (Python, Golang, ML) overrules them. See PreScreen.check step 2.
DIVISIONS_SOFT_IT = [
    "Мобильн",
    "DevOps",
    "баз данных",
    "Десктоп",
    "встраиваемых",
]

# Divisions this candidate actually targets. A title that says only "Разработчик"
# names no technology, so on its own it proves nothing — but inside one of these
# divisions it is our kind of work. This is what keeps "Principal backend
# engineer" and "Разработчик бэкенда в Яндекс Образование" while dropping
# "Разработчик Hadoop" and "BI Developer". See PreScreen.check step 3.
DIVISIONS_POSITIVE_IT = [
    "Бэкенд разработчик",
    "Фулстек разработчик",
    "ML разработчик",
    "MLOps-инженер",
]

STARTER_IT = SearchProfile(
    queries=["Python", "Backend", "FastAPI", "Golang", "Machine Learning", "Computer Vision"],
    positive=[
        "Python", "Backend", "Бэкенд", "Бекенд", "Back-end",
        "FastAPI", "Django", "Flask", "aiohttp", "asyncio",
        "Fullstack", "Full Stack", "Full-stack", "Фулстек",
        "word:ML", "Machine Learning", "Computer Vision", "Deep Learning",
        "Inference", "MLOps", "Машинное обучение", "Компьютерное зрение",
        "глубокому обучению", "глубокое обучение",
        "Разработчик", "Программист", "Инженер-программист",
        "AI-разработчик", "ML-инженер", "ML-разработчик",
        "Golang", "word:Go Developer", "word:Go Engineer",
        "Platform Engineer", "Software Engineer",
    ],
    negative=[
        # seniority we don't want
        "Junior", "Джуниор", "Стажёр", "Стажер", "word:Intern", "Trainee",
        # other professions. Both alphabets: Habr titles mix them freely, and a
        # Russian-only list lets "BI Analyst" and "AQA Engineer" straight through.
        "word:QA", "QA-", "AQA", "word:QC", "Тестировщик", "Тестирован", "SDET",
        "Automation Engineer", "Quality Assurance",
        "Аналитик", "Analyst", "Аналитика", "Менеджер", "Manager", "Руководитель",
        # "Юрист" needs word: — without it "Нейроюрист", a real ML product
        # name, drops an ML vacancy.
        "Рекрутер", "Recruiter", "Дизайнер", "Маркетолог", "Продавец", "word:Юрист",
        # Other stacks. Title stop-words are the ONLY defence for these: Habr
        # files PHP, Scala, Oracle and Kotlin vacancies under the division
        # "Бэкенд разработчик", so the specialization filter never sees them.
        # "разработчик + java" instead of listing spellings: on 2026-09-15
        # career-ops surfaced "Ведущий разработчик Java (Микросервисы)", which
        # neither "Java Developer" nor "Java-разработчик" matched — the words
        # were simply in the other order.
        "1С", "1C", "PHP", "разработчик + java", "developer + java", ".NET", "C#",
        "Frontend", "Фронтенд", "Android", "iOS", "DevOps Engineer", "word:SRE",
        "Support Engineer",
        "Scala", "Oracle", "PL/SQL", "PL PLUS", "Kotlin", "Delphi", "ЦФТ",
        "Symfony", "Laravel", "Битрикс", "Bitrix",
        "word:Vue", "Vue.js", "Angular", "word:React",
        # Stacks filed under "Бэкенд разработчик", where the division filter
        # cannot reach them. Verified against live listings on 2026-09-15.
        "C++", "С++", "word:C/C++", "Rust", "Hadoop", "GreenData",
        "Преподаватель", "AI-тренер", "AI Vibe",
    ],
    divisions_negative=DIVISIONS_NEGATIVE_IT,
    divisions_soft=DIVISIONS_SOFT_IT,
    divisions_positive=DIVISIONS_POSITIVE_IT,
    qualifications=["middle", "senior"],
    min_qualification="middle",
    remote_only=False,
    salary_min=None,
)


def starter_profile() -> SearchProfile:
    """A deep copy, so editing the result never mutates the shipped template."""
    return SearchProfile.from_dict(deepcopy(STARTER_IT.as_dict()))
