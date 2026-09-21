"""Repairing state written before the filters were fixed.

The bug being pinned here is expensive in a quiet way: a profile that lost the
template's guard lists keeps passing junk on every later scan, and the user only
notices when they pay to evaluate it.
"""

from core.defaults import starter_profile
from core.filters import SearchProfile
from core.repair import clean_found, repair_profile, row_to_posting


def row(title: str, **kw) -> dict:
    base = {
        "id": kw.pop("id", "1"),
        "url": "https://career.habr.com/vacancies/1",
        "title": title,
        "company": "Компания",
    }
    return {**base, **kw}


class TestRepairProfile:
    def test_template_guards_come_back(self):
        """The exact loss from 2026-09-14: model lists replaced template roots."""
        damaged = {
            "queries": ["Python FastAPI разработчик"],
            "positive": ["Python", "Backend"],
            "negative": ["word:QA", "Аналитик"],
            "divisions_negative": ["Тестирование, контроль качества"],
            "min_qualification": "middle",
        }
        fixed = repair_profile(damaged)
        assert "Фронтенд" in fixed["divisions_negative"]
        # Mobile is a neighbouring stack, so it lives in the soft list — but it
        # still has to come back: the stored profile had neither.
        assert "Мобильн" in fixed["divisions_soft"]
        assert "Frontend" in fixed["negative"]
        assert "iOS" in fixed["negative"]

    def test_model_findings_are_kept(self):
        """Repair must not undo the model's work — it only adds what was lost.

        `queries` stays exactly as the model wrote it: that is the model reading
        this CV. `positive` is unioned instead of replaced, because a repair is
        undoing a loss — see repair_profile's docstring.
        """
        damaged = {
            "queries": ["Python FastAPI разработчик"],
            "positive": ["Python"],
            "negative": ["Аналитик"],
            "divisions_negative": ["Тестирование, контроль качества"],
        }
        fixed = repair_profile(damaged)
        assert fixed["queries"] == ["Python FastAPI разработчик"]
        assert "Python" in fixed["positive"]
        assert "MLOps" in fixed["positive"]
        assert "Аналитик" in fixed["negative"]
        assert "Тестирование, контроль качества" in fixed["divisions_negative"]

    def test_no_stored_profile_gives_the_template(self):
        assert repair_profile(None) == starter_profile().as_dict()

    def test_repair_is_idempotent(self):
        once = repair_profile({"negative": ["Аналитик"]})
        assert repair_profile(once) == once


class TestHabrDivisionNames:
    """Division roots have to match the names Habr actually states.

    Read off live postings on 2026-09-14. A root that matches nothing is worse
    than no root at all: it reads as protection that isn't there.
    """

    def test_real_division_names_are_caught(self):
        """Title here is deliberately generic: "Разработчик" names no
        technology, so both the hard and the soft list stay in charge."""
        prof = starter_profile()
        for name in [
            "Фронтенд разработчик",
            "Разработчик мобильных приложений",
            "DevOps-инженер",
            "Разработчик баз данных",
            "Десктоп разработчик",
            "Инженер встраиваемых систем",
            "Тестирование, контроль качества",
        ]:
            p = row_to_posting(row("Разработчик", divisions=[name]))
            from core.filters import PreScreen

            assert not PreScreen(prof).check(p).keep, name

    def test_backend_division_junk_is_caught_by_title(self):
        """Habr files PHP, Scala, Oracle and Kotlin under "Бэкенд разработчик",
        so the specialization filter never sees them — the title list is the
        only thing standing there."""
        from core.filters import PreScreen

        prof = starter_profile()
        for title in [
            "Middle/Senior PHP-разработчик (Laravel + высоконагруженные)",
            "Scala разработчик",
            "Android Developer (Kotlin)",
            "Разработчик Oracle PL/SQL",
            "Разработчик ЦФТ-Банк (Oracle, PL/SQL / PL PLUS)",
            "Frontend-разработчик (React)",
        ]:
            p = row_to_posting(row(title, divisions=["Бэкенд разработчик"]))
            assert not PreScreen(prof).check(p).keep, title


class TestFalsePositivesFoundByHand:
    """Four live vacancies the first version of this repair wrongly deleted.

    Found by reading the removal list on 2026-09-14 before applying it. Each one
    is a real posting the user would have wanted to see.
    """

    def screen(self):
        from core.filters import PreScreen

        return PreScreen(starter_profile())

    def test_product_name_containing_a_stop_word(self):
        """"Нейроюрист" is an ML product; a bare "Юрист" stop-word ate it."""
        p = row_to_posting(row("Старший LLM-разработчик в сервис Нейроюрист"))
        assert self.screen().check(p).keep

    def test_real_lawyer_vacancy_still_drops(self):
        """The word: fix must not disarm the stop-word itself."""
        assert not self.screen().check(row_to_posting(row("Юрист по контрактам"))).keep

    def test_wrong_division_does_not_beat_a_matching_title(self):
        """Employers file postings under the wrong specialization. A title that
        already cleared the stop-word list is the stronger signal."""
        for title, division in [
            ("Python разработчик", "Разработчик мобильных приложений"),
            ("Software engineer (Go\\Python)", "Инженер встраиваемых систем"),
        ]:
            p = row_to_posting(row(title, divisions=[division]))
            assert self.screen().check(p).keep, title

    def test_division_still_catches_a_title_without_a_match(self):
        """The override needs a positive hit — it is not a blanket amnesty."""
        p = row_to_posting(row("Инженер поддержки", divisions=["Техническая поддержка"]))
        prof = starter_profile()
        prof.divisions_negative.append("Техническая поддержка")
        from core.filters import PreScreen

        assert not PreScreen(prof).check(p).keep

    def test_another_profession_outranks_the_title(self):
        """QA is not a neighbouring stack, it is another job. A QA posting's
        title says "Python" legitimately, so the title cannot vouch for it —
        this is the case the specialization filter exists for."""
        from core.filters import PreScreen

        p = row_to_posting(row("Python разработчик", divisions=["Инженер по тестированию"]))
        assert not PreScreen(starter_profile()).check(p).keep

    def test_repair_restores_ml_keywords(self):
        """A stored profile without "MLOps" dropped live MLOps vacancies."""
        damaged = {"positive": ["Python", "Backend"], "negative": []}
        fixed = repair_profile(damaged)
        assert "MLOps" in fixed["positive"]
        assert "ML-инженер" in fixed["positive"]
        assert "Python" in fixed["positive"]


class TestCleanFound:
    def test_junk_is_removed(self):
        rows = [
            row("Python разработчик", id="1"),
            row("Frontend-разработчик (React)", id="2", divisions=["Фронтенд разработчик"]),
        ]
        out = clean_found(rows, starter_profile())
        assert [r["id"] for r in out.kept] == ["1"]
        assert len(out.removed) == 1

    def test_paid_evaluation_is_never_thrown_away(self):
        """Removing a scored row would charge the user twice for one verdict."""
        rows = [
            row("iOS Developer", id="9", divisions=["Разработчик мобильных приложений"],
                report={"score": 4.0, "verdict": "apply"}),
        ]
        out = clean_found(rows, starter_profile())
        assert [r["id"] for r in out.kept] == ["9"]
        assert not out.removed
        assert [r["id"] for r in out.protected] == ["9"]

    def test_removal_carries_a_reason(self):
        rows = [row("QA Engineer", id="3")]
        out = clean_found(rows, starter_profile())
        assert out.removed[0][1]

    def test_empty_profile_keeps_everything(self):
        rows = [row("Что угодно", id="1"), row("И это тоже", id="2")]
        out = clean_found(rows, SearchProfile())
        assert len(out.kept) == 2
