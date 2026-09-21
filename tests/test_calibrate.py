"""Calibration: does the scoring still obey the rules EVALUATE states for itself?

The point of these tests is that the guard has teeth. A check that passes
everything is worse than no check — it certifies drift instead of catching it,
so every rule here gets a case that must fail it.

No network, no LLM.
"""

import json

import pytest

from core import calibrate


def report(score=4.5, verdict=None, requirements=None, red_flags=None) -> dict:
    return {
        "score": score,
        "verdict": verdict if verdict is not None else calibrate.verdict_for(score),
        "role_summary": "что это за работа",
        "requirements": requirements if requirements is not None else [
            {"text": "Python", "weight": "critical", "basis": "stated",
             "jd_signal": "Требуется Python", "match": "full",
             "evidence": "6 лет на Python"},
        ],
        "red_flags": red_flags or [],
    }


def row(title="Вакансия", **kw) -> dict:
    return {"id": "1", "title": title, "report": report(**kw)}


class TestVerdictRule:
    """The verdict follows from the score. That is arithmetic, not opinion."""

    @pytest.mark.parametrize(
        ("score", "expected"),
        [(5.0, "apply"), (4.0, "apply"), (3.5, "apply_with_caveats"),
         (3.0, "apply_with_caveats"), (2.9, "skip"), (1.0, "skip")],
    )
    def test_thresholds_match_the_prompt(self, score, expected):
        assert calibrate.verdict_for(score) == expected

    def test_a_verdict_that_contradicts_its_score_is_caught(self):
        r = calibrate.from_stored([row(score=2.0, verdict="apply")])
        assert not r.ok
        assert r.findings[0].kind == "вердикт не следует из балла"

    def test_a_flagged_downgrade_is_allowed(self):
        """4.0 with real red flags IS "with caveats" — the model was right."""
        r = calibrate.from_stored([
            row(score=4.0, verdict="apply_with_caveats", red_flags=["вилка не указана"]),
        ])
        assert r.ok

    def test_a_downgrade_without_a_stated_reason_is_not(self):
        """Downgrading on red flags is allowed; doing it silently is not."""
        r = calibrate.from_stored([
            row(score=4.0, verdict="apply_with_caveats", red_flags=[]),
        ])
        assert not r.ok

    def test_an_upgrade_is_never_allowed(self):
        """Flattery goes the other way: a 3.0 may not be sold as 'apply'."""
        r = calibrate.from_stored([
            row(score=3.0, verdict="apply", red_flags=["что-то"]),
        ])
        assert not r.ok


class TestScoreSanity:
    def test_score_off_the_scale_is_caught(self):
        assert not calibrate.from_stored([row(score=7.0, verdict="apply")]).ok

    def test_score_not_on_the_half_step_is_caught(self):
        r = calibrate.from_stored([row(score=4.3, verdict="apply")])
        assert any(f.kind == "балл не кратен 0.5" for f in r.findings)

    def test_a_missing_score_is_caught_and_does_not_crash(self):
        r = calibrate.from_stored([{"id": "1", "title": "t", "report": {"verdict": "apply"}}])
        assert not r.ok
        assert r.checked == 0


class TestImportanceBands:
    """Five bands, as in career-ops. The binary must/nice they replace is what
    let one unmet row sink a posting the candidate actually fits."""

    def test_an_unknown_band_is_caught(self):
        r = calibrate.from_stored([row(
            requirements=[{"text": "Python", "weight": "must", "match": "full",
                           "evidence": "есть"}],
        )])
        assert any("важность" in f.kind for f in r.findings)

    def test_a_guess_may_not_be_called_critical(self):
        """The gate: market intuition does not create obligations."""
        r = calibrate.from_stored([row(
            requirements=[{"text": "Опыт в финтехе", "weight": "critical",
                           "basis": "inferred", "match": "none", "evidence": "нет"}],
        )])
        assert any("догадка" in f.kind for f in r.findings)

    def test_a_guess_may_be_meaningful(self):
        r = calibrate.from_stored([row(
            requirements=[{"text": "Опыт в финтехе", "weight": "meaningful",
                           "basis": "inferred", "match": "none", "evidence": "нет"}],
        )])
        assert r.ok

    def test_stated_without_a_quote_is_caught(self):
        """"The posting says so" has to be backed by what the posting says."""
        r = calibrate.from_stored([row(
            requirements=[{"text": "Python", "weight": "critical", "basis": "stated",
                           "jd_signal": "", "match": "full", "evidence": "есть"}],
        )])
        assert any("цитаты" in f.kind for f in r.findings)


class TestCoreCoveredButScoredLow:
    """The regression this whole change exists for: a posting whose core the
    candidate covers, scored 2.5 because one row was unmet."""

    def _reqs(self, met: int, total: int = 5):
        return [
            {"text": f"требование {i}", "weight": "critical", "basis": "stated",
             "jd_signal": "цитата", "match": "full" if i < met else "none",
             "evidence": "есть" if i < met else "нет"}
            for i in range(total)
        ]

    def test_core_covered_with_a_low_score_is_caught(self):
        r = calibrate.from_stored([row(score=2.5, requirements=self._reqs(met=4))])
        assert any("ядро закрыто" in f.kind for f in r.findings)

    def test_core_covered_with_a_fair_score_passes(self):
        r = calibrate.from_stored([row(score=4.0, requirements=self._reqs(met=4))])
        assert r.ok

    def test_a_genuinely_poor_match_may_score_low(self):
        """A low score is not itself a defect — only a low score on a good fit."""
        r = calibrate.from_stored([row(score=2.0, requirements=self._reqs(met=1))])
        assert r.ok

    def test_a_short_requirement_list_is_not_judged(self):
        """Three rows say too little about whether the core is covered."""
        r = calibrate.from_stored([row(score=2.0, requirements=self._reqs(met=2, total=3))])
        assert r.ok


class TestEvidence:
    def test_a_full_match_without_evidence_is_caught(self):
        """Evidence is the product. "Full match, no reason" is the failure
        week 0 was watching for."""
        r = calibrate.from_stored([row(
            requirements=[{"text": "Python", "weight": "critical", "basis": "stated",
                           "jd_signal": "цитата", "match": "full", "evidence": "   "}],
        )])
        assert any(f.kind == "совпадение без обоснования" for f in r.findings)


class TestSpread:
    def test_a_flat_run_is_caught(self):
        """"Everything is a 4" was week 0's stop-criterion. Here it is a number."""
        rows = [{"id": str(i), "title": f"v{i}", "report": report(score=4.0)} for i in range(5)]
        r = calibrate.from_stored(rows)
        assert any("различаются" in f.kind for f in r.findings)
        assert r.spread < calibrate.MIN_SPREAD

    def test_a_varied_run_passes(self):
        scores = [1.5, 2.5, 3.5, 4.5, 5.0]
        rows = [{"id": str(i), "title": f"v{i}", "report": report(score=s)}
                for i, s in enumerate(scores)]
        r = calibrate.from_stored(rows)
        assert r.ok
        assert r.spread >= calibrate.MIN_SPREAD

    def test_two_reports_are_not_judged_for_spread(self):
        """Two points say nothing about spread; flagging them would be noise."""
        rows = [{"id": str(i), "title": "v", "report": report(score=4.0)} for i in range(2)]
        assert calibrate.from_stored(rows).ok


class TestStored:
    def test_rows_without_a_report_are_skipped_not_failed(self):
        """Most found vacancies were never evaluated. That is normal."""
        r = calibrate.from_stored([{"id": "1", "title": "не оценена"}])
        assert r.checked == 0
        assert r.ok


class TestGolden:
    def test_agreement_with_the_label_is_counted(self):
        cases = [{"id": "a", "label": {"score": 4.0, "verdict": "apply"},
                  "report": report(score=4.0)}]
        r = calibrate.from_golden(cases)
        assert r.labeled == 1
        assert r.agreed == 1
        assert r.ok

    def test_drift_beyond_tolerance_is_caught(self):
        cases = [{"id": "a", "label": {"score": 4.5}, "report": report(score=2.0)}]
        r = calibrate.from_golden(cases)
        assert not r.ok
        assert r.agreed == 0

    def test_drift_within_tolerance_is_accepted(self):
        cases = [{"id": "a", "label": {"score": 4.5}, "report": report(score=4.0)}]
        assert calibrate.from_golden(cases).agreed == 1

    def test_a_label_without_a_score_is_not_counted(self):
        cases = [{"id": "a", "label": {}, "report": report(score=4.0)}]
        assert calibrate.from_golden(cases).labeled == 0

    def test_a_missing_golden_dir_is_not_an_error(self, tmp_path):
        assert calibrate.load_golden(tmp_path / "nope") == []

    def test_a_broken_case_file_names_itself(self, tmp_path):
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="bad.json"):
            calibrate.load_golden(tmp_path)


class TestShippedGoldenSet:
    """The set in data/golden is real frozen output, not a fixture."""

    def test_the_shipped_set_still_passes(self):
        cases = calibrate.load_golden()
        if not cases:
            pytest.skip("золотой набор не собран")
        r = calibrate.from_golden(cases)
        assert r.ok, [f.as_dict() for f in r.findings]

    def test_the_shipped_set_has_real_spread(self):
        """A golden set of near-identical scores cannot detect flattery."""
        cases = calibrate.load_golden()
        if len(cases) < 3:
            pytest.skip("мало случаев")
        assert calibrate.from_golden(cases).spread >= calibrate.MIN_SPREAD

    def test_every_case_carries_provenance(self):
        """A label whose origin is unknown cannot be trusted as a reference."""
        for case in calibrate.load_golden():
            assert case.get("label", {}).get("provenance"), case.get("id")

    def test_cases_are_valid_json_with_an_id(self):
        for case in calibrate.load_golden():
            assert case.get("id")
            assert json.dumps(case)
