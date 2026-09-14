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
DIVISIONS_NEGATIVE_IT = [
    "Тестирован",
    "тестирования",
    "QA",
    "Фронтенд",
    "Мобильн",
    "Дизайн",
    "Менеджер",
    "Маркетинг",
    "Продаж",
    "Поддержк",
    "1С",
]

STARTER_IT = SearchProfile(
    queries=["Python", "Backend", "FastAPI", "Golang", "Machine Learning", "Computer Vision"],
    positive=[
        "Python", "Backend", "Бэкенд", "Бекенд", "Back-end",
        "FastAPI", "Django", "Flask", "aiohttp", "asyncio",
        "Fullstack", "Full Stack", "Full-stack", "Фулстек",
        "word:ML", "Machine Learning", "Computer Vision", "Deep Learning",
        "Inference", "MLOps", "Машинное обучение", "Компьютерное зрение",
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
        "Рекрутер", "Recruiter", "Дизайнер", "Маркетолог", "Продавец", "Юрист",
        # other stacks
        "1С", "1C", "PHP", "Java Developer", "Java-разработчик", ".NET", "C#",
        "Frontend", "Фронтенд", "Android", "iOS", "DevOps Engineer", "word:SRE",
        "Support Engineer",
    ],
    divisions_negative=DIVISIONS_NEGATIVE_IT,
    qualifications=["middle", "senior"],
    min_qualification="middle",
    remote_only=False,
    salary_min=None,
)


def starter_profile() -> SearchProfile:
    """A deep copy, so editing the result never mutates the shipped template."""
    return SearchProfile.from_dict(deepcopy(STARTER_IT.as_dict()))
