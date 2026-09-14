"""Filters decide what the user pays for, so their edges are pinned down here.

No network, no LLM — pure functions over made-up postings.
"""

from probe.defaults import starter_profile
from probe.filters import PreScreen, SearchProfile, _compile
from probe.sources.habr_api import Posting


def posting(title: str, **kw) -> Posting:
    base = dict(
        id=kw.pop("id", "1"), url="https://career.habr.com/vacancies/1",
        title=title, company=kw.pop("company", "Компания"),
    )
    return Posting(**base, **kw)


class TestKeywordCompilation:
    def test_short_keyword_does_not_match_inside_a_word(self):
        """"QA" must not fire on "Quality" — this is why career-ops has word:."""
        qa = _compile("QA")
        assert qa.search("QA Engineer")
        assert not qa.search("Quality Assurance Manager")

    def test_two_letter_keyword_is_anchored(self):
        go = _compile("Go")
        assert go.search("Go Developer")
        assert not go.search("Google Cloud Engineer")
        assert not go.search("Golang Developer")

    def test_word_prefix_forces_whole_word(self):
        p = _compile("word:Intern")
        assert p.search("Operations Intern")
        assert not p.search("Internal Tools Engineer")
        assert not p.search("International Sales")

    def test_long_keyword_matches_as_substring(self):
        p = _compile("Backend")
        assert p.search("Senior Backend Developer")
        assert p.search("BackendEngineer")

    def test_matching_is_case_insensitive(self):
        assert _compile("python").search("Senior PYTHON Developer")


class TestPreScreen:
    def test_negative_title_keyword_drops_posting(self):
        prof = SearchProfile(negative=["word:QA"], positive=["Python"])
        d = PreScreen(prof).check(posting("QA Engineer (Python)"))
        assert not d.keep
        assert "стоп-слово" in d.reason

    def test_negative_wins_over_positive(self):
        """A title matching both lists is rejected: negative is checked first."""
        prof = SearchProfile(positive=["Python"], negative=["Тестировщик"])
        assert not PreScreen(prof).check(posting("Тестировщик Python")).keep

    def test_division_filter_catches_clean_title(self):
        """The real case: title says Python, specialization says QA."""
        prof = SearchProfile(positive=["Python"], divisions_negative=["Тестирован"])
        p = posting("Python разработчик", divisions=["Инженер по тестированию"])
        d = PreScreen(prof).check(p)
        assert not d.keep
        assert "специализация" in d.reason

    def test_positive_list_required_when_set(self):
        prof = SearchProfile(positive=["Python", "Backend"])
        assert PreScreen(prof).check(posting("Python разработчик")).keep
        assert not PreScreen(prof).check(posting("Верстальщик")).keep

    def test_empty_positive_list_accepts_any_title(self):
        prof = SearchProfile(positive=[])
        assert PreScreen(prof).check(posting("Что угодно")).keep

    def test_qualification_below_minimum_is_dropped(self):
        prof = SearchProfile(min_qualification="middle")
        assert not PreScreen(prof).check(posting("Разработчик", qualification="Junior")).keep
        assert PreScreen(prof).check(posting("Разработчик", qualification="Middle")).keep
        assert PreScreen(prof).check(posting("Разработчик", qualification="Senior")).keep

    def test_blank_qualification_passes(self):
        """On Habr a missing grade is common and usually means mid-level."""
        prof = SearchProfile(min_qualification="middle")
        assert PreScreen(prof).check(posting("Разработчик", qualification="")).keep

    def test_remote_only_drops_onsite(self):
        prof = SearchProfile(remote_only=True)
        assert not PreScreen(prof).check(posting("Разработчик", remote=False)).keep
        assert PreScreen(prof).check(posting("Разработчик", remote=True)).keep

    def test_salary_below_floor_is_dropped(self):
        prof = SearchProfile(salary_min=250_000)
        low = posting("Разработчик", salary_from=100_000, salary_to=150_000,
                      salary_text="100 000 – 150 000 ₽")
        assert not PreScreen(prof).check(low).keep

    def test_salary_filter_ignores_unstated_salary(self):
        """No stated figure must never be a rejection — most postings hide it."""
        prof = SearchProfile(salary_min=250_000)
        assert PreScreen(prof).check(posting("Разработчик")).keep

    def test_run_splits_kept_and_dropped(self):
        prof = SearchProfile(positive=["Python"], negative=["word:QA"])
        kept, dropped = PreScreen(prof).run([
            posting("Python разработчик", id="1"),
            posting("QA Engineer", id="2"),
            posting("Дизайнер", id="3"),
        ])
        assert [p.title for p in kept] == ["Python разработчик"]
        assert len(dropped) == 2


class TestStarterProfile:
    def test_starter_rejects_the_vacancies_that_started_this(self):
        """The real QA/SDET titles the first scanner wrongly surfaced."""
        screen = PreScreen(starter_profile())
        for title in [
            "Инженер по тестированию (Python / API) / QA Engineer",
            "SDET / QA Automation Architect (Framework & Tooling)",
            "QA Engineer — Web, Backend, API, Linux",
        ]:
            assert not screen.check(posting(title)).keep, title

    def test_starter_keeps_real_backend_roles(self):
        screen = PreScreen(starter_profile())
        for title in ["Python разработчик", "Ведущий Python-разработчик",
                      "Backend Developer (Python)", "ML Platform Engineer"]:
            assert screen.check(posting(title, qualification="Middle")).keep, title

    def test_starter_catches_both_alphabets(self):
        """Habr mixes Russian and Latin in one title; a one-alphabet list leaks."""
        screen = PreScreen(starter_profile())
        for title in ["BI Analyst (Tableau, Python)", "AQA Engineer (Python)",
                      "Quality Assurance Engineer", "Системный аналитик Python"]:
            assert not screen.check(posting(title, qualification="Middle")).keep, title

    def test_starter_is_a_fresh_copy_each_time(self):
        a = starter_profile()
        a.queries.append("испорчено")
        assert "испорчено" not in starter_profile().queries
