"""A band written in prose, read into numbers.

Every case here is a shape seen on a configured channel. The guard rails matter
more than the happy path: a figure invented from a misparse travels on into the
evaluation and the report as if it were published fact.
"""

import pytest

from core.sources.salary_text import parse_salary


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Зарплата: от 250 000 ₽", (250000, 250000, "RUB")),
        ("Вилка: 180 000 - 250 000 руб", (180000, 250000, "RUB")),
        ("от 185 до 215 тыс руб", (185000, 215000, "RUB")),
        # The unit is written once, after the last figure. Scaling only the
        # figure it touches gives a band three orders of magnitude wide — and
        # the bogus low end is what salary_min then compares against.
        ("350–380 к", (350000, 380000, "RUB")),
        ("Оклад 150к", (150000, 150000, "RUB")),
        ("зарплата от 200 000", (200000, 200000, "RUB")),
        ("ЗП: 2000$-4500$", (2000, 4500, "USD")),
        # "до X" is a ceiling with no stated floor, not a point value.
        ("до 8 000$ в месяц", (0, 8000, "USD")),
        # Annual figures normalize to monthly, which is what salary_min means.
        ("$180,000 per year", (15000, 15000, "USD")),
    ],
)
def test_reads_a_band(text, expected):
    band = parse_salary(text)
    assert band is not None, text
    assert (band.min, band.max, band.currency) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A POSITIVE statement that there is no band. Without this the digits
        # left on neighbouring lines are all a number parser sees.
        "Зарплата: не указана",
        "Зарплата по договорённости",
        # Not money at all.
        "Требуемый опыт: 3–6 лет",
        "Дата публикации: 13.08.2026",
        "от 3 лет опыта",
        "Стек: Python, Django, PostgreSQL",
        "Телефон: 8 900 123 45 67",
        "Нас уже 5000 подписчиков",
        "Компания основана в 1998 году",
        # Money the candidate PAYS. A course ad that slipped through as a
        # posting parses into a perfectly plausible-looking band.
        "1200 € или 120 000 ₽, возможна оплата частями",
        "Стоимость курса 90 000 ₽, есть рассрочка",
        # Somebody else's pay, or pay that only starts later.
        "Руководители стажировки — с зарплатами 500–600 тыс ₽",
        "После испытательного срока зарплата 300 000 ₽",
        # Per hour needs an hours-per-week assumption to normalize. Refuse.
        "Ставка: 3000 ₽ в час",
    ],
)
def test_refuses_to_guess(text):
    assert parse_salary(text) is None


def test_reads_the_band_line_out_of_a_whole_post():
    post = (
        "#вакансия Python-разработчик\n"
        "Требуемый опыт: 3–6 лет\n"
        "Вилка: 220 000 - 280 000 ₽\n"
        "Дата публикации: 13.08.2026\n"
        "Писать: @hr"
    )
    band = parse_salary(post)
    assert (band.min, band.max) == (220000, 280000)


def test_an_implausible_figure_is_a_misparse_not_an_offer():
    """Out of range → None → "no salary data" → the posting passes."""
    assert parse_salary("Зарплата: 5 ₽") is None
    assert parse_salary("Зарплата: 900 000 000 ₽") is None


def test_empty_input_is_not_a_crash():
    assert parse_salary("") is None
    assert parse_salary("   \n  ") is None
