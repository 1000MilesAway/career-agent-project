"""Snapshot tests: recorded model answers, replayed without a key.

Borrowed from DeepSeek Harness, which replays recorded sessions in CI and keeps
live-API tests in a separate suite that skips itself without a key. The point
here is the same: prompts will change often (the roadmap has "промпты по
сферам"), and every change must be checkable for free and offline.

The fixtures are synthetic — see tests/fixtures/README.md. Real answers live in
out/, which is gitignored because it holds personal data.

What this pins down is the *shape* the prompts promise: if a prompt stops
asking for a field the product reads, that shows up here instead of in
production.
"""

import json
from pathlib import Path

import pytest

from probe import prompts

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class TestFixturesAreValid:
    @pytest.mark.parametrize(
        "name",
        [
            "parse_resume",
            "evaluate_apply",
            "evaluate_skip",
            "tailor_resume",
            "cover_letter",
            "interview_prep",
        ],
    )
    def test_fixture_parses_as_json_object(self, name):
        """This is what llm.complete_json does to a real answer."""
        assert isinstance(load(name), dict)


class TestParseResume:
    def test_profile_has_the_sections_the_product_reads(self):
        p = load("parse_resume")
        for key in ("name", "target_role", "years_experience", "field",
                    "experience", "skills", "education", "languages", "preferences"):
            assert key in p, key

    def test_experience_entries_are_complete(self):
        for job in load("parse_resume")["experience"]:
            assert job["company"] and job["role"] and job["period"]
            assert isinstance(job["achievements"], list)

    def test_prompt_still_asks_for_every_field_the_fixture_has(self):
        """If a field is dropped from the prompt, the fixture goes stale."""
        for key in load("parse_resume"):
            assert f'"{key}"' in prompts.PARSE_RESUME, key


class TestEvaluate:
    @pytest.mark.parametrize("name", ["evaluate_apply", "evaluate_skip"])
    def test_report_has_every_block(self, name):
        r = load(name)
        for key in ("score", "verdict", "role_summary", "requirements",
                    "red_flags", "salary", "questions_to_employer", "angle"):
            assert key in r, key

    @pytest.mark.parametrize("name", ["evaluate_apply", "evaluate_skip"])
    def test_score_is_on_the_half_point_scale(self, name):
        score = load(name)["score"]
        assert 1.0 <= score <= 5.0
        assert (score * 2) % 1 == 0, "шаг шкалы — 0.5"

    @pytest.mark.parametrize(
        "name,verdict", [("evaluate_apply", "apply"), ("evaluate_skip", "skip")]
    )
    def test_verdict_follows_the_threshold_rule(self, name, verdict):
        """The prompt fixes the thresholds: apply >= 4.0, skip < 3.0."""
        r = load(name)
        assert r["verdict"] == verdict
        if r["verdict"] == "apply":
            assert r["score"] >= 4.0
        elif r["verdict"] == "skip":
            assert r["score"] < 3.0

    @pytest.mark.parametrize("name", ["evaluate_apply", "evaluate_skip"])
    def test_requirements_use_the_declared_vocabulary(self, name):
        for req in load(name)["requirements"]:
            assert req["weight"] in (
                "critical", "high", "meaningful", "preferred", "low_signal")
            assert req["basis"] in ("stated", "structural", "inferred")
            assert req["match"] in ("full", "partial", "none")
            assert req["evidence"], "evidence обязателен: балл должен быть обоснован"

    @pytest.mark.parametrize("name", ["evaluate_apply", "evaluate_skip"])
    def test_a_stated_requirement_quotes_the_posting(self, name):
        """"The posting says so" is a claim about the posting, so it is quoted."""
        for req in load(name)["requirements"]:
            if req["basis"] == "stated":
                assert req["jd_signal"], req["text"]

    @pytest.mark.parametrize("name", ["evaluate_apply", "evaluate_skip"])
    def test_a_guess_is_never_binding(self, name):
        """The gate: market intuition does not create obligations."""
        for req in load(name)["requirements"]:
            if req["basis"] == "inferred":
                assert req["weight"] not in ("critical", "high"), req["text"]

    def test_an_unmet_requirement_does_not_by_itself_cap_the_score(self):
        """The rule this replaces produced a 2.5 on a posting career-ops
        scored 4.4: one unmet row out of twelve, core covered. A gap informs
        the judgement; it does not set a ceiling."""
        r = load("evaluate_apply")
        unmet = [q for q in r["requirements"] if q["match"] != "full"]
        assert unmet, "фикстура должна содержать незакрытое требование"
        assert r["score"] >= 4.0

    def test_a_gap_carries_advice(self):
        """A partial or missing match without advice is a useless report line."""
        for name in ("evaluate_apply", "evaluate_skip"):
            for req in load(name)["requirements"]:
                if req["match"] in ("partial", "none"):
                    assert req["gap_advice"], f"{name}: {req['text']}"


class TestTailorResume:
    def test_changes_are_listed_for_the_user(self):
        """The whole point: the user must see what was rewritten."""
        t = load("tailor_resume")
        assert t["changes"]
        assert all(isinstance(c, str) and c for c in t["changes"])

    def test_no_invented_skills(self):
        """The hard rule in the prompt: nothing that is not in the profile."""
        profile_skills = {s.lower() for s in load("parse_resume")["skills"]}
        tailored = {s.lower() for s in load("tailor_resume")["skills"]}
        assert tailored <= profile_skills, tailored - profile_skills

    def test_ats_keywords_are_backed_by_the_profile(self):
        profile_skills = {s.lower() for s in load("parse_resume")["skills"]}
        for kw in load("tailor_resume")["ats_keywords"]:
            assert kw.lower() in profile_skills, kw


class TestCoverLetter:
    def test_three_angles_then_one_letter(self):
        c = load("cover_letter")
        assert len(c["angles"]) == 3
        assert c["letter"].strip()

    def test_letter_fits_the_length_limit(self):
        assert len(load("cover_letter")["letter"]) <= 1200

    def test_letter_avoids_the_banned_cliches(self):
        """The prompt names these outright."""
        letter = load("cover_letter")["letter"].lower()
        for cliche in ("с большим интересом", "динамично развивающ",
                       "командный игрок", "хотел бы выразить"):
            assert cliche not in letter, cliche


class TestInterviewPrep:
    def test_question_count_is_within_the_asked_range(self):
        assert 8 <= len(load("interview_prep")["questions"]) <= 12

    def test_every_question_is_justified_and_answerable(self):
        for q in load("interview_prep")["questions"]:
            assert q["question"] and q["why"] and q["answer_hint"]

    def test_star_stories_are_complete(self):
        for s in load("interview_prep")["star_stories"]:
            for part in ("title", "situation", "task", "action", "result"):
                assert s[part], part

    def test_star_stories_reuse_real_achievements(self):
        """STAR stories must come from the profile, not be invented."""
        numbers = ["120", "800", "40", "6"]
        joined = json.dumps(load("interview_prep")["star_stories"], ensure_ascii=False)
        assert any(n in joined for n in numbers), "истории должны опираться на цифры из профиля"
