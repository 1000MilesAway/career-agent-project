"""The fraud mark: rules from data, no model, and no mark on honest postings.

The honest cases are real phrases from the production pool (2026-10-05)
that a naive word list would have marked.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Posting as Pooled
from core import fraud
from tests.dbmark import requires_db


def mark(text: str, title: str = "Вакансия") -> str | None:
    return fraud.check(title, text)


class TestStrong:
    @pytest.mark.parametrize(
        ("text", "why"),
        [
            ("Обучение платное, 5000 р., окупится за неделю", "заплатить за обучение"),
            ("Перед стартом нужно внести залог 3000 ₽ за материалы", "внести деньги"),
            ("Для начала работы внесите депозит", "внести деньги"),
            ("Необходимо оплатить курс, затем трудоустройство", "заплатить за обучение"),
            ("Требуется стартовый капитал от 10 000", "стартовый капитал"),
            ("Поиск межбиржевых связок, работа по готовой стратегии", "банковскими картами"),
            ("P2P-арбитраж, доход от 5% в день", "банковскими картами"),
            ("Нужно оформить карту на себя и принимать переводы", "банковскими картами"),
            ("Ищем закладчиков, оплата сразу", "закладки"),
        ],
    )
    def test_one_strong_signal_marks(self, text, why):
        reason = mark(text)
        assert reason is not None
        assert why in reason

    def test_the_same_words_in_an_anti_fraud_job_are_its_subject(self):
        text = "Аналитик антифрода: выявление дропов и схем обнала по картам"
        assert mark(text) is None


class TestHonestPostings:
    @pytest.mark.parametrize(
        "text",
        [
            # Phrases from the production pool.
            "Мы предлагаем: ДМС, оплату обучения и конференций",
            "Компания предоставляет услуги кредитования под залог имущества",
            "Ищем facebook media buyer в растущую международную арбитражную команду",
            "Защита почты от спама, фишинга и вредоносных вложений",
            "Вилка $6k–$12k gross. Пишите в личку! @key_eugene",
            "Рассмотрим кандидатов без опыта, обучим",
            # Marked by the first rules: the company's clients pay, not the
            # candidate.
            "Помогать пользователям выбрать программу, оформить и оплатить обучение",
            "Развивать ИИ на сайте: помогать клиенту оформить договор, внести взнос",
        ],
    )
    def test_is_not_marked(self, text):
        assert mark(text) is None


class TestWeak:
    def test_one_weak_signal_does_not_mark(self):
        assert mark("Пишите в личку, обсудим") is None

    def test_two_different_weak_signals_mark_and_are_named(self):
        reason = mark("Работа с телефона, без опыта. Пиши в лс")
        assert reason is not None
        assert "без опыта" in reason
        assert "работа с телефона" in reason

    def test_the_title_is_read_too(self):
        assert mark("Подробности по ссылке", title="Платное обучение профессии") is not None


def test_yo_and_line_breaks_do_not_hide_a_signal():
    assert mark("Обучение за ваш\nсчёт") is not None


def test_no_text_is_no_mark():
    assert fraud.check("Python developer", None) is None


@requires_db
class TestPool:
    async def test_a_pooled_posting_carries_the_mark(self, session):
        from app.services import pool
        from app.services.pool import Sighting
        from core.sources.habr_api import Posting

        honest = Posting(id="1", url="u1", title="Python", company="A")
        scam = Posting(id="2", url="u2", title="Менеджер", company="")
        await pool.upsert(session, "telegram", [
            Sighting(honest, "Пишите в личку"),
            Sighting(scam, "Обучение платное, потом выход на доход"),
        ])
        rows = {
            r.external_id: r.fraud_reason
            for r in (await session.execute(select(Pooled))).scalars()
        }
        assert rows["1"] is None
        assert "заплатить за обучение" in rows["2"]
