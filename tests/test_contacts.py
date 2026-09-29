"""Contacts read from the resume text: what is taken, and what must not be."""

from __future__ import annotations

import pytest

from core.contacts import extract_contacts


def kinds(text: str) -> dict[str, str]:
    return {c.kind: c.label for c in extract_contacts(text)}


class TestTaken:
    def test_the_users_own_header(self):
        """The line from the CV this feature was built for, markdown links and all."""
        text = (
            "**Контакты:** [maksim.slesarenko132@gmail.com](mailto:maksim.slesarenko132@gmail.com)"
            " · [Telegram @e1337_u53r](https://t.me/e1337_u53r)"
            " · [GitHub](https://github.com/1000MilesAway)"
        )
        assert kinds(text) == {
            "email": "maksim.slesarenko132@gmail.com",
            "telegram": "@e1337_u53r",
            "github": "github.com/1000MilesAway",
        }

    def test_link_list_appended_by_the_pdf_reader(self):
        """A PDF shows "@handle"; the address only survives in the link list."""
        text = "@e1337_u53r\n\nСсылки:\nhttps://t.me/e1337_u53r"
        assert kinds(text)["telegram"] == "@e1337_u53r"

    @pytest.mark.parametrize(
        "raw",
        ["+7 (913) 123-45-67", "8 913 123 45 67", "+79131234567", "8-913-123-45-67"],
    )
    def test_russian_phone_is_normalised(self, raw):
        assert kinds(f"Телефон: {raw}")["phone"] == "+7 913 123-45-67"

    def test_telegram_handle_next_to_the_word(self):
        assert kinds("tg: @ivan_petrov")["telegram"] == "@ivan_petrov"
        assert kinds("Telegram — @ivan_petrov")["telegram"] == "@ivan_petrov"

    def test_linkedin(self):
        assert kinds("linkedin.com/in/ivan-petrov")["linkedin"] == "linkedin.com/in/ivan-petrov"

    def test_links_are_built_not_copied(self):
        contacts = {c.kind: c.href for c in extract_contacts("a@b.ru +7 913 123-45-67 t.me/ivan_dev")}
        assert contacts == {
            "email": "mailto:a@b.ru",
            "phone": "tel:+79131234567",
            "telegram": "https://t.me/ivan_dev",
        }


class TestNotTaken:
    def test_years_and_salaries_are_not_phones(self):
        assert "phone" not in kinds("Сентябрь 2016 — Июль 2020, ЗП 150000, 10 000 пользователей")

    def test_a_bare_handle_is_not_telegram(self):
        """Could be Twitter, a decorator in a code sample, anything."""
        assert "telegram" not in kinds("Писал на Python, использовал @dataclass и @property")

    def test_first_of_each_kind_wins(self):
        """A former employer's address in an achievement must not reach the header."""
        text = "me@mail.ru\n...\nНастроил рассылку с noreply@company.ru"
        assert kinds(text)["email"] == "me@mail.ru"
        assert len(extract_contacts(text)) == 1

    def test_empty_and_none(self):
        assert extract_contacts("") == []
        assert extract_contacts(None) == []
