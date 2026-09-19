"""The found-vacancy store decides what survives a page reload.

The expensive mistake it must prevent: losing a score the user already paid for.
No network, no LLM.
"""

from probe import found


def posting(vid: str, **kw) -> dict:
    base = {
        "id": vid,
        "url": f"https://career.habr.com/vacancies/{vid}",
        "title": f"Python developer {vid}",
        "company": "Acme",
        "qualification": "Middle",
        "remote": True,
        "salary_text": "от 200 000 ₽",
    }
    return {**base, **kw}


class TestMerge:
    def test_first_scan_stores_what_it_found(self, tmp_path):
        p = tmp_path / "found.json"
        rows = found.merge([posting("1"), posting("2")], p)
        assert [r["id"] for r in rows] == ["2", "1"]

    def test_second_scan_accumulates_instead_of_replacing(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        rows = found.merge([posting("2")], p)
        assert {r["id"] for r in rows} == {"1", "2"}

    def test_repeat_sighting_does_not_duplicate(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        rows = found.merge([posting("1")], p)
        assert len(rows) == 1

    def test_rescan_refreshes_the_listing_fields(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1", salary_text="от 200 000 ₽")], p)
        rows = found.merge([posting("1", salary_text="от 250 000 ₽")], p)
        assert rows[0]["salary_text"] == "от 250 000 ₽"

    def test_postings_without_an_id_are_ignored(self, tmp_path):
        p = tmp_path / "found.json"
        assert found.merge([{"title": "no id"}], p) == []


class TestScorePersistence:
    def test_score_survives_a_later_scan(self, tmp_path):
        """The whole point: a rescan must not make the user pay twice."""
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_score("1", {"score": 4.5, "verdict": "apply"}, path=p)

        rows = found.merge([posting("1"), posting("2")], p)
        scored = next(r for r in rows if r["id"] == "1")
        assert scored["score"] == 4.5
        assert scored["verdict"] == "apply"

    def test_score_is_written_to_disk(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_score("1", {"score": 3.0, "verdict": "apply_with_caveats"}, path=p)
        assert found.load(p)[0]["score"] == 3.0

    def test_full_report_is_kept_for_redisplay(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        report = {"score": 4.0, "verdict": "apply", "role_summary": "подходит"}
        found.set_score("1", report, path=p)
        assert found.find("1", p)["report"]["role_summary"] == "подходит"

    def test_cost_is_recorded_with_the_score(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_score("1", {"score": 4.0}, usage={"total_rub": 0.1712}, path=p)
        assert found.find("1", p)["rub"] == 0.1712

    def test_scoring_an_unknown_id_is_a_noop(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        rows = found.set_score("999", {"score": 5.0}, path=p)
        assert all("score" not in r for r in rows)


class TestLoad:
    def test_missing_file_is_an_empty_list(self, tmp_path):
        assert found.load(tmp_path / "nope.json") == []

    def test_corrupt_file_does_not_break_the_page(self, tmp_path):
        p = tmp_path / "found.json"
        p.write_text("{not json", encoding="utf-8")
        assert found.load(p) == []

    def test_clear_forgets_everything(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.clear(p)
        assert found.load(p) == []


class TestArtifacts:
    """Letter, tailored resume and interview prep live beside the report.

    They are bought separately and accumulate: adding the letter later must not
    erase the interview prep, nor forget what the evaluation already cost.
    """

    def test_stored_artifact_survives_reload(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_artifacts("1", {"cover_letter": {"letter": "текст"}}, path=p)
        assert found.find("1", p)["artifacts"]["cover_letter"]["letter"] == "текст"

    def test_second_run_adds_without_dropping_the_first(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_artifacts("1", {"cover_letter": {"letter": "a"}}, path=p)
        found.set_artifacts("1", {"tailored_resume": {"changes": []}}, path=p)
        stored = found.find("1", p)["artifacts"]
        assert set(stored) == {"cover_letter", "tailored_resume"}

    def test_cost_accumulates_across_runs(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_score("1", {"score": 4.0, "verdict": "apply"}, {"total_rub": 0.17}, p)
        found.set_artifacts("1", {"cover_letter": {"letter": "L"}}, {"total_rub": 0.30}, p)
        assert found.find("1", p)["rub"] == 0.47

    def test_empty_result_does_not_overwrite_a_stored_artifact(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_artifacts("1", {"cover_letter": {"letter": "a"}}, path=p)
        found.set_artifacts("1", {"cover_letter": None}, path=p)
        assert found.find("1", p)["artifacts"]["cover_letter"] == {"letter": "a"}

    def test_nothing_usable_does_not_label_the_row_as_ready(self, tmp_path):
        """An empty answer is a failure, not a delivered artifact."""
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_artifacts("1", {"cover_letter": None}, path=p)
        assert "artifacts" not in found.find("1", p)
        assert found.spend(p)["with_artifacts"] == 0

    def test_unknown_vacancy_is_a_no_op(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_artifacts("404", {"cover_letter": {"letter": "L"}}, path=p)
        assert "artifacts" not in found.find("1", p)


class TestStatus:
    """The tracker. The user moves a vacancy; nothing moves it on its own."""

    def test_status_is_stored_and_logged(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_status("1", "applied", path=p)
        row = found.find("1", p)
        assert row["status"] == "applied"
        assert row["status_log"][0]["to"] == "applied"
        assert row["status_log"][0]["from"] is None

    def test_transitions_append_rather_than_replace(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_status("1", "applied", path=p)
        found.set_status("1", "interview", path=p)
        row = found.find("1", p)
        assert row["status"] == "interview"
        assert [e["to"] for e in row["status_log"]] == ["applied", "interview"]
        assert row["status_log"][1]["from"] == "applied"

    def test_unknown_status_is_refused(self, tmp_path):
        import pytest

        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        with pytest.raises(ValueError):
            found.set_status("1", "нанят", path=p)
        assert "status" not in found.find("1", p)

    def test_repeating_a_status_does_not_pad_the_log(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_status("1", "applied", path=p)
        found.set_status("1", "applied", path=p)
        assert len(found.find("1", p)["status_log"]) == 1

    def test_a_rescan_keeps_the_status(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_status("1", "applied", path=p)
        found.merge([posting("1", salary_text="от 250 000 ₽")], p)
        row = found.find("1", p)
        assert row["status"] == "applied"
        assert row["salary_text"] == "от 250 000 ₽"


class TestClearingBothStores:
    """Clearing the list must clear the history with it.

    The bug this pins: "Очистить" emptied found.json but left scan-history.tsv,
    so the next scan called every survivor "already shown" and put it into an
    empty list. The vacancies became invisible with no way back.
    """

    def test_a_rescan_after_clearing_shows_vacancies_again(self, tmp_path):
        from probe import history

        fj, hf = tmp_path / "found.json", tmp_path / "h.tsv"
        rows = [{"id": "1", "url": "u", "title": "t", "company": "c"}]

        found.merge(rows, fj)
        history.append([{**rows[0], "status": "found"}], hf)

        found.clear(fj)
        history.clear(path=hf)

        fresh, seen = history.split_seen_rows(rows, hf)
        assert [r["id"] for r in fresh] == ["1"]
        assert seen == []

    def test_clearing_only_the_list_strands_the_vacancy(self, tmp_path):
        """The old behaviour, kept as a test so it cannot come back."""
        from probe import history

        fj, hf = tmp_path / "found.json", tmp_path / "h.tsv"
        rows = [{"id": "1", "url": "u", "title": "t", "company": "c"}]
        found.merge(rows, fj)
        history.append([{**rows[0], "status": "found"}], hf)

        found.clear(fj)  # history deliberately left behind

        fresh, seen = history.split_seen_rows(rows, hf)
        assert fresh == []
        assert len(seen) == 1
        assert found.load(fj) == []

    def test_clearing_history_twice_is_not_an_error(self, tmp_path):
        from probe import history

        hf = tmp_path / "h.tsv"
        history.clear(path=hf)
        history.clear(path=hf)
        assert history.load(hf) == {}


class TestShortlist:
    """The shortlist is the boundary between searching and working.

    Search throws most things away; the shortlist is what the user chose to act
    on, and it carries reports and materials they paid for.
    """

    def test_saving_marks_the_row(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_saved("1", True, p)
        assert found.find("1", p)["saved"] is True
        assert found.find("1", p)["saved_at"]

    def test_unsaving_clears_the_mark(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_saved("1", True, p)
        found.set_saved("1", False, p)
        assert found.find("1", p)["saved"] is False
        assert found.saved(p) == []

    def test_saved_lists_only_the_shortlist(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1"), posting("2"), posting("3")], p)
        found.set_saved("2", True, p)
        assert [r["id"] for r in found.saved(p)] == ["2"]

    def test_clearing_the_search_keeps_the_shortlist(self, tmp_path):
        """"Очистить" means "this batch was not useful" — not "drop what I kept"."""
        p = tmp_path / "found.json"
        found.merge([posting("1"), posting("2")], p)
        found.set_saved("1", True, p)
        found.set_score("1", {"score": 4.5, "verdict": "apply"}, {"total_rub": 0.17}, p)

        found.clear(p)

        rows = found.load(p)
        assert [r["id"] for r in rows] == ["1"]
        assert rows[0]["score"] == 4.5, "оплаченный отчёт должен пережить очистку"

    def test_clearing_with_nothing_saved_empties_the_list(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.clear(p)
        assert found.load(p) == []

    def test_a_rescan_does_not_unsave(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_saved("1", True, p)
        found.merge([posting("1", salary_text="от 300 000 ₽")], p)
        assert found.find("1", p)["saved"] is True

    def test_saving_an_unknown_vacancy_is_a_no_op(self, tmp_path):
        p = tmp_path / "found.json"
        found.merge([posting("1")], p)
        found.set_saved("404", True, p)
        assert found.saved(p) == []

    def test_history_keeps_the_shortlist_after_a_clear(self, tmp_path):
        """A saved vacancy forgotten by history comes back as a fresh find —
        the user would be looking at a duplicate of what they already kept."""
        from probe import history

        hf = tmp_path / "h.tsv"
        rows = [{"id": "1", "url": "u1", "title": "kept", "company": "c"},
                {"id": "2", "url": "u2", "title": "dropped", "company": "c"}]
        history.append(rows, hf)

        history.clear(keep_ids=["1"], path=hf)

        known = history.load(hf)
        assert set(known) == {"1"}
