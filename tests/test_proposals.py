"""The confirmation layer decides what gets written, so its edges are pinned here.

No network, no LLM.
"""

import json

from probe import proposals
from probe.proposals import ACCEPTED, PENDING, REJECTED, Proposal


def make(current=None, proposed=None, **kw) -> Proposal:
    return Proposal(
        kind="search_profile",
        current=current if current is not None else {"queries": ["Python"], "negative": []},
        proposed=proposed if proposed is not None else {"queries": ["Python", "FastAPI"]},
        **kw,
    )


class TestDiff:
    def test_added_and_removed_are_separated(self):
        d = proposals.diff({"queries": ["Python", "Go"]}, {"queries": ["Python", "Rust"]})
        assert len(d) == 1
        assert d[0].added == ["Rust"]
        assert d[0].removed == ["Go"]

    def test_untouched_field_is_not_reported(self):
        d = proposals.diff({"queries": ["Python"], "negative": ["QA"]},
                           {"queries": ["Python"]})
        assert d == []

    def test_field_absent_from_proposal_is_left_alone(self):
        """A proposal that says nothing about stop-words must not clear them."""
        d = proposals.diff({"negative": ["QA", "Тестировщик"]}, {"queries": ["Python"]})
        assert all(x.field != "negative" for x in d)

    def test_scalar_field_diffs(self):
        d = proposals.diff({"min_qualification": "junior"}, {"min_qualification": "middle"})
        assert d[0].added == ["middle"]
        assert d[0].removed == ["junior"]

    def test_diff_is_readable(self):
        d = proposals.diff({"queries": ["Go"]}, {"queries": ["Python"]})
        text = d[0].describe()
        assert "Python" in text and "Go" in text


class TestLifecycle:
    def test_new_proposal_is_pending(self):
        p = make()
        assert p.status == PENDING
        assert p.created_at
        assert not p.decided_at

    def test_creating_a_proposal_changes_nothing(self):
        """The whole point: generating is not writing."""
        current = {"queries": ["Python"]}
        make(current=current)
        assert current == {"queries": ["Python"]}

    def test_accept_returns_the_merged_value(self):
        p = make()
        result = p.accept()
        assert result["queries"] == ["Python", "FastAPI"]
        assert p.status == ACCEPTED
        assert p.decided_at

    def test_accept_keeps_fields_the_proposal_did_not_mention(self):
        p = make(current={"queries": ["Python"], "negative": ["QA"]},
                 proposed={"queries": ["Go"]})
        assert p.accept()["negative"] == ["QA"]

    def test_reject_returns_the_original_untouched(self):
        p = make()
        result = p.reject()
        assert result["queries"] == ["Python"]
        assert p.status == REJECTED

    def test_accept_does_not_mutate_current(self):
        current = {"queries": ["Python"]}
        p = make(current=current)
        p.accept()
        assert current == {"queries": ["Python"]}


class TestGuardListsAreUnioned:
    """A proposal must never shrink the protection the template ships with.

    Regression from 2026-09-14: accepting a generated profile replaced the
    template's stop-words, and Android/iOS/React vacancies appeared in the
    results because the words guarding against them were gone.
    """

    def test_template_stop_words_survive_acceptance(self):
        p = make(current={"negative": ["Frontend", "Android", "iOS"]},
                 proposed={"negative": ["word:QA", "аналитик"]})
        result = p.accept()["negative"]
        assert "Frontend" in result and "Android" in result and "iOS" in result

    def test_proposed_stop_words_are_added(self):
        p = make(current={"negative": ["Frontend"]},
                 proposed={"negative": ["word:QA"]})
        assert p.accept()["negative"] == ["Frontend", "word:QA"]

    def test_stop_words_are_not_duplicated(self):
        p = make(current={"negative": ["Frontend", "word:QA"]},
                 proposed={"negative": ["word:qa", "Android"]})
        result = p.accept()["negative"]
        assert result.count("word:QA") == 1
        assert "word:qa" not in result
        assert result == ["Frontend", "word:QA", "Android"]

    def test_negative_divisions_are_unioned_too(self):
        p = make(current={"divisions_negative": ["Фронтенд", "Мобильн"]},
                 proposed={"divisions_negative": ["Тестирование и QA"]})
        result = p.accept()["divisions_negative"]
        assert result == ["Фронтенд", "Мобильн", "Тестирование и QA"]

    def test_queries_are_replaced_not_unioned(self):
        """The model read the CV, so its queries win outright."""
        p = make(current={"queries": ["Python", "Golang"]},
                 proposed={"queries": ["FastAPI разработчик"]})
        assert p.accept()["queries"] == ["FastAPI разработчик"]

    def test_positive_keywords_are_replaced(self):
        p = make(current={"positive": ["Python", "Backend"]},
                 proposed={"positive": ["Golang"]})
        assert p.accept()["positive"] == ["Golang"]

    def test_min_qualification_is_replaced(self):
        p = make(current={"min_qualification": "junior"},
                 proposed={"min_qualification": "senior"})
        assert p.accept()["min_qualification"] == "senior"

    def test_diff_does_not_promise_a_removal_that_will_not_happen(self):
        """The table must show what accepting really does."""
        d = proposals.diff({"negative": ["Frontend", "Android"]},
                           {"negative": ["word:QA"]})
        assert len(d) == 1
        assert d[0].added == ["word:QA"]
        assert d[0].removed == []

    def test_diff_still_reports_removals_for_replaced_fields(self):
        d = proposals.diff({"queries": ["Python"]}, {"queries": ["Golang"]})
        assert d[0].removed == ["Python"]


class TestNoop:
    def test_identical_proposal_is_a_noop(self):
        p = make(current={"queries": ["Python"]}, proposed={"queries": ["Python"]})
        assert p.is_noop
        assert "Изменений нет" in p.describe()

    def test_real_change_is_not_a_noop(self):
        assert not make().is_noop


class TestPersistence:
    def test_round_trip_preserves_the_decision(self, tmp_path):
        p = make(rationale="по резюме кандидата")
        p.accept()
        path = proposals.save(p, tmp_path / "proposal.json")

        back = proposals.load(path)
        assert back.status == ACCEPTED
        assert back.rationale == "по резюме кандидата"
        assert back.proposed == p.proposed

    def test_missing_file_loads_as_none(self, tmp_path):
        assert proposals.load(tmp_path / "nope.json") is None

    def test_saved_file_shows_the_diff_without_reparsing(self, tmp_path):
        """A human opening the file should see what would change."""
        path = proposals.save(make(), tmp_path / "p.json")
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["changes"][0]["added"] == ["FastAPI"]
        assert saved["status"] == PENDING
