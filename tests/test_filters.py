"""Filters decide what the user pays for, so their edges are pinned down here.

No network, no LLM — pure functions over made-up postings.
"""

from core.defaults import starter_profile
from core.filters import PreScreen, SearchProfile, _compile
from core.sources.habr_api import Posting


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

    def test_stem_prefix_demands_the_start_of_a_word(self):
        """Between the loose default and the strict word:. A plain keyword is
        loose on both sides and usually only one side was meant."""
        p = _compile("stem:Rust")
        assert p.search("Rust Engineer")
        assert p.search("Rust Engineering")  # suffix still allowed
        assert not p.search("Zero Trust Engineer")  # mid-word is not

    def test_stem_differs_from_word_on_the_suffix(self):
        assert _compile("stem:Крипто").search("Криптовалюта")
        assert not _compile("word:Крипто").search("Криптовалюта")

    def test_and_group_requires_every_term_in_any_order(self):
        """One entry replaces a matrix of spellings. The live miss on
        2026-09-15: "Ведущий разработчик Java" matched neither
        "Java Developer" nor "Java-разработчик" — the words were reordered."""
        p = _compile("разработчик + java")
        assert p.search("Ведущий разработчик Java (Микросервисы)")
        assert p.search("Java-разработчик")
        assert not p.search("Python разработчик")
        assert not p.search("Java Engineer")

    def test_and_group_keeps_per_term_anchoring(self):
        """Each term compiles by the normal rules, so a short one stays anchored."""
        p = _compile("word:Go + Developer")
        assert p.search("Go Developer")
        assert not p.search("Google Developer")

    def test_bare_plus_is_not_a_separator(self):
        """Splitting on a bare "+" would turn "C++" into "c", which matches
        almost every title — a silent flood in place of a silent miss."""
        p = _compile("C++")
        assert p.search("С++ Developer")
        assert not p.search("Python разработчик")

    def test_latin_lookalike_inside_a_cyrillic_word_still_matches(self):
        """A real posting on 2026-09-15 read "Rust paзработчик" — the "pa" is
        Latin. Without folding, every keyword misses it."""
        assert _compile("Rust разработчик").search("Rust paзработчик")
        assert _compile("Разработчик").search("paзработчик")

    def test_folding_leaves_ordinary_latin_words_alone(self):
        """Only lower-case twins may be folded. Mapping "b"/"h"/"k"/"m"/"t" as
        well mangles "Backend" into "ьаскеnd" and breaks unrelated keywords."""
        assert _compile("Backend").search("Senior Backend Developer")
        assert _compile("Golang").search("Golang-разработчик")
        assert _compile("PHP").search("Backend-разработчик php (yii2)")


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

    def test_generic_title_needs_a_target_division(self):
        """"Разработчик" alone proves nothing — it fits every stack there is.
        On 2026-09-15 it waved through Hadoop, BI, BPMN, C++ and Rust."""
        prof = SearchProfile(
            positive=["Python", "Разработчик"],
            divisions_positive=["Бэкенд разработчик"],
        )
        screen = PreScreen(prof)
        assert not screen.check(posting("Разработчик Hadoop", divisions=["Инженер по данным"])).keep
        assert screen.check(posting("Разработчик Hadoop", divisions=["Бэкенд разработчик"])).keep

    def test_specific_keyword_passes_any_division(self):
        prof = SearchProfile(
            positive=["Python", "Разработчик"],
            divisions_positive=["Бэкенд разработчик"],
        )
        p = posting("Python разработчик", divisions=["Инженер по данным"])
        assert PreScreen(prof).check(p).keep

    def test_missing_division_does_not_fail_a_generic_title(self):
        """Judging an absence would drop every posting Habr files under nothing."""
        prof = SearchProfile(
            positive=["Python", "Разработчик"],
            divisions_positive=["Бэкенд разработчик"],
        )
        assert PreScreen(prof).check(posting("Разработчик")).keep

    def test_no_target_divisions_means_the_rule_is_off(self):
        """A profile that never listed target divisions keeps the old behaviour."""
        prof = SearchProfile(positive=["Разработчик"])
        assert PreScreen(prof).check(posting("Разработчик Hadoop", divisions=["Что угодно"])).keep

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

    def test_accredited_only_drops_only_an_explicit_no(self):
        """Telegram and geekjob never state it; silence must not empty them."""
        prof = SearchProfile(accredited_only=True)
        no = PreScreen(prof).check(posting("Разработчик", accredited=False))
        assert not no.keep
        assert "аккредитац" in no.reason
        assert PreScreen(prof).check(posting("Разработчик", accredited=True)).keep
        assert PreScreen(prof).check(posting("Разработчик", accredited=None)).keep

    def test_accreditation_is_ignored_unless_asked(self):
        prof = SearchProfile()
        assert PreScreen(prof).check(posting("Разработчик", accredited=False)).keep

    def test_salary_below_floor_is_dropped(self):
        prof = SearchProfile(salary_min=250_000)
        low = posting("Разработчик", salary_from=100_000, salary_to=150_000,
                      salary_text="100 000 – 150 000 ₽")
        assert not PreScreen(prof).check(low).keep

    def test_salary_filter_ignores_unstated_salary(self):
        """No stated figure must never be a rejection — most postings hide it."""
        prof = SearchProfile(salary_min=250_000)
        assert PreScreen(prof).check(posting("Разработчик")).keep

    def test_no_floor_keeps_every_salary(self):
        """The web's "искать без зарплаты" box clears salary_min, and that has to
        switch off both screens: Habr's own `salary` parameter and this one.
        Leaving the local check armed would hand the box back its own result."""
        prof = SearchProfile(salary_min=None)
        low = posting("Разработчик", salary_from=30_000, salary_to=50_000,
                      salary_text="30 000 – 50 000 ₽")
        assert PreScreen(prof).check(low).keep
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


class TestAccreditationSource:
    def test_habr_listing_states_it_from_the_company(self):
        from core.sources.habr_api import _posting

        yes = _posting({"id": 1, "title": "x", "company": {"title": "VK", "accredited": True}})
        no = _posting({"id": 2, "title": "x", "company": {"title": "Яндекс", "accredited": False}})
        silent = _posting({"id": 3, "title": "x", "company": {"title": "Нечто"}})
        assert (yes.accredited, no.accredited, silent.accredited) == (True, False, None)

    def test_scan_form_overrides_the_profile_for_one_run(self):
        from app.services.scan import ScanRequest, with_overrides

        stored = {"queries": ["python"]}
        merged = with_overrides(stored, ScanRequest(source="all", accredited_only=True))
        assert SearchProfile.from_dict(merged).accredited_only is True
        assert "accredited_only" not in stored
        untouched = with_overrides(stored, ScanRequest(source="all"))
        assert "accredited_only" not in untouched
